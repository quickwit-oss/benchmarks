#!/usr/bin/env python3

import subprocess
import requests
from glob import glob
import yaml
import sys
import json
import time
import logging
import statistics
import fnmatch

import argparse
import time
import os
import json
import random
import psutil
from abc import ABC, abstractmethod
from datetime import date
from dataclasses import dataclass
import prometheus_client
import prometheus_client.parser as prometheus_parser

WARMUP_ITER = 1


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
    name: str
    labels: dict[str, str]
    factor: float = 1.

    def sample_matches(self, sample: prometheus_client.samples.Sample) -> bool:
        if self.name != sample.name: return False
        for label_name, label_value in self.labels.items():
            if sample.labels.get(label_name) != label_value:
                return False
        return True


class ProcessMonitor:
    def __init__(self, process_id=None, process_name=None, metrics_addr=None, watched_metrics: dict[str, WatchedMetric] = None):
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
            'total_cpu_time_s': cpu_times.user + cpu_times.system - self._cpu_times.user - self._cpu_times.system,
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


class ElasticClient(SearchClient):
    def __init__(self, endpoint="http://127.0.0.1:9200", no_hits=False) -> None:
        self.no_hits = no_hits
        self.root_api = endpoint

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
            }
        data = response.json()
        return {
            "num_hits": data["hits"]["total"]["value"] if "total" in data["hits"] else 0,
            "elapsed_time_micros": data["took"] * 1000
        }

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


def drive(index: str, queries: list[Query], client: SearchClient):
    for query in queries:
        start = time.monotonic()
        result = client.query(index, query.query)
        stop = time.monotonic()
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

def run_search_benchmark(engine: str, index: str, num_iteration: int, queries_dir: str, query_filter, output_filepath: str, no_hits: bool) -> dict:
    if engine == "quickwit":
        search_client = QuickwitClient(no_hits=no_hits)
    elif engine == "loki":
        search_client = LokiClient(endpoint="http://127.0.0.1:3100", no_hits=no_hits)
    elif engine == "opensearch":
        search_client = ElasticClient(endpoint="http://127.0.0.1:9301", no_hits=no_hits)
    elif engine == "elasticsearch":
        search_client = ElasticClient(endpoint="http://127.0.0.1:9200", no_hits=no_hits)
    else:
        raise ValueError(f"Unknown engine {engine}")

    queries: list[Query] = list(read_queries(queries_dir, query_filter))

    queries_results = []
    query_idx = {}
    for query in queries:
        query_result = {
            "name": query.name,
            "query": query.query,
            "count": 0,
            "duration": { "values": []},
            "engine_duration": { "values": []},
            # TODO: gcs_fetch_requests
        }
        query_idx[query.name] = query_result
        queries_results.append(query_result)
    
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
            if count is None:
                query_idx[query.name] = {count: -1, duration: {}, engine_duration: {}}
            else:
                print(f"{query.name} {engine_duration / 1000.:.2}ms {drive_results}")
                query_idx[query.name]["count"] = count
                query_idx[query.name]["duration"]["values"].append(duration)
                query_idx[query.name]["engine_duration"]["values"].append(engine_duration)
                # TODO: propagate the remaining key/values of drive_results to query_idx.
    for query in queries_results:
        query["duration"]["values"].sort()
        query["engine_duration"]["values"].sort()
        for duration_type in ["duration", "engine_duration"]:
            query[duration_type]["min"] = query["duration"]["values"][0]
            query[duration_type]["max"] = query["duration"]["values"][-1]
            query[duration_type]["mean"] = statistics.mean(query["duration"]["values"])
            query[duration_type]["median"] = statistics.median(query["duration"]["values"])
            query[duration_type]["stddev"] = statistics.stdev(query["duration"]["values"])
            query[duration_type]["p90"] = statistics.quantiles(query["duration"]["values"], n=10)[8]

    results = {
        "engine": engine,
        "index": index,
        "queries": queries_results,
    }

    return results


def prepare_index(engine, track, index):
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
        answer = input(f"You are going to delete index '{index}', please confirm (y/n)")
        if answer.lower() in ["y","yes"]:
            print("Deleting index")
            client.delete_index(index)
        else:
            raise Exception("Index already exists")

    print("Creating index", index)
    client.create_index(index, index_config)


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
    parser.add_argument('--tags', type=str, help='Mark the dataset with a tag')
    parser.add_argument('--output-path', type=str, help='Output file path where results are dumped', default="./results")
    parser.add_argument('--metrics-endpoint', type=str, help='Prometheus endpoint', default=None)
    parser.add_argument('--indexing-only', action='store_true', help='Only run indexing')
    parser.add_argument('--storage', type=str, help='Storage: S3, 500GB gp3, ...', default='')
    parser.add_argument('--instance', type=str, help='Instance short name: m1, c6a.2xlarge, ...', required=True)
    parser.add_argument('--search-only', action='store_true', help='Only run search')
    parser.add_argument('--no-hits', action='store_true', help='Do not retrieve docs')
    parser.add_argument('--query-filter', help='Only run queries matching the given pattern', default="*")
    parser.add_argument('--num-iteration', type=int, help='Number of iterations of the search benchmark. Must be >= 2.', default=10)
    parser.add_argument('--qw-ingest-v2', action='store_true', help="If set, we will use Quickwit's ingest V2")

    args = parser.parse_args()

    results_dir = f'{args.output_path}/{args.track}.{args.engine}'
    if args.tags:
        results_dir += f'.{args.instance}{args.tags}'
    if args.no_hits:
        results_dir += '_no_hits'
    try:
        os.makedirs(results_dir)
    except OSError:
        pass

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

    if not args.search_only:
        prepare_index(args.engine, args.track, index)

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

            search_output_filepath = f'{results_dir}/indexing-results.json'
            with open(search_output_filepath , "w") as f:
                json.dump(indexing_results, f, default=lambda obj: obj.__dict__, indent=4)
        if completed_process.returncode != 0:
            print("Error while running indexing", completed_process.stderr)
            return False

    if not args.indexing_only:
        print("Run search bench...")
        search_results = run_search_benchmark(args.engine, index, num_iteration, queries_dir, args.query_filter, f'{results_dir}/search-results.json', args.no_hits)
        search_results['tag'] = args.tags
        search_results['storage'] = args.storage
        search_results['instance'] = args.instance
        search_results['track'] = args.track
        search_output_filepath = f'{results_dir}/search-results.json'
        with open(search_output_filepath , "w") as f:
            json.dump(search_results, f, default=lambda obj: obj.__dict__, indent=4)

    return True

if __name__ == "__main__":
    import sys
    if main():
        sys.exit(0)
    else:
        sys.exit(1)
