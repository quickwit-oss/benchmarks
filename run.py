#!/usr/bin/env python3

import argparse
import configparser
import datetime
import enum
import fnmatch
import getpass
import json
import logging
import os
import platform
import pprint
import random
import re
import statistics
import subprocess
import sys
import time
import webbrowser
from abc import ABC, abstractmethod
from dataclasses import dataclass
from glob import glob
from typing import Any

import dateutil.parser as dateutil_parser
import docker
import prometheus_client
import prometheus_client.parser as prometheus_parser
import psutil
import requests
import yaml

logger = logging.getLogger(__name__)

WARMUP_ITER = 1
# Any failed query whose response contains one of those strings will be retried.
RETRY_ON_FAILED_RESPONSE_SUBSTR = [
    "there are no available searcher nodes in the pool"
]
# File where the JWT token will be cached across invocation of this
# tool.
JWT_TOKEN_FILENAME = "~/.jwt_token_benchmark_service.txt"

# Path to the optional runner config file. If it exists, it should be
# an ini config file with sections:
# - a 'paths' section mapping path placeholder names (e.g. 'qwdata')
#   to the actual data path to use. Used for resolving placeholders in
#   --engine-data-dir.
# - an 'engine_env' section with env variables to pass to the engine
#   at startup.
RUNNER_CONFIG_FILENAME = "~/.qw_benchmarks_runner.txt"

AUTODETECT_GCP_INSTANCE_PLACEHOLDER = '{autodetect_gcp}'


class BenchType(enum.StrEnum):
    INDEXING = "indexing"
    SEARCH = "search"


def read_runner_config(runner_config_path: str):
    parser = configparser.ConfigParser()
    if not parser.read(os.path.expanduser(runner_config_path)):
        raise ValueError(
            f"Runner config ({runner_config_path}) could not be opened.")
    return parser


def resolve_instance(instance_or_placeholder: str | None) -> str | None:
    if instance_or_placeholder == AUTODETECT_GCP_INSTANCE_PLACEHOLDER:
        try:
            return requests.get(
                "http://metadata.google.internal/computeMetadata/v1/instance/machine-type",
                headers={"Metadata-Flavor": "Google"}).text.split('/')[-1]
        except requests.exceptions.RequestException as ex:
            logging.info("Could not get GCP machine type: %s", ex)
            return "GCP_UNKNOWN"
    return instance_or_placeholder


def resolve_engine_data_dir(engine: str, data_dir: str | None, runner_config_path: str) -> str | None:
    """Returns the data dir to use for an engine.

    Args:
      engine: name of the engine, e.g. quickwit.
      data_dir: Optional data directory to use for the engine. It can
        be a placholder, e.g. '{qwdata}' that will then be resolved
        using the config.
      runner_config_path: Path to an ini config file containing a 'paths'
        section mapping placeholder names (e.g. 'qwdata') to the
        actual data path to use.

    Returns:
      The data path to use for the engine.
    """
    if not data_dir:
        return os.path.join(os.getcwd(), "engines", engine, "data")
    if data_dir[0] != '{' or data_dir[-1] != '}':
        return data_dir
    # Placeholder that must be resolved using the config.
    try:
        config = read_runner_config(runner_config_path)
    except ValueError as ex:
        raise ValueError(
            f"Path placeholder was passed ({data_dir}) but the config to resolve placeholders could not be opened: {ex}.")
    try:
        return config.get("paths", data_dir[1:-1])
    except configparser.NoOptionError as ex:
        raise ValueError(
            f"Path placeholder was passed ({data_dir}) but the config "
            f"({runner_config_path}) did not include a mapping for this placeholder")


def resolve_engine_config_filename(engine: str, config_filename: str | None) -> str:
    if engine != "quickwit":
        raise ValueError("Only quickwit is supported in resolve_engine_config_filename()")
    if config_filename:
        return os.path.join(os.getcwd(), os.path.expanduser(config_filename))
    return os.path.join(os.getcwd(), "engines", engine, "configs", "quickwit.yaml")


def get_engine_env(runner_config_path: str) -> dict[str, str]:
    """Returns additional engine env variables if present in the runner cfg."""
    try:
        config = read_runner_config(runner_config_path)
    except ValueError as ex:
        logging.info(f"Could not read runner config {runner_config_path}. This is not necessarily a problem. Original exception {ex}")
        return {}
    if "engine_env" not in config:
        return {}
    return {key.upper(): value for key, value in config.items("engine_env")}


class BearerAuthentication(requests.auth.AuthBase):
    """Helper to pass a bearer token as a header with requests."""

    def __init__(self, token):
        self.token = token

    def __call__(self, r):
        r.headers["authorization"] = f"Bearer {self.token.strip()}"
        return r


def get_docker_info(container_name: str):
    client = docker.from_env()
    try:
        container = client.containers.get(container_name)
    except docker.errors.NotFound as ex:
        logging.error("Could not fetch the docker container: %s", ex)
        return {}
    return {"image_label": container.attrs["Config"]["Image"],
            "image_hash": container.attrs["Image"]}


def find_process(process_name, cmdline_component: str | None = None) -> psutil.Process | None:
    """Finds a process by name and optionnaly cmdline component."""
    process = None
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        if cmdline_component is not None and cmdline_component not in proc.cmdline():
            continue
        if proc.name() != process_name:
            continue
        if process:
            raise ValueError(
                f'Found multiple processes with name {process_name}: {process.pid}, {proc.pid}')
        process = proc
    return process


@dataclass
class WatchedMetric:
    name: str  # e.g "object_storage_fetch_requests"
    labels: dict[str, str]  # e.g. {'operation': 'GET', 'status_code': '200'}
    factor: float = 1.

    def sample_matches(self, sample: prometheus_client.samples.Sample) -> bool:
        if self.name != sample.name: return False
        for label_name, label_value in self.labels.items():
            if sample.labels.get(label_name) != label_value:
                return False
        return True


