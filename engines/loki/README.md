# Benchmarking Loki

## Useful links before running the bench

- running Grafana in production https://lokidex.com/
- deep dive https://taisho6339.gitbook.io/grafana-loki-deep-dive
- sizing the cluster https://grafana.com/docs/loki/latest/setup/size/

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
docker run -i -v $(pwd)/:/etc/vector/ -v /data/benchmarks/datasets/generated-logs-v1/:/datasets/ --net benchmark --rm timberio/vector:0.36.0-debian --config /etc/vector/vector_100streams.yaml
```

### Open Grafana

- Explore view to search with Loki.
- Loki dashboards to monitor ingestion speed, RAM/CPU/Disk usage.

### Additional commands.

#### Index with 25k streams.

First make sure you have a machine with >25GBs, ideally 64GBs.

```bash
docker run --memory="61g" -d --rm --name loki --net benchmark  -p 3100:3100  -v $(pwd):/mnt/config  -v /data/loki_data_25000streams:/loki  grafana/loki:2.9.4 --config.file=/mnt/config/loki_gcs.yaml
```
where loki_gcs points to gs://bench202403-loki-25000streams and has ingester.chunk_target_size.

````bash
docker run -d  -v $(pwd)/:/etc/vector/ -v /data/benchmarks/datasets/generated-logs-v1/:/datasets/ --net benchmark --rm timberio/vector:0.36.0-debian --config /etc/vector/vector_25000streams.yaml
```
