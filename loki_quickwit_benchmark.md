
## Benchmark Quickwit VS Loki results

## Goals

The purpose of this benchmark is to help Quickwit's users understand the tradeoffs between Loki and Quicwkit.

Even though both engines have the same decoupled compute and storage architecture, they are very different when it comes to compare the datastructure used to query the data:
- Quickwit is a search engine equipped with an inverted index and a columnar storage for analytics
- Loki does not have those datastructure but has the concept of `labels`. Those lables are used to split the incoming data into unique streams identified by their labels.

Each engine has its pros and cons. Building datastructures like an inverted index is costly and Quickwit will take more CPU at ingestion.

On the contrary, Quickwit will be faster at search time. This benchmark will highlight this tradeoff.

## Dataset

For this benchmark, we will use a log dataset generated with the [elastic-integration-corpus-generator-tool](https://github.com/elastic/elastic-integration-corpus-generator-tool), it is available `gs://quickwit-datasets-public/benchmarks/generated-logs/generated-logs-v1-*`.

For this benchmark, we only used the first 200 files. You can download them from our GCS bucket with this command:

```bash
gcloud storage cp "gs://quickwit-datasets-public/benchmarks/generated-logs/generated-logs-v1-{0..200}.ndjson.gz" datasets/
```

This dataset contains 243,527,673 logs totalling 212.40 GB. A log is structured JSON document, see this [example](tracks/generated-logs-for-loki/log_example.json).

## Metrics
### Ingestion
At ingestion, we will compare the ingestion time, the total CPU time, the index size and the number of files stored on the object storage.

### Benchmarked queries
To keep things both simple and relevant, we chose to focus on the two types of queries used by the Grafana Explore view:
- The query which fetches the last 100 or 1000 logs.
- The query which fetches the log volume per log level.

| Query   |   Last 100 logs   | Log volume per log level   |
|----------|----------|------------|
| Match all | [ ]   | [x] |
| Contain `queen` | [x]   | [x] |
| Contain label region `us-east-2` | [x] | [x] |
| Contain label region `us-east-2` and `queen` | [x] | [x] |

For each query, we track the query latency, the total CPU time, and the number of GET requests on the object storage.

### Setup
### Object storage
We used Google Cloud Storage (GCS).

### Caching
Quickwit and Loki caching mechanisms were all disabled (as much as we can).

### Loki setup
We used labels for region and log levels, this makes up to 100 streams. We tried different sets of labels, but it was too difficult to control the cardinality which generally exploded and made the ingestion slow and eating a lot of RAM.

You can find loki config file [here](engines/loki/loki_gcs.yaml).

Vector was used to send logs in Loki.

## Results

### Ingestion

| Engine   |   Loki   | Quickwit   |
|----------|----------|------------|
| Ingest time | 55 min   | 123 min |
| Mean vCPU | 2.75 | 2.2 |
| Total CPU time | ~151 min | ~270 min |
| Number of files on GCS | 145,756 | 25 |
| Bucket size | 55 GiB | 53 GiB |

## Queries

### Query last 100 logs

<table>
    <thead>
        <tr>
            <th></th>
            <th colspan="2">Latency</th>
            <th colspan="2">CPU Time (s)</th>
            <th colspan="2">Get Requests</th>
        </tr>
        <tr>
            <th>Query \ Engine</th>
            <th>Loki</th>
            <th>Quickwit</th>
            <th>Loki</th>
            <th>Quickwit</th>
            <th>Loki</th>
            <th>Quickwit</th>
        </tr>
    </thead>
    <tbody>
    <tr>
        <td>Logs containing `queen`</td>
        <td>15.0</td>
        <td>0.6</td>
        <td>229</td>
        <td>2.89</td>
        <td>24,682</td>
        <td>207</td>
    </tr>
    <tr>
        <td>Logs containing `us-east-2` (label)</td>
        <td>1.0</td>
        <td>0.6</td>
        <td>2.05</td>
        <td>2.82</td>
        <td>47</td>
        <td>203</td>
    </tr>
    <tr>
        <td>Logs containing `us-east-2` (label) and `queen`</td>
        <td>0.8</td>
        <td>0.5</td>
        <td>10</td>
        <td>3</td>
        <td>585</td>
        <td>255</td>
    </tr>
    </tbody>
</table>


### Query log volume by level

<table>
    <thead>
        <tr>
            <th></th>
            <th colspan="2">Latency</th>
            <th colspan="2">CPU Time (s)</th>
            <th colspan="2">Get Requests</th>
        </tr>
        <tr>
            <th>Query \ Engine</th>
            <th>Loki</th>
            <th>Quickwit</th>
            <th>Loki</th>
            <th>Quickwit</th>
            <th>Loki</th>
            <th>Quickwit</th>
        </tr>
    </thead>
    <tbody>
    <tr>
        <td>Log volume on all dataset</td>
        <td>85.0</td>
        <td>2.1</td>
        <td>1151</td>
        <td>22.3</td>
        <td>204,808</td>
        <td>88</td>
    </tr>
    <tr>
        <td>Log volume containing `queen`</td>
        <td>560.0</td>
        <td>0.4</td>
        <td>8688</td>
        <td>3.2</td>
        <td>203,910</td>
        <td>147</td>
    </tr>
    <tr>
        <td>Log volume containing `us-east-2` (label)</td>
        <td>4.1</td>
        <td>0.6</td>
        <td>41</td>
        <td>2.85</td>
        <td>6,180</td>
        <td>146</td>
    </tr>
    <tr>
        <td>Log volume containing `us-east-2` (label) and `queen`</td>
        <td>27.0</td>
        <td>0.5</td>
        <td>337</td>
        <td>2.93</td>
        <td>5,471</td>
        <td>195</td>
    </tr>
    </tbody>
</table>

## Reproducing the benchmark

Coming soon!

### Command to get the number of files stored by Loki

```
gsutil ls -lR gs://bench20240414-loki-2-9-6--100streams | tail -n 1
TOTAL: 145756 objects, 55137804027 bytes (51.35 GiB)
```

