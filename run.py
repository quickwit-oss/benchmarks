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
import requests
from abc import ABC, abstractmethod
from datetime import date

WARMUP_ITER = 1


class Query(object):
    def __init__(self, name, query):
        self.name = name
        self.query = query


class SearchClient(ABC):
    @abstractmethod
    def query(self, query: Query):
        raise NotImplementedError


class QuickwitClient:
    def __init__(self) -> None:
        self.root_api = "http://127.0.0.1:7280/api/v1"

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
        raise Exception("not implemented")

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

    def query(self, index: str, query):
        if self.no_hits:
            query["size"] = 0
        response = requests.post(f"{self.root_api}/{index}/_search", json=query)
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


def drive(index: str, queries: list[Query], client: ElasticClient):
    for query in queries:
        start = time.monotonic()
        result = client.query(index, query.query)
        stop = time.monotonic()
        duration = int((stop - start) * 1e6)
        yield (query, result['num_hits'], result['elapsed_time_micros'], duration)


def read_queries(queries_dir, query_filter):
    query_files = sorted(glob("{queries_dir}/*.json".format(queries_dir=queries_dir)))
    for q_filepath in query_files:
        query_name, _ = os.path.splitext(os.path.basename(q_filepath))
        if not fnmatch.fnmatch(query_name, query_filter):
            continue
        query_json = json.load(open(q_filepath))
        yield Query(query_name, query_json)

def run_search_benchmark(engine: str, index: str, num_iteration: int, queries_dir: str, query_filter, output_filepath: str, no_hits: bool) -> dict:
    if engine == "quickwit":
        endpoint = "http://127.0.0.1:7280/api/v1/_elastic"
    elif engine == "loki":
        endpoint = "http://127.0.0.1:3100"
    elif engine == "opensearch":
        endpoint = "http://127.0.0.1:9301"
    else:
        assert engine == "elasticsearch", f"Unknown engine {engine}"
        endpoint = "http://127.0.0.1:9200"
    es_client = ElasticClient(endpoint=endpoint, no_hits=no_hits)
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
        }
        query_idx[query.name] = query_result
        queries_results.append(query_result)
    
    print("--- Warming up ...")
    queries_shuffled = list(queries[:])
    random.seed(2)
    random.shuffle(queries_shuffled)
    for i in range(WARMUP_ITER):
        for _ in drive(index, queries_shuffled, es_client):
            pass

    print("--- Start measuring response times ...")
    for i in range(num_iteration):
        if i % 10 == 0:
            print("- Run #%s of %s" % (i + 1, num_iteration))
        for (query, count, engine_duration, duration) in drive(index, queries_shuffled, es_client):
            if count is None:
                query_idx[query.name] = {count: -1, duration: {}, engine_duration: {}}
            else:
                print(f"{query.name} {engine_duration}")
                query_idx[query.name]["count"] = count
                query_idx[query.name]["duration"]["values"].append(duration)
                query_idx[query.name]["engine_duration"]["values"].append(engine_duration)
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
    parser.add_argument('--tags', type=str, help='Mark the dataset with a tag')
    parser.add_argument('--output-path', type=str, help='Output file path where results are dumped', default="./results")
    parser.add_argument('--metrics-endpoint', type=str, help='Prometheus endpoint', default=None)
    parser.add_argument('--indexing-only', action='store_true', help='Only run indexing')
    parser.add_argument('--storage', type=str, help='Storage: S3, 500GB gp3, ...', default='')
    parser.add_argument('--instance', type=str, help='Instance short name: m1, c6a.2xlarge, ...', required=True)
    parser.add_argument('--search-only', action='store_true', help='Only run search')
    parser.add_argument('--no-hits', action='store_true', help='Do not retrieve docs')
    parser.add_argument('--query-filter', help='Only run queries matching the given pattern', default="*")
    parser.add_argument('--num-iteration', type=int, help='Number of iterations of the search benchmark', default=10)
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
    index = track_config["index"]
    num_iteration = args.num_iteration

    if not args.search_only:
        prepare_index(args.engine, args.track, index)

    if not args.search_only:
        print("Run indexing...")
        completed_process = subprocess.run([
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
        ])
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
