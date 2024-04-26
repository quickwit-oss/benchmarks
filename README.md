# Benchmark for Logs & Traces Search Engines

Last results are available at (not yet available) [https://benchmark.quickwit.io](https://benchmark.quickwit.io)

## Overview

This benchmark is designed to measure the performance of various search engines for logs and traces use cases and more generally for append-only semi-structured data.

The benchmark makes use of two datatsets:
- A 1TB dataset sampled from the [GitHub Archive](https://www.gharchive.org/) dataset.
- A 1TB log datasets generated with t

We plan to add a trace dataset soon.

The supported engines are:
- [Quickwit](https://quickwit.io)
- [Elasticsearch](https://www.elastic.co/)
- [Loki](https://grafana.com/oss/loki/) (only for generated logs)


## Running the benchmark

### Build qbench

```bash

cd qbench
cargo build --release

```

### Run the benchmark

```bash

python3 run.py --engine elasticsearch --track generated-logs --instance m1

```

### Prerequisites

- [Make](https://www.gnu.org/software/make/) to ease the running of the benchmark.
- [Docker](https://docs.docker.com/get-docker/) to run the benchmarked engines, including the Python API.
- [Python3](https://www.python.org/downloads/) to download the dataset and run queries against the benchmarked engines.
- [Rust](https://www.rust-lang.org/tools/install) and `openssl-devel` to build the ingestion tool `qbench`.
- [gcloud](https://cloud.google.com/sdk/docs/install) to download datasets.
- Prometheus client python library.
- psutil python library: `apt-get install python3-psutil`

Alternatively, python deps can be installed with:
`pip install -r requirements.txt`

### Downloading datasets

```bash
mkdir -p datasets
gcloud storage cp "gs://quickwit-datasets-public/benchmarks/generated-logs/generated-logs-v1-????.ndjson.gz" datasets/
```

### Compile qbench

```
make qbench
```

### Indexing phase

```

python3 run.py --engine elasticsearch --track generated-logs --instance m1 --indexing-only

```

After indexing, results will be saved in `results/{track}.{engine}.{tag}/indexing-results.json` file.

```json
{
  "doc_per_second": 8752.761519421289,
  "engine": "quickwit",
  "index": "generated-logs",
  "indexing_duration_secs": 1603.68884367,
  "mb_bytes_per_second": 22.77175235654048,
  "num_indexed_bytes": 18840178633,
  "num_indexed_docs": 14036706,
  "num_ingested_bytes": 36518805205,
  "num_ingested_docs": 14036706,
  "num_splits": 12
}
```

### Disable Caches
Disable request caches for each engine to avoid benchmarking the cache.

#### Quickwit
```yaml
searcher:
  partial_request_cache_capacity: 0
```

### Execute the queries

```bash

python3 run.py --engine elasticsearch --track generated-logs --instance m1 --search-only

```

The results will be saved in `results/{track}.{engine}.{tag}/search-results.json` file.

```json
{
    "engine": "quickwit",
    "index": "generated-logs",
    "queries": [
        {
            "id": 0,
            "query": {
                "query": "payload.description:the",
                "sort_by_field": "-created_at",
                "max_hits": 10
            },
            "tags": [
                "search"
            ],
            "count": 138290,
            "duration": [
                8843,
                9131,
                9614
            ],
            "engine_duration": [
                7040,
                7173,
                7508
            ]
        }
    ]
}
```

#### Merging engine results

There is a step to merge indexing results file and queries results file into a single file.

```bash
`make merge-results`
```

This step will be removed in the future.


### Explore the results

To explore the results, first merge the intermediate results file of the engine, then merge all results files from all engines and finally serve the results:

```bash
make merge-results
make serve
```

and open your browser at [http://localhost:8080](http://localhost:8080).

For `make serve` to work, `export NODE_OPTIONS=--openssl-legacy-provider` or a better fix might be needed (see [stackoverflow thread](https://stackoverflow.com/questions/69692842/error-message-error0308010cdigital-envelope-routinesunsupported)).


### Export results to the benchmark service

> [!NOTE]
> This is still WIP and subject to change.

Use `run.py` with `--export-to-endpoint` to export benchmark results to the benchmark service.
```bash
python run.py --engine quickwit --storage SSD --track generated-logs --instance P14s_laptop --tags "$(date '+%Y%m%d')_${USER}_test_run"  --export-to-endpoint https://qw-benchmarks.104.155.161.122.nip.io
```
The first time this runs, you will be re-directed to a web page where
you should login with you Google account and pass back a token to run.py (just follow the
instructions the tool prints).

Exported runs can then be seen in the [Benchmark Service](https://qw-benchmarks.104.155.161.122.nip.io).

See [here](service/README.md) for running the benchmark service.