class ProcessMonitor:
    """Reports process metrics between the start and stop of the monitor.

    Example:
      monitor = ProcessMonitor(
          process_name='loki', metrics_addr='localhost:3100/metrics',
            watched_metrics={
                'object_storage_fetch_requests': WatchedMetric(
                    name='loki_gcs_request_duration_seconds_count',
                    labels={'operation': 'GET', 'status_code': '200'}),
            })
      monitor.start()
      # ...Perfom a loki query...
      monitor.get_stats_since_start()
    will return a dict with the following stats since monitor.start() was called:
    - 'total_cpu_time_s' with the total CPU time (user+system) of the process
       with name 'loki'.
    - 'peak_memory_megabytes' with the peak resident memory of the
      process. Drawing conclusions from this metric should be done
      with care, e.g. the process's allocator might not have released
      previously allocated memory to the OS.
    - 'object_storage_fetch_requests' with the diff of the Prometheus metric
      'loki_gcs_request_duration_seconds_count' with labels
      {'operation': 'GET', 'status_code': '200'}.

    """
    def __init__(self, process_id=None, process_name=None, metrics_addr=None,
                 watched_metrics: dict[str, WatchedMetric] = None):
        if bool(process_id) == bool(process_name):
            raise ValueError('Either process_id or process_name should be specified')
        if not process_id:
            process = find_process(process_name)
            if process is None:
                raise ValueError(
                    f"Can't monitor a process that was not found {process_name=}")
            process_id = process.pid
        self.process = psutil.Process(process_id)
        self.metrics_addr = metrics_addr
        self.watched_metrics = watched_metrics
        self._metrics_values = {}
        self._reset_vm_hwm_success = True
        self._docker_client = docker.from_env()
       
    def _read_metrics(self):
        if not self.metrics_addr or not self.watched_metrics:
            return {}
        metrics = {}
        for family in prometheus_parser.text_string_to_metric_families(
                requests.get(self.metrics_addr).text):
            for sample in family.samples:
                for name, watched in self.watched_metrics.items():
                    if watched.sample_matches(sample):
                        metrics[name] = sample.value * watched.factor
        return metrics

    def _get_docker_container_id(self) -> str | None:
        """Return the container ID of the process."""
        # See man cgroups and
        # https://docs.docker.com/config/containers/runmetrics/#find-the-cgroup-for-a-given-container.
        with open(f'/proc/{self.process.pid}/cgroup', 'r') as process_cgroup:
            match = re.search(r"docker-(?P<containerid1>.*)\.scope|docker/(?P<containerid2>.*)",
                              process_cgroup.read().split(":")[-1])
            if not match:
                return None
            return match.group("containerid1") or match.group("containerid2")
    
    def _reset_vm_hwm(self) -> bool:
        """Reset VmHWM for the process in /proc. See `man proc` for details."""
        # /proc/$PID/clear_refs is only writeable by the owner of the
        # process, which in case of an engine running in a docker
        # container is typically...root.
        # In that case, we run the commands inside the docker
        # container. This is annoying as we also need to translate
        # between between the PID namespace of the host and of the
        # docker container, and because available commands inside a
        # container are typically limited.
        container_id = self._get_docker_container_id()
        if container_id:
            container = self._docker_client.containers.get(container_id)
            # This will return a \n separated list of /proc/PID/status
            # files for the processes matching self.process.name()
            # inside the container.
            grep_result = container.exec_run(
                # We don't use pgrep or fancier tools, as they are
                # often not available in a docker image.
                ["sh", "-c", r"grep -l  -s -e '^Name:\s*" + self.process.name() + r"$' /proc/*/status"])
            matching_processes_status = grep_result.output.decode("ascii").strip().split("\n")
            if grep_result.exit_code != 0 or not matching_processes_status:
                logging.error("Failed to get processes with name %s inside container %s",
                              self.process.name(), container_id)
                return False
            if len(matching_processes_status) > 1:
                # Typically java, as the memory usage is not very
                # representative because of xms, xmx, we don't
                # disambiguate using the command line.
                logging.error("Found multiple processes with name name %s inside container %s",
                              self.process.name(), container_id)
                return False
            # Finally, reset VmHWM inside the container.
            clear_refs_result = container.exec_run(
                ["sh", "-c", "echo 5 > " + matching_processes_status[0].replace("/status", "/clear_refs")])
            if clear_refs_result.exit_code != 0:
                logging.error("Failed to reset VmHWM of process with name name %s inside container %s",
                              self.process.name(), container_id)
                return False
            return True
        else:  # Not running in docker.
            with open(f'/proc/{self.process.pid}/clear_refs', 'w') as clear_refs:
                clear_refs.write("5\n")
            return True

    def _get_vm_hwm_megabytes(self) -> int | None:
        """Read /proc/pid/status to get the VmHWM (see man proc)."""
        with open(f'/proc/{self.process.pid}/status', 'r') as status:
            match = re.search(r"VmHWM:\s*(?P<size>\d*)\s*kB", status.read())
            if match:
                return int(match.group("size")) / 1024
            return None

    def start(self):
        self._reset_vm_hwm_success = self._reset_vm_hwm()
        self._cpu_times = self.process.cpu_times()
        self._metrics_values = self._read_metrics()
        return self

    def get_stats_since_start(self) -> dict[str, float]:
        cpu_times = self.process.cpu_times()
        stats = {
            'total_cpu_time_s': max(
                cpu_times.user + cpu_times.system -
                self._cpu_times.user - self._cpu_times.system,
                0),
        }
        if self._reset_vm_hwm_success:
            stats['peak_memory_megabytes'] = self._get_vm_hwm_megabytes()

        for name, new_v in self._read_metrics().items():
            stats[name] = new_v - self._metrics_values[name]
        return stats

    def __repr__(self) -> str:
        return f"ProcessMonitor({self.process=}, {self.metrics_addr=}, {self.watched_metrics=})"


