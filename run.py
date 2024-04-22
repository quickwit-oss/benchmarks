#!/usr/bin/env python3

import argparse
import datetime
import fnmatch
import getpass
import json
import logging
import os
import platform
import pprint
import random
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
JWT_TOKEN_FILENAME = ".jwt_token_benchmark_service.txt"


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


def find_process_by_name(process_name):
    process_id = None
    for proc in psutil.process_iter(['pid', 'name']):
        if proc.name() == process_name:
            if process_id:
                raise ValueError(
                    f'Found multiple processes with name {process_name}: {process_id}, {proc.pid}')
            process_id = proc.pid
    if not process_id:
        raise ValueError(
            f'Did not find process with name {process_name}')
    return process_id


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
    - 'object_storage_fetch_requests' with the diff of the Prometheus metric
        'loki_gcs_request_duration_seconds_count' with labels
        {'operation': 'GET', 'status_code': '200'}.
    """
    def __init__(self, process_id=None, process_name=None, metrics_addr=None,
                 watched_metrics: dict[str, WatchedMetric] = None):
        if bool(process_id) == bool(process_name):
            raise ValueError('Either process_id or process_name should be specified')
        if not process_id:
            process_id = find_process_by_name(process_name)
        self.process = psutil.Process(process_id)
        self.metrics_addr = metrics_addr
        self.watched_metrics = watched_metrics
        self._metrics_values = {}
       
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
    
    def start(self):
        self._cpu_times = self.process.cpu_times()
        self._metrics_values = self._read_metrics()

    def get_stats_since_start(self) -> dict[str, float]:
        cpu_times = self.process.cpu_times()
        stats = {
            'total_cpu_time_s': (cpu_times.user + cpu_times.system
                                 - self._cpu_times.user - self._cpu_times.system),
        }
        for name, new_v in self._read_metrics().items():
            stats[name] = new_v - self._metrics_values[name]
        return stats
        

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

    @property
    @abstractmethod
    def docker_container_name(self) -> str:
        """The name of the docker container running this engine."""
        raise NotImplementedError


def export_results(endpoint: str,
                   results: dict[str, Any],
                   exporter_token: str | None,
                   verify_https: bool = True):
    """Exports bench results to the a REST API endpoint.

    The endpoint is supposed to implement the API of service/main.py.
    """
    results = results.copy()
    info_fields = {'track', 'engine', 'storage', 'instance', 'tag', 'unsafe_user', 'source'}
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
            endpoint, json=request,
            verify=verify_https,
            auth=BearerAuthentication(exporter_token) if exporter_token else None)
    except requests.exceptions.ConnectionError as ex:
        logging.error("Failed to export results to %s: %s", endpoint, ex)
        return
    
    if response.status_code != 200:
        resp_content = response.content
        try:
            # Just trying to get the best error message
            resp_content = json.loads(resp_content)
        except json.JSONDecodeError:
            pass
        logging.error(f'Failed exporting results to {endpoint}: {response} {pprint.pformat(resp_content)}')
        return

    logging.info(f'Exported results to {endpoint}: {response.json()["run_info"]}')


def get_common_debug_info(engine_client: SearchClient):
    return {
        "command_line": ' '.join(sys.argv),
        "unsafe_user": getpass.getuser(),
        "engine_info": engine_client.engine_info(),
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

    def query(self, index: str, query, extra_url_component=None):
        if self.no_hits:
            query["size"] = 0
        url = self.root_api
        if extra_url_component:
            url += '/' + extra_url_component
        response = requests.post(f"{url}/{index}/_search", json=query)
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
        }

    def engine_info(self):
        response = requests.get(f"{self.root_api}/")
        if response.status_code != 200:
            raise Exception(f"Error while checking basic info {status_code=} {response.text=}")
        return response.json()

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

    def query(self, index: str, query):
        # TODO: Improve hack.
        metrics_url = self.root_api.removesuffix('/api/v1') + '/metrics'
        monitor = ProcessMonitor(
            process_name='quickwit',
            metrics_addr=metrics_url,
            watched_metrics={
                'object_storage_fetch_requests': WatchedMetric(
                    name='quickwit_storage_object_storage_gets_total',
                    labels={}),
            'object_storage_download_megabytes': WatchedMetric(
                name='quickwit_storage_object_storage_download_num_bytes_total',
                labels={},
                # bytes to megabytes.
                factor=1. / (2 ** 20)),
            })
        monitor.start()
        results = super().query(index, query, extra_url_component='_elastic')
        monitor_stats = monitor.get_stats_since_start()
        return results | monitor_stats

    def engine_info(self):
        response = requests.get(f"{self.root_api}/version")
        if response.status_code != 200:
            raise Exception(f"Error while checking basic info {status_code=} {response.text=}")
        return response.json()

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

    def query(self, index: str, query):
        del index  # Loki does not have the concept of an index.
        # Sanity check.
        if 'query' not in query:
            raise ValueError(f'Expected the json query to have a "query" field. Got {query}')
        monitor = ProcessMonitor(
            process_name='loki', metrics_addr=f'{self.root_api}/metrics',
            watched_metrics={
                'object_storage_fetch_requests': WatchedMetric(
                    name='loki_gcs_request_duration_seconds_count',
                    labels={'operation': 'GET', 'status_code': '200'}),
            })
        monitor.start()
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
        if i % 10 == 0:
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


def start_engine(engine: str):
    if engine != 'quickwit':
        raise ValueError(f"Engine {engine} not supported by run_engine().")
    docker_client = docker.from_env()
    image = docker_client.images.pull("quickwit/quickwit", tag="edge", platform="linux/amd64")
    config_dir = os.path.join(os.getcwd(), "engines", engine, "configs")
    data_dir = os.path.join(os.getcwd(), "engines", engine, "data")
    os.makedirs(data_dir, exist_ok=True)
    container = docker_client.containers.run(
        image.id,
        "run",
        name=engine,
        auto_remove=True,
        detach=True,
        init=True,
        environment={"QW_DISABLE_TELEMETRY": "1",
                     "QW_CONFIG": "/var/lib/quickwit/configs/quickwit.yaml",
                     },
        mounts=[
            docker.types.Mount("/quickwit/qwdata",
                               data_dir,
                               type="bind",
                               propagation="rprivate",
                               read_only=False,
                               ),
            docker.types.Mount("/var/lib/quickwit/configs",
                               config_dir,
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


def stop_engine(engine: str):
    if engine != 'quickwit':
        raise ValueError(f"Engine {engine} not supported by run_engine().")
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
        return


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


def run_benchmark(args: argparse.Namespace, exporter_token: str | None):
    """Prepares indices and runs the benchmark."""
    results_dir = f'{args.output_path}/{args.track}.{args.engine}'
    if args.tags:
        results_dir += f'.{args.tags}'
    if args.instance:
        results_dir += f'.{args.instance}'
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

    if not args.search_only:
        # TODO: use 'engine_client'.
        prepare_index(args.engine, args.track, index, args.overwrite_index)

    if not args.search_only:
        print("Run indexing...")
        qbench_command = [
            "./qbench/target/release/qbench",
            "--engine",
            args.engine,
            "--index",
            index,
            "--dataset-uri",
            track_config["dataset_uri"],
            "--output-path",
            f'{results_dir}/indexing-results.json',
            "--retry-indexing-errors",
        ]
        if args.qw_ingest_v2:
            qbench_command.append("--qw-ingest-v2")
        completed_process = subprocess.run(qbench_command)
        with open(f'{results_dir}/indexing-results.json') as results_file:
            indexing_results = json.load(results_file)
            indexing_results['tag'] = args.tags
            indexing_results['storage'] = args.storage
            indexing_results['instance'] = args.instance
            indexing_results['track'] = args.track
            indexing_results['qbench_returncode'] = completed_process.returncode
            indexing_results['qbench_command_line'] = ' '.join(qbench_command)
            indexing_results['source'] = args.source
            indexing_results |= get_common_debug_info(engine_client)
            # TODO: add config (/api/v1/config)?

            search_output_filepath = f'{results_dir}/indexing-results.json'
            with open(search_output_filepath , "w") as f:
                json.dump(indexing_results, f, default=lambda obj: obj.__dict__, indent=4)
            if args.export_to_endpoint:
                export_results(f'{args.export_to_endpoint}/api/v1/indexing_runs/', indexing_results,
                               exporter_token, verify_https=not args.disable_exporter_https_verification)
        if completed_process.returncode != 0:
            logging.error("Error while running indexing %s", completed_process.stderr)
            return False

    if not args.indexing_only:
        print("Run search bench...")
        search_results = run_search_benchmark(engine_client, args.engine, index, num_iteration, queries_dir, args.query_filter, f'{results_dir}/search-results.json', args.no_hits)
        search_results['tag'] = args.tags
        search_results['storage'] = args.storage
        search_results['instance'] = args.instance
        search_results['track'] = args.track
        search_results['source'] = args.source
        search_results |= get_common_debug_info(engine_client)
        search_output_filepath = f'{results_dir}/search-results.json'
        with open(search_output_filepath , "w") as f:
            json.dump(search_results, f, default=lambda obj: obj.__dict__, indent=4)
        if args.export_to_endpoint:
            export_results(f'{args.export_to_endpoint}/api/v1/search_runs/', search_results,
                           exporter_token, verify_https=not args.disable_exporter_https_verification)
            
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
    try:
        with open(JWT_TOKEN_FILENAME, "r") as f:
            token = f.read()
            if check_exporter_token(endpoint, token, verify_https):
                print(f"Token in {JWT_TOKEN_FILENAME} is valid. Re-using it.")
                return token
            else:
                print(f"Invalid token in {JWT_TOKEN_FILENAME}, trying to obtain a new one.")
    except FileNotFoundError:
        pass
    auth_url = f'{endpoint}/login/google'
    print("Opening login page of the endpoint used for exporting runs:")
    print(auth_url)
    webbrowser.open(auth_url, new=2, autoraise=True)
    token = input("Please paste the JWT token displayed in the service response:\n").strip()
    if not check_exporter_token(endpoint, token, verify_https):
        raise Exception(f"Token '{token}' is invalid, error in copy-paste?")
    print(f"Saving token to file {JWT_TOKEN_FILENAME} for future use.")
    with open(JWT_TOKEN_FILENAME, "w") as f:
        f.write(token)
    return token


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
    parser.add_argument('--instance', type=str, help='Instance short name: m1, c6a.2xlarge, ...', required=True)
    parser.add_argument('--search-only', action='store_true', help='Only run search')
    parser.add_argument('--no-hits', action='store_true', help='Do not retrieve docs')
    parser.add_argument('--query-filter', help='Only run queries matching the given pattern', default="*")
    parser.add_argument('--num-iteration', type=int, help='Number of iterations of the search benchmark. Must be >= 2.', default=10)
    parser.add_argument('--qw-ingest-v2', action='store_true', help="If set, we will use Quickwit's ingest V2")
    parser.add_argument('--export-to-endpoint', type=str,
                        help="If set, run results will be exported to this endpoint.",
                        default="https://qw-internal-benchmark-service.104.155.161.122.nip.io"
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
              "and stopped at the end."))
    parser.add_argument(
        '--loop', action='store_true',
        help=("If set, the benchmark will be run repeatedly until this script is killed. "
              "Useful for continuous benchmarking"))
    parser.add_argument(
        '--source', type=str,
        choices=["manual", "continuous_benchmarking"],
        help=("Source of the run. In the web UI, graph will typically only be "
              "shown for 'continuous_benchmarking' runs only."),
        default="manual")

    args = parser.parse_args()

    if args.export_to_endpoint and not args.disable_exporter_auth:
        exporter_token = get_exporter_token(args.export_to_endpoint,
                                            verify_https=not args.disable_exporter_https_verification)
    else:
        exporter_token = None

    while True:
        if args.manage_engine:
            stop_engine(args.engine)
            start_engine(args.engine)

        # When looping, we ignore errors.
        bench_ok = run_benchmark(args, exporter_token)

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
