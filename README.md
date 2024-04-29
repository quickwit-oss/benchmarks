# Benchmark for Logs & Traces Search Engines

## Overview

This benchmark is designed to measure the performance of various search engines for logs and traces use cases and more generally for append-only semi-structured data.

The benchmark makes use of two datatsets:
- A 1TB dataset sampled from the [GitHub Archive](https://www.gharchive.org/) dataset.
- A 1TB log datasets generated with the [https://github.com/elastic/elastic-integration-corpus-generator-tool](elastic-integration-corpus-generator-tool)

We plan to add a trace dataset soon.

The supported engines are:
- [Quickwit](https://quickwit.io)
- [Elasticsearch](https://www.elastic.co/)
- [Loki](https://grafana.com/oss/loki/) (only for generated logs)


## Prerequisites

### Dependencies

- [Make](https://www.gnu.org/software/make/) to ease the running of the benchmark.
- [Docker](https://docs.docker.com/get-docker/) to run the benchmarked engines, including the Python API.
- [Python3](https://www.python.org/downloads/) to download the dataset and run queries against the benchmarked engines.
- [Rust](https://www.rust-lang.org/tools/install) and `openssl-devel` to build the ingestion tool `qbench`.
- [gcloud](https://cloud.google.com/sdk/docs/install) to download datasets.
- Various python packages installed with `pip install -r requirements.txt`

### Build qbench

```bash

cd qbench
cargo build --release

```

### Download datasets

For the generated logs dataset:

```bash
mkdir -p datasets
gcloud storage cp "gs://quickwit-datasets-public/benchmarks/generated-logs/generated-logs-v1-????.ndjson.gz" datasets/
```

## Running the benchmark manually

### Start engines

Go to desired engines subdirs `engines/<engine_name>` and run `make start`.

### Indexing phase

```bash

python3 run.py --engine quickwit --storage SSD --track generated-logs --instance m1 --tags my-bench-run --indexing-only

```

By default this will export results to the [benchmark
service](service/README.md) accessible at [this
address](https://qw-benchmarks.104.155.161.122.nip.io).
The first time this runs, you will be re-directed to a web page where
you should login with you Google account and pass back a token to run.py (just follow the
instructions the tool prints).
Exporting to the benchmark service can be disabled by passing the flag `--export-to-endpoint ""`

After indexing (and if exporting to the service was not disabled), the tool will print a URL to access results, e.g.:
https://qw-benchmarks.104.155.161.122.nip.io/?run_ids=678

Results will also be saved to a `results/{track}.{engine}.{tag}.{instance}/indexing-results.json` file.

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


### Execute the queries

```bash

python3 run.py --engine quickwit --storage SSD --track generated-logs --instance m1 --tags my-bench-run --search-only

```

The results will also be exported to the service and saved to a `results/{track}.{engine}.{tag}.{instance}/search-results.json` file.

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

## Exploring results

Use the [Benchmark Service web page](https://qw-benchmarks.104.155.161.122.nip.io).

### Run comparison

The default page allows selecting and comparing runs:
[example](https://qw-benchmarks.104.155.161.122.nip.io/?run_ids=779,780,771,772).

Runs are identified by a numerical ID and are automatically named
`<engine>.<storage>.<instance>.<short_commit_hash>.<tag>`.
For now, names are allowed to collide, i.e. a given name can refer to
multiple runs. In that case, selecting a name in the list of runs to
compare will show the most recent indexing run with that name, and the
most recent search run with that name.

Tips:
- The URL of the page is a permanent link to the runs shown. This is
  convenient way to share results.
- Clicking on the run name in the comparison shows the raw run results
  with additional information.
- It's fine if a run only has indexing or search results.
- The full list of runs is loaded when the web page is loaded, so you
  may need to reload it to see your latest runs.

### Graphs

The [graphs
page](https://qw-benchmarks.104.155.161.122.nip.io/?page=graphs)
allows plotting graphs of indexing and search run results over time
([example](https://qw-benchmarks.104.155.161.122.nip.io/?page=graphs&track=generated-logs&run_filter_display_name=quickwit.pd-ssd.c3-standard-4.docker_edge)). Only
runs with `source` `continuous_benchmarking` or `github_workflow` are
shown there. Runs are identified by a string
`<engine>.<storage>.<instance>.<tag>` (note the absence of commit
hash) which refers to a series of indexing and search runs over time.

Tip:
- The URL of the page is a permanent link to the series of runs
  shown. Later visits can contain additional data points.
- Clicking on a point in any graph shows the raw results of the run
  that contributed the point.


### Running the service

See [here](service/README.md) for running the benchmark service.

## Loki VS Quickwit (WIP)

Details of the comparison can be found [here](loki_quickwit_benchmark.md).