class Query(object):
    def __init__(self, name, query):
        self.name = name
        self.query = query


class SearchClient(ABC):
    @abstractmethod
    def query(self, index: str, query: Query):
        raise NotImplementedError

    @abstractmethod
    def engine_info(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def commit_hash(self) -> str | None:
        raise NotImplementedError

    @property
    @abstractmethod
    def docker_container_name(self) -> str:
        """The name of the docker container running this engine."""
        raise NotImplementedError

    @abstractmethod
    def create_started_monitor(self) -> ProcessMonitor:
        """Creates a ProcessMonitor and starts it."""
        raise NotImplementedError


def export_results(endpoint: str,
                   results: dict[str, Any],
                   results_type: str,
                   exporter_token: str | None,
                   verify_https: bool = True,
                   url_file: str | None = None):
    """Exports bench results to the a REST API endpoint.

    The endpoint is supposed to implement the API of service/main.py.
    """
    api_endpoint = f'{endpoint}/api/v1/{results_type}_runs/'
    results = results.copy()
    info_fields = {'track', 'engine', 'storage', 'instance', 'tag', 'unsafe_user',
                   'source', 'commit_hash'}
    run_info = {k: results.pop(k) for k in info_fields}
    run_results = results

    # See CreateIndexingRunRequest / CreateSearchRunResults in service/schemas.py.
    request = {
        'run': {
            'run_info': run_info,
            'run_results': run_results,
        }
    }
    try:
        response = requests.post(
            api_endpoint, json=request,
            verify=verify_https,
            auth=BearerAuthentication(exporter_token) if exporter_token else None)
    except requests.exceptions.ConnectionError as ex:
        logging.error("Failed to export results to %s: %s", api_endpoint, ex)
        return
    
    if response.status_code != 200:
        resp_content = response.content
        try:
            # Just trying to get the best error message
            resp_content = json.loads(resp_content)
        except json.JSONDecodeError:
            pass
        logging.error(f'Failed exporting results to {api_endpoint}: {response} {pprint.pformat(resp_content)}')
        return

    run_info = response.json()["run_info"]
    run_id = run_info["id"]
    url = endpoint + f"/?run_ids={run_id}"
    logging.info(f'Exported results to {api_endpoint}: {run_info}\nResults can be seen at address: {url}')
    # This will typically be $GITHUB_OUTPUT for easily getting the URL from a github workflow.
    if url_file:
        with open(url_file, 'a') as out:
            out.write(f"url={url}\n")


def get_common_debug_info(engine_client: SearchClient):
    return {
        "command_line": ' '.join(sys.argv),
        "unsafe_user": getpass.getuser(),
        "engine_info": engine_client.engine_info(),
        "commit_hash": engine_client.commit_hash(),
        "docker_info": get_docker_info(engine_client.docker_container_name),
        "platform_uname": ' '.join(platform.uname()),
    }


class ElasticClient(SearchClient):

    def __init__(self, endpoint="http://127.0.0.1:9200", no_hits=False,
                 docker_container_name: str = "elasticsearch"):
        self.no_hits = no_hits
        self.root_api = endpoint
        self._docker_container_name = docker_container_name

    def create_index(self, index, config_json: str):
        response = requests.put(f"{self.root_api}/{index}", data=config_json, headers={"Content-Type": "application/json"})
        if response.status_code != 200:
            raise Exception("Error while creating index", response.text)
        return response.json()
    
    def delete_index(self, index: str):
        response = requests.delete(f"{self.root_api}/{index}")
        if response.status_code != 200:
            raise Exception("Error while deleting index", response.text)
        return response.json()

    def check_index_exists(self, index: str):
        response = requests.get(f"{self.root_api}/{index}")
        if response.status_code == 404:
            return False
        if response.status_code != 200:
            raise Exception("Error while checking index", response.text)
        return True

    def create_started_monitor(self) -> ProcessMonitor:
        process = find_process(
            "java",
            cmdline_component=(
                "org.opensearch.bootstrap.OpenSearch"
                if self._docker_container_name == "opensearch-node"
                else "org.elasticsearch.server/org.elasticsearch.bootstrap.Elasticsearch"))
        if process is None:
            raise ValueError(
                f"Can't monitor a process that was not found for {self._docker_container_name=}")
        return ProcessMonitor(process_id=process.pid).start()
    
    def query(self, index: str, query, extra_url_component=None):
        if self.no_hits:
            query["size"] = 0
        url = self.root_api
        if extra_url_component:
            url += '/' + extra_url_component
        monitor = self.create_started_monitor()
        response = requests.post(f"{url}/{index}/_search", json=query)
        monitor_stats = monitor.get_stats_since_start()
        if response.status_code != 200:
            print("Error while querying", query, response.text)
            return {
                "num_hits": 0,
                "elapsed_time_micros": -1,
                "response_status_code": response.status_code,
                "response": response.text,
            }
        data = response.json()
        return {
            "num_hits": data["hits"]["total"]["value"] if "total" in data["hits"] else 0,
            "elapsed_time_micros": data["took"] * 1000
        } | monitor_stats

    def engine_info(self):
        response = requests.get(f"{self.root_api}/")
        if response.status_code != 200:
            raise Exception(f"Error while checking basic info {status_code=} {response.text=}")
        return response.json()

    def commit_hash(self) -> str | None:
        return self.engine_info().get("version", {}).get("build_hash")

    @property
    def docker_container_name(self) -> str:
        """The name of the docker container running this engine."""
        return self._docker_container_name


class QuickwitClient(ElasticClient):
    def __init__(self, endpoint="http://127.0.0.1:7280/api/v1", no_hits=False):
        super().__init__(endpoint=endpoint, no_hits=no_hits)

    def create_index(self, index: str, config_yaml: str):
        response = requests.post(f"{self.root_api}/indexes", data=config_yaml, headers={"Content-Type": "application/yaml"})
        if response.status_code != 200:
            raise Exception("Error while creating index", response.text)
        return response.json()

    def delete_index(self, index: str):
        response = requests.delete(f"{self.root_api}/indexes/{index}")
        if response.status_code != 200:
            raise Exception("Error while deleting index", response.text)
        return response.json()

    def check_index_exists(self, index: str):
        response = requests.get(f"{self.root_api}/indexes/{index}")
        if response.status_code == 404:
            return False
        if response.status_code != 200:
            raise Exception("Error while checking index", response.text)
        return True

    def create_started_monitor(self) -> ProcessMonitor:
        # TODO: Improve hack.
        metrics_url = self.root_api.removesuffix('/api/v1') + '/metrics'
        return ProcessMonitor(
            process_name='quickwit',
            metrics_addr=metrics_url,
            watched_metrics={
                'object_storage_fetch_requests': WatchedMetric(
                    name='quickwit_storage_object_storage_gets_total',
                    labels={}),
                'object_storage_put_requests': WatchedMetric(
                    name='quickwit_storage_object_storage_puts_total',
                    labels={}),
                'object_storage_download_megabytes': WatchedMetric(
                    name='quickwit_storage_object_storage_download_num_bytes_total',
                    labels={},
                    # bytes to megabytes.
                    factor=1. / (2 ** 20)),
                'object_storage_upload_megabytes': WatchedMetric(
                    name='quickwit_storage_object_storage_upload_num_bytes_total',
                    labels={},
                    # bytes to megabytes.
                    factor=1. / (2 ** 20)),
            }).start()
    
    def query(self, index: str, query):
        monitor = self.create_started_monitor()
        results = super().query(index, query, extra_url_component='_elastic')
        monitor_stats = monitor.get_stats_since_start()
        return results | monitor_stats

    def engine_info(self):
        response = requests.get(f"{self.root_api}/version")
        if response.status_code != 200:
            raise Exception(f"Error while checking basic info {status_code=} {response.text=}")
        return response.json()

    def commit_hash(self) -> str | None:
        return self.engine_info().get("build", {}).get("commit_hash")

    @property
    def docker_container_name(self) -> str:
        """The name of the docker container running this engine."""
        return "quickwit"


class LokiClient(SearchClient):
    def __init__(self, endpoint="http://127.0.0.1:3100", no_hits=False) -> None:
        self.no_hits = no_hits
        self.root_api = endpoint

    def create_index(self, index, config_json: str):
        return
    
    def delete_index(self, index: str):
        response = requests.post(f'{self.root_api}/loki/api/v1/delete?query={{label="benchmark"}}&start=2000-01-08T22:15:32.000Z', data={}, headers={"Content-Type": "application/json"})
        if response.status_code != 200:
            raise Exception("Error while deleting index", response.text)
        return response.json()

    def check_index_exists(self, index: str):
        answer = input(f"You have to delete the data in loki yourself (e.g. delete data folder) because loki has a unusable deletion API, confirm (y/n)")
        if answer.lower() in ["n","no"]:
            raise Exception("Need to confirm")

        return False

    def create_started_monitor(self) -> ProcessMonitor:
        return ProcessMonitor(
            process_name='loki', metrics_addr=f'{self.root_api}/metrics',
            watched_metrics={
                'object_storage_fetch_requests': WatchedMetric(
                    name='loki_gcs_request_duration_seconds_count',
                    labels={'operation': 'GET', 'status_code': '200'}),
                'object_storage_put_requests': WatchedMetric(
                    name='loki_gcs_request_duration_seconds_count',
                    labels={'operation': 'PUT', 'status_code': '200'}),
            }).start()

    def query(self, index: str, query):
        del index  # Loki does not have the concept of an index.
        # Sanity check.
        if 'query' not in query:
            raise ValueError(f'Expected the json query to have a "query" field. Got {query}')
        monitor = self.create_started_monitor()
        response = requests.get(f"{self.root_api}/loki/api/v1/query_range", params=query)
        monitor_stats = monitor.get_stats_since_start()
        if response.status_code != 200:
            print("Error while querying", query, response.text)
            return {
                "num_hits": 0,
                "elapsed_time_micros": -1,
            }
        data = response.json()

        # For reference, data["data"]["stats"]["summary"] contains:
        # "bytesProcessedPerSecond": 75177670,
        # "linesProcessedPerSecond": 91689,
        # "totalBytesProcessed": 909287,
        # "totalLinesProcessed": 1109,
        # "execTime": 0.012095,
        # "queueTime": 0.0008,
        # "subqueries": 0,
        # "totalEntriesReturned": 2,
        # "splits": 5,
        # "shards": 0,
        # "totalPostFilterLines": 1109,
        # "totalStructuredMetadataBytesProcessed": 0
        return {
            # Not really the number of hits, but the best we have.
            "num_hits": data["data"]["stats"]["summary"]["totalEntriesReturned"],
            # "execTime" is in seconds.
            "elapsed_time_micros": data["data"]["stats"]["summary"]["execTime"] * 1000_000,
        } | monitor_stats

    def engine_info(self):
        response = requests.get(f"{self.root_api}/loki/api/v1/status/buildinfo")
        if response.status_code != 200:
            raise Exception(f"Error while checking basic info {status_code=} {response.text=}")
        return response.json()

    def commit_hash(self) -> str | None:
        return self.engine_info().get("revision")

    @property
    def docker_container_name(self) -> str:
        """The name of the docker container running this engine."""
        return "loki"


def drive(index: str, queries: list[Query], client: SearchClient):
    for query in queries:
        while True:
            start = time.monotonic()
            result = client.query(index, query.query)
            stop = time.monotonic()
            if (result.get("response_status_code") != 200 and
                any([sub in result.get("response", "")
                     for sub in RETRY_ON_FAILED_RESPONSE_SUBSTR])):
                logging.info(
                    "Retrying query %s because the engine does not "
                    "seem ready to take requests.",
                    query.name)
                continue
            break
        # This could move under the ProcessMonitor and we could get rid of this drive() function.
        duration = int((stop - start) * 1e6)
        yield result | {'query': query, 'duration': duration}


def read_queries(queries_dir, query_filter):
    query_files = sorted(glob("{queries_dir}/*.json".format(queries_dir=queries_dir)))
    for q_filepath in query_files:
        query_name, _ = os.path.splitext(os.path.basename(q_filepath))
        if not fnmatch.fnmatch(query_name, query_filter):
            continue
        try:
            query_json = json.load(open(q_filepath))
        except Exception as ex:  
            raise ValueError(f'Error with query in path {q_filepath}: {ex}')
        yield Query(query_name, query_json)


def get_engine_client(engine: str, no_hits: bool) -> SearchClient:
    if engine == "quickwit":
        return QuickwitClient(no_hits=no_hits)
    elif engine == "loki":
        return LokiClient(endpoint="http://127.0.0.1:3100", no_hits=no_hits)
    elif engine == "opensearch":
        return ElasticClient(endpoint="http://127.0.0.1:9301", no_hits=no_hits,
                             docker_container_name="opensearch-node")
    elif engine == "elasticsearch":
        return ElasticClient(endpoint="http://127.0.0.1:9200", no_hits=no_hits)
    else:
        raise ValueError(f"Unknown engine {engine}")


def run_search_benchmark(search_client: SearchClient, engine: str, index: str, num_iteration: int,
                         queries_dir: str, query_filter, output_filepath: str, no_hits: bool) -> dict:
    """Run the benchmark."""
    queries: list[Query] = list(read_queries(queries_dir, query_filter))

    queries_results = {}
    for query in queries:
        query_result = {
            "name": query.name,
            "query": query.query,
            "count": 0,
            "duration": { "values": []},
            "engine_duration": { "values": []},
        }
        queries_results[query.name] = query_result
    keys_with_multiple_values = {"duration", "engine_duration"}
    
    print("--- Warming up ...")
    queries_shuffled = list(queries[:])
    random.seed(2)
    random.shuffle(queries_shuffled)
    for i in range(WARMUP_ITER):
        for _ in drive(index, queries_shuffled, search_client):
            pass

    print("--- Start measuring response times ...")
    for i in range(num_iteration):
        print("- Run #%s of %s" % (i + 1, num_iteration))
        for drive_results in drive(index, queries_shuffled, search_client):
            query = drive_results.pop('query')
            count = drive_results.pop('num_hits')
            engine_duration = drive_results.pop('elapsed_time_micros')
            duration = drive_results.pop('duration')
            print(f"{query.name} {engine_duration / 1000.:.2}ms {drive_results}")
            queries_results[query.name]["count"] = count
            queries_results[query.name]["duration"]["values"].append(duration)
            queries_results[query.name]["engine_duration"]["values"].append(engine_duration)
            for key, value in drive_results.items():
                if key not in queries_results[query.name]:
                    keys_with_multiple_values.add(key)
                    queries_results[query.name][key] = {"values": []}
                queries_results[query.name][key]["values"].append(value)

    for query in queries_results.values():
        for results_key, results_values in query.items():
            if results_key not in keys_with_multiple_values:
                continue
            values = results_values["values"]
            if len(values) < 2:
                # This should not happen as we check that
                # --num-iteration >= 2. However, it does happen, so we
                # raise an exception with details to investigate.
                raise ValueError(f"Too few values for {results_key=} {query=} {engine=}")
            values.sort()
            results_values["min"] = values[0]
            results_values["max"] = values[-1]
            results_values["mean"] = statistics.mean(values)
            results_values["median"] = statistics.median(values)
            results_values["stddev"] = statistics.stdev(values)
            results_values["p90"] = statistics.quantiles(values, n=10)[8]

    results = {
        "engine": engine,
        "index": index,
        "queries": list(queries_results.values()),
    }

    return results


def prepare_index(engine: str, track: str, index: str, overwrite_index: bool):
    if engine == "quickwit":
        client = QuickwitClient()
        index_config = open(f"tracks/{track}/index-config.quickwit.yaml").read()
    elif engine == "opensearch":
        client = ElasticClient(endpoint="http://127.0.0.1:9301")
        index_config = open(f"tracks/{track}/index-config.opensearch.json").read()
    elif engine == "loki":
        client = LokiClient(endpoint="http://127.0.0.1:9301")
        index_config = open(f"tracks/{track}/index-config.quickwit.yaml").read()
    else:
        assert engine == "elasticsearch", f"Unknown engin {engine}"
        client = ElasticClient()
        index_config = open(f"tracks/{track}/index-config.elasticsearch.json").read()
        
    if client.check_index_exists(index):
        if overwrite_index:
            answer = "yes"
        else:
            answer = input(f"You are going to delete index '{index}', please confirm (y/n)")
        if answer.lower() in ["y","yes"]:
            print("Deleting index")
            client.delete_index(index)
        else:
            raise Exception("Index already exists")

    print("Creating index", index)
    client.create_index(index, index_config)


def start_engine_from_binary(
        engine: str, binary_path: str,
        engine_data_dir: str | None, engine_config_filename: str | None = None,
        extra_args: list[str] = None):
    if engine != 'quickwit':
        raise ValueError(f"Engine {engine} not supported by start_engine_from_binary().")
    config_filename = resolve_engine_config_filename(engine, engine_config_filename)
    data_dir = resolve_engine_data_dir(engine, engine_data_dir, RUNNER_CONFIG_FILENAME)
    env_vars = get_engine_env(RUNNER_CONFIG_FILENAME)
    os.makedirs(data_dir, exist_ok=True)
    process = subprocess.Popen(
        [binary_path, "run"] + (extra_args or []),
        env={"QW_DISABLE_TELEMETRY": "1",
             "QW_CONFIG": config_filename,
             "QW_DATA_DIR": data_dir,
             } | env_vars,
    )
    logging.info("Started binary %s PID=%s", binary_path, process.pid)
    time.sleep(2)
    if process.poll() is not None:
        raise Exception(f"Engine {engine} failed with code: {process.returncode}")


def start_engine_from_docker(
        engine: str, binary_path: str | None,
        engine_data_dir: str | None = None, engine_config_filename: str | None = None,
        extra_args: list[str] = None):
    if engine != 'quickwit':
        raise ValueError(f"Engine {engine} not supported by start_engine_from_docker().")
    docker_client = docker.from_env()
    image = docker_client.images.pull("quickwit/quickwit", tag="edge", platform="linux/amd64")
    config_filename = resolve_engine_config_filename(engine, engine_config_filename)
    data_dir = resolve_engine_data_dir(engine, engine_data_dir, RUNNER_CONFIG_FILENAME)
    env_vars = get_engine_env(RUNNER_CONFIG_FILENAME)
    os.makedirs(data_dir, exist_ok=True)
    container = docker_client.containers.run(
        image.id,
        ["run"] + (extra_args or []),
        name=engine,
        auto_remove=True,
        detach=True,
        init=True,
        environment={"QW_DISABLE_TELEMETRY": "1",
                     "QW_CONFIG": os.path.join("/var/lib/quickwit/configs", os.path.basename(config_filename)),
                     } | env_vars,
        mounts=[
            docker.types.Mount("/quickwit/qwdata",
                               data_dir,
                               type="bind",
                               propagation="rprivate",
                               read_only=False,
                               ),
            docker.types.Mount("/var/lib/quickwit/configs",
                               os.path.dirname(config_filename),
                               type="bind",
                               propagation="rprivate",
                               read_only=False,
                               ),
        ],
        ports={"7280/tcp": 7280, "7280/udp": 7280},
    )
    logging.info("Started container %s with ID %s. Status: %s", container.name, container.short_id,
                 container.status)
    logging.info("Waiting until it's running")
    while container.status == "created":
        time.sleep(1)
        container = docker_client.containers.get(engine)
        logging.info("Container '%s' status %s", container.name, container.status)

    if container.status != "running":
        raise ValueError("Failed to start container '%s': status: '%s'", container.name, container.status)


def start_engine(
        engine: str, binary_path: str | None,
        engine_data_dir: str | None = None, engine_config_filename: str | None = None,
        for_search_only: bool = False):
    if engine != 'quickwit':
        raise ValueError(f"Engine {engine} not supported by start_engine().")
    extra_args = []
    if for_search_only:
        extra_args.extend(["--service", "metastore", "--service", "searcher"])
    if binary_path:
        return start_engine_from_binary(
            engine, binary_path, engine_data_dir, engine_config_filename, extra_args)
    else:
        return start_engine_from_docker(
            engine, binary_path, engine_data_dir, engine_config_filename, extra_args)


def stop_engine(engine: str):
    """Stops both docker containers and non-docker processes of an engine."""
    if engine != 'quickwit':
        raise ValueError(f"Engine {engine} not supported by stop_engine().")
    docker_client = docker.from_env()
    try:
        container = docker_client.containers.get(engine)
        logging.info("Stopping docker container %s", engine)
        # This needs to be under the try/except block because if the
        # container was being stopped previously, get() can succeed
        # and stop() can raise a NotFound exception.
        container.stop()
    except docker.errors.NotFound as ex:
        logging.info("Attempted to stop %s but it was not found: %s", engine, ex)

    process = find_process(engine)
    if process is not None:
        process.kill()
        logging.info("Killed %s process with ID %s", engine, process.pid)
        # Bad way to make sure the port was released, which does not
        # happen immediately when killing a process.
        time.sleep(1)



def prune_docker_images(engine: str, until_days: int = 3):
    """Removes dangling images for a given engine.

    Args:
      engine: Name of the engine. Only 'quickwit' is supported.
      until_days: Only images older than this number of days will be removed.
    """
    if engine != 'quickwit':
        raise ValueError(f"Pruning docker images for {engine} is not supported.")
    logging.info("Pruning docker images for engine %s", engine)
    docker_client = docker.from_env()
    remove_before = (datetime.datetime.now(datetime.timezone.utc) -
                     datetime.timedelta(days=until_days))
    try:
        for image in docker_client.images.list(
                name="quickwit/quickwit", all=True, filters={"dangling": True}):
            # Docker provides date in RFC3339Nano format, which unfortunately
            # cannot be parsed by datetime.strptime which only supports
            # microseconds.
            creation_date = dateutil_parser.parse(image.attrs["Created"])
            if creation_date < remove_before:
                logging.info("Removing docker image %s", image.id)
                docker_client.images.remove(image=image.id)
    except docker.errors.APIError as ex:
        logging.info("Failed to prune images for engine %s", engine)

def run_indexing_benchmark(
        engine_client: SearchClient, engine: str, index: str,
        qw_ingest_v2: bool, track_config: dict[str, Any],
        output_path: str) -> tuple[subprocess.CompletedProcess, dict[str, Any]]:
    """Runs the indexing.

    Returns:
      (Completed qbench process, monitor stats)
    """
    print("Run indexing...")
    qbench_command = [
        "./qbench/target/release/qbench",
        "--engine",
        engine,
        "--index",
        index,
        "--dataset-uri",
        track_config["dataset_uri"],
        "--output-path",
        output_path,
        "--retry-indexing-errors",
    ]
    print(qbench_command)
    if qw_ingest_v2:
        qbench_command.append("--qw-ingest-v2")

    monitor = engine_client.create_started_monitor()
    completed_process = subprocess.run(qbench_command)
    monitor_stats = monitor.get_stats_since_start()
    return completed_process, monitor_stats
        

def run_benchmark(benchs_to_run: list[BenchType],
                  args: argparse.Namespace, exporter_token: str | None):
    """Prepares indices and runs the benchmark."""
    results_dir = f'{args.output_path}/{args.track}.{args.engine}'
    if args.tags:
        results_dir += f'.{args.tags}'
    instance = resolve_instance(args.instance)
    if instance:
        results_dir += f'.{instance}'
    if args.no_hits:
        results_dir += '_no_hits'
    os.makedirs(results_dir, exist_ok=True)

    print(f"Results will be written to {results_dir} and exported to {args.export_to_endpoint}.")

    print("======================")
    print(f"Benchmarking engine `{args.engine}` on track `{args.track}`.")
    print("======================")

    track_config = yaml.load(open(f"tracks/{args.track}/track-config.yaml"), Loader=yaml.FullLoader)
    queries_dir = f"tracks/{args.track}/queries"
    if args.engine_specific_queries_subdir:
        queries_dir = os.path.join(queries_dir, args.engine_specific_queries_subdir)
    index = track_config["index"]
    num_iteration = args.num_iteration
    if num_iteration < 2:
        raise ValueError(
            'We require at least two iterations as stats computed downstream require at least two points.')

    engine_client = get_engine_client(args.engine, args.no_hits)

    while True:
        try:
            _ = engine_client.engine_info()
        except Exception as ex:
            logging.info("Waiting until engine %s is responding", args.engine)
            time.sleep(0.5)
            continue
        break

    if BenchType.INDEXING in benchs_to_run:
        # TODO: use 'engine_client'.
        prepare_index(args.engine, args.track, index, args.overwrite_index)

    if BenchType.INDEXING in benchs_to_run:
        output_path = f'{results_dir}/indexing-results.json'
        completed_process, monitor_stats = run_indexing_benchmark(
            engine_client, args.engine, index, args.qw_ingest_v2, track_config, output_path)
        with open(output_path) as results_file:
            indexing_results = json.load(results_file)
            indexing_results['tag'] = args.tags
            indexing_results['storage'] = args.storage
            indexing_results['instance'] = instance
            indexing_results['track'] = args.track
            indexing_results['qbench_returncode'] = completed_process.returncode
            indexing_results['qbench_command_line'] = ' '.join(completed_process.args)
            indexing_results['source'] = args.source
            indexing_results |= get_common_debug_info(engine_client)
            indexing_results |= monitor_stats
            # TODO: add config (/api/v1/config)?

        with open(output_path , "w") as f:
            json.dump(indexing_results, f, default=lambda obj: obj.__dict__, indent=4)
        if args.export_to_endpoint:
            export_results(args.export_to_endpoint, indexing_results, "indexing",
                           exporter_token, verify_https=not args.disable_exporter_https_verification,
                           url_file=args.write_exported_run_url_to_file)
        if completed_process.returncode != 0:
            logging.error("Error while running indexing")
            return False

    if BenchType.SEARCH in benchs_to_run:
        print("Run search bench...")
        search_results = run_search_benchmark(engine_client, args.engine, index, num_iteration, queries_dir, args.query_filter, f'{results_dir}/search-results.json', args.no_hits)
        search_results['tag'] = args.tags
        search_results['storage'] = args.storage
        search_results['instance'] = instance
        search_results['track'] = args.track
        search_results['source'] = args.source
        search_results |= get_common_debug_info(engine_client)
        search_output_filepath = f'{results_dir}/search-results.json'
        with open(search_output_filepath , "w") as f:
            json.dump(search_results, f, default=lambda obj: obj.__dict__, indent=4)
        if args.export_to_endpoint:
            export_results(args.export_to_endpoint, search_results, "search",
                           exporter_token, verify_https=not args.disable_exporter_https_verification,
                           url_file=args.write_exported_run_url_to_file)
            
    return True


def check_exporter_token(endpoint: str, token: str, verify_https: bool = True) -> bool:
    """Returns true if the token is valid according to the service."""
    endpoint = f'{endpoint}/api/v1/check_jwt_token'
    try:
        print(f"Checking JWT token using {endpoint}")
        response = requests.get(endpoint,
                                verify=verify_https,
                                auth=BearerAuthentication(token))
    except requests.exceptions.ConnectionError as ex:
        logging.error("Failed to connect to %s: %s", endpoint, ex)
        return False
    if response.status_code == 404:
        logging.error("Endpoint '%s' not found (404)", endpoint)
        return False
    if response.status_code == 401:
        return False
    if response.status_code != 200:
        logging.error("Unexpected error from endpoint %s: %s", endpoint, response)
        return False
    return True


def get_exporter_token(endpoint: str, verify_https: bool = True) -> str:
    """Get and return a JWT token for the benchmark service endpoint.

    The token is cached to a local file for convenience during future runs.
    """
    jwt_token_filename = os.path.expanduser(JWT_TOKEN_FILENAME)
    try:
        with open(jwt_token_filename, "r") as f:
            token = f.read()
            if check_exporter_token(endpoint, token, verify_https):
                print(f"Token in {jwt_token_filename} is valid. Re-using it.")
                return token
            else:
                print(f"Invalid token in {jwt_token_filename}, trying to obtain a new one.")
    except FileNotFoundError:
        pass
    auth_url = f'{endpoint}/login/google'
    print("Opening login page of the endpoint used for exporting runs:")
    print(auth_url)
    webbrowser.open(auth_url, new=2, autoraise=True)
    token = input("Please paste the JWT token displayed in the service response:\n").strip()
    if not check_exporter_token(endpoint, token, verify_https):
        raise Exception(f"Token '{token}' is invalid, error in copy-paste?")
    print(f"Saving token to file {jwt_token_filename} for future use.")
    with open(jwt_token_filename, "w") as f:
        f.write(token)
    return token


# Temporary hacks to fix args for the github workflow running quickwit on GCS.
def tmp_fix_args_for_gh_workflow(args):
    if (args.source == 'github_workflow' and
        args.storage == 'gcs' and
        args.engine_data_dir == '{qwdata_gcs}' and
        not args.engine_config_file):
        args.engine_config_file = 'engines/quickwit/configs/cbench_quickwit_gcs.yaml'
        logging.info("Setting --engine-config-file to %s to fix gh workflow",
                     args.engine_config_file)


def main():
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )

    parser = argparse.ArgumentParser(description='Run queries against a given engine.')
    parser.add_argument('--engine', type=str, help='Engine to benchmark', required=True)
    parser.add_argument('--track', type=str, help='Track ID', required=True)
    parser.add_argument(
        '--engine_specific_queries_subdir',
        type=str,
        help="If specified, queries will be read from tracks/<track>/queries/<engine_specific_queries_subdir>. This is useful for engines that don't have the same query API as Elastic Search",
        default='')
    parser.add_argument('--tags', type=str, help='Mark the run with a tag')
    parser.add_argument('--output-path', type=str, help='Output file path where results are dumped', default="./results")
    parser.add_argument('--indexing-only', action='store_true', help='Only run indexing')
    parser.add_argument('--overwrite-index', action='store_true',
                        help='If set, previous indexes will be overwritten without confirmation.')
    parser.add_argument('--storage', type=str, help='Storage: S3, 500GB gp3, ...', default='')
    parser.add_argument(
        '--instance', type=str,
        help=(f'Instance short name: m1, c6a.2xlarge, etc. If '
              f'{AUTODETECT_GCP_INSTANCE_PLACEHOLDER} is provided, it will be '
              'autodetected on GCP.'),
        required=True)
    parser.add_argument('--search-only', action='store_true', help='Only run search')
    parser.add_argument('--no-hits', action='store_true', help='Do not retrieve docs')
    parser.add_argument('--query-filter', help='Only run queries matching the given pattern', default="*")
    parser.add_argument('--num-iteration', type=int, help='Number of iterations of the search benchmark. Must be >= 2.', default=10)
    parser.add_argument('--qw-ingest-v2', action='store_true', help="If set, we will use Quickwit's ingest V2")
    parser.add_argument('--export-to-endpoint', type=str,
                        help="If set, run results will be exported to this endpoint.",
                        default="https://qw-benchmarks.104.155.161.122.nip.io"
                        )
    parser.add_argument(
        '--disable-exporter-auth', action='store_true',
        help=("Disables the exporter authentication. Useful for running against a "
              "dev benchmark service or if the benchmark service is down."))
    parser.add_argument(
        '--disable-exporter-https-verification', action='store_true',
        help=("Disables the https cert verification. Useful for running against "
              "a local dev benchmark service."))
    parser.add_argument(
        '--manage-engine', action='store_true',
        help=("If set, the engine will be started by this script before being benchmarked, "
              "and stopped at the end. It will be started from a docker image by default, "
              "unless --binary-path is specified."))
    parser.add_argument(
        '--binary-path', type=str,
        help=("Path to the binary to run. Only makes sense with --manage-engine is set."),
        default="")
    parser.add_argument(
        '--loop', action='store_true',
        help=("If set, the benchmark will be run repeatedly until this script is killed. "
              "Useful for continuous benchmarking"))
    parser.add_argument(
        '--source', type=str,
        choices=["manual", "continuous_benchmarking", "github_workflow"],
        help=("Source of the run. In the web UI, graph will typically only be "
              "shown for 'continuous_benchmarking' runs only."),
        default="manual")
    parser.add_argument(
        '--engine-data-dir', type=str,
        help=("If specified and --manage-engine is set, this overrides the default engine data "
              "dir 'engines/$ENGINE/data'. This can contain a placeholder e.g. {qwdata} that will "
              "be resolved with the config."))
    parser.add_argument(
        '--engine-config-file', type=str,
        help=("If specified and --manage-engine is set, this overrides the default engine config file "
              "(typically 'engines/$ENGINE/configs/quickwit.yaml for quickwit)'."))
    parser.add_argument(
        '--write-exported-run-url-to-file', type=str,
        help=("If specified, the URL of the exported run will be written to that file. "
              "Useful in github workflows where this will typically be set to $GITHUB_OUTPUT."))

    args = parser.parse_args()

    tmp_fix_args_for_gh_workflow(args)

    if args.export_to_endpoint and not args.disable_exporter_auth:
        exporter_token = get_exporter_token(args.export_to_endpoint,
                                            verify_https=not args.disable_exporter_https_verification)
    else:
        exporter_token = None

    benchs_to_run = []
    if args.indexing_only and args.search_only:
        raise ValueError("Conflicting args: --indexing-only and --search-only")
    if args.indexing_only:
        benchs_to_run = [BenchType.INDEXING]
    elif args.search_only:
        benchs_to_run = [BenchType.SEARCH]
    else:
        benchs_to_run = [BenchType.INDEXING, BenchType.SEARCH]

    bench_ok = True
    while True:
        if BenchType.INDEXING in benchs_to_run:
            if args.manage_engine:
                stop_engine(args.engine)
                start_engine(args.engine, args.binary_path,
                             args.engine_data_dir, args.engine_config_file)
            # When looping, we ignore errors.
            bench_ok = run_benchmark([BenchType.INDEXING], args, exporter_token)

        if BenchType.SEARCH in benchs_to_run and bench_ok:
            if args.manage_engine:
                # We stop/start the engine after indexing so that the peak
                # memory usage metrics for search queries is more accurate
                # (otherwise, some engines may keep indexing-related
                # datastructures in memory).
                # We only start what is necessary for search
                # (for_search_only=True), so that the memory usage
                # (and other metrics to a lesser extend) are not
                # polluted by background tasks such as split merges.
                stop_engine(args.engine)
                start_engine(args.engine, args.binary_path,
                             args.engine_data_dir, args.engine_config_file, for_search_only=True)
            bench_ok = run_benchmark([BenchType.SEARCH], args, exporter_token)

        if args.manage_engine:
            stop_engine(args.engine)
            prune_docker_images(args.engine)

        if not args.loop:
            break

    return bench_ok


if __name__ == "__main__":
    if main():
        sys.exit(0)
    else:
        sys.exit(1)
