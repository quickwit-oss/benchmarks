# Benchmarking Loki

## Useful links before running the bench

- running Grafana in production https://lokidex.com/
- deep dive https://taisho6339.gitbook.io/grafana-loki-deep-dive
- sizing the cluster https://grafana.com/docs/loki/latest/setup/size/

## Context

It is hard to compare Quickwit with Loki for several reasons:
- Loki should be fast on ingestion but slow when searching the needle in the haystack or when doing analytics. Quickwit is slower on ingestion (because of indexing) and is faster on search and analytics.
- the Query Language is very different. As a consequence, we will compare Loki and Quickwit only on very basic queries.


## Goals

We want to compare Loki and Quickwit on:
- CPU, RAM, disk usage
- Volume query on a time window (like the one in the explore view)
- Query all on a given label or set of labels
- Query a very frequent keyword
- Query a rare keyword (needle in the haystack)


## Prerequisites

- You should have downloaded the generated logs dataset.

## Running the benchmark


### Create `benchmark` network

All containers should be run in the `benchmark` network.
Let's start by creating the docker network:

```bash
docker network create benchmark
```

### Starting monitoring tools

```bash
# To run at the root level
# It will start Grafana, Prometheus, Cadvisor, ...
# and load predefined dashboards
docker compose up -d
```

### Start Loki

```bash
# In engines/loki directory
make start
```

### Send logs with vector

```bash
docker run -i -v $(pwd)/:/etc/vector/ -v $(pwd)/../../datasets/:/datasets/ --net benchmark --rm timberio/vector:0.36.0-debian
```

### Open Grafana

- Explore view to search with Loki.
- Loki dashboards to monitor ingestion speed, RAM/CPU/Disk usage.


