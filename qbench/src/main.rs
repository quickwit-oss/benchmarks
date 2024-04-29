#[macro_use]
extern crate tracing;

use std::fmt::{Display, Formatter};
use std::path::PathBuf;
use std::str::FromStr;
use std::time::Instant;

use anyhow::bail;
use clap::Parser;
use futures_util::stream::FuturesUnordered;
use serde_json::json;
use source::{DocumentBatch, Source};
use tokio_stream::StreamExt;

mod sink;
mod source;
mod utils;

#[derive(Parser, Debug)]
pub struct CliArgs {
    #[arg(long, env)]
    /// Print rtsc and exit.
    print_only_rtsc: bool,

    #[arg(short, long, env)]
    /// The search engine to benchmark against.
    ///
    /// Options are currently
    /// "quickwit", "elasticsearch", "opensearch", "loki".
    engine: Engine,

    #[arg(long, env)]
    /// The target engine's host address.
    ///
    /// If not provided the default engine port and localhost are used.
    host: Option<String>,

    #[arg(short, long, env)]
    /// The target index ID to benchmark.
    index: String,

    #[arg(long, env)]
    /// Merge the index into one segment/split after indexing.
    /// Only available for Elasticsearch.
    merge: bool,

    #[arg(long, env)]
    /// Whether indexing errors should be retried (in which case, they will
    /// be retried indefinitely).
    retry_indexing_errors: bool,

    #[arg(long, env)]
    /// Whether the v2 ingestion for Quickwit should be used.
    /// Only makes sense when engine is Engine::Quickwit.
    qw_ingest_v2: bool,

    #[arg(long, env)]
    /// Specify the datasets path.
    dataset_uri: String,

    #[arg(long, env)]
    /// Specify output file path.
    output_path: Option<PathBuf>,
}

// Expose for python
#[cfg(target_arch = "x86_64")]
fn read_rdtsc() -> u64 {
    unsafe { core::arch::x86_64::_rdtsc() }
}

#[cfg(not(target_arch = "x86_64"))]
fn read_rdtsc() -> u64 {
    0
}

#[tokio::main(worker_threads = 4)]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt::init();
    let args: CliArgs = CliArgs::parse();
    if args.print_only_rtsc {
        let rtsc = read_rdtsc();
        println!("{}", rtsc);
        return Ok(());
    }
    let host = args
        .host
        .unwrap_or_else(|| args.engine.default_host().to_string());
    let source: Box<dyn Source> = Box::new(source::UriSource::new(&args.dataset_uri));
    let sink: Box<dyn sink::Sink> = match args.engine {
        Engine::Quickwit => {
            let sink =
                sink::quickwit::QuickwitSink::new(&host, &args.index, args.qw_ingest_v2);
            Box::new(sink)
        },
        Engine::Elasticsearch | Engine::Opensearch => {
            let sink = sink::elasticsearch::ElasticsearchSink::new(
                &host,
                &args.index,
                args.merge,
            );
            Box::new(sink)
        },
        Engine::Loki => {
            let sink = sink::loki::LokiSink::new(
                &host,
                //&args.index,
            );
            Box::new(sink)
        },
        _ => {
            bail!("Engine not supported");
        },
    };
    let output_path = args
        .output_path
        .unwrap_or_else(|| PathBuf::from("indexing_results.json"));
    info!(
        "Start indexing, results will be written in `{:?}`",
        output_path
    );
    // Write an empty file to avoid error at the end of indexing.
    std::fs::write(output_path.clone(), "{}")?;
    let build_info = sink.build_info().await?;
    let mut num_ingested_bytes = 0u64;
    let mut num_ingestion_error_bytes = 0u64;

    let start = Instant::now();

    let mut futures = FuturesUnordered::new();
    for batch_res in source.batch_stream(sink.batch_size()).await? {
        let doc_batch = batch_res.map_err(|err| {
            error!(err=?err);
            err
        })?;

        let batch_num_bytes = doc_batch.bytes.len() as u64;
        futures.push(send_with_retry(
            &sink,
            doc_batch,
            batch_num_bytes,
            args.retry_indexing_errors,
        ));

        // Allow 2 futures to run in parallel
        if futures.len() >= 2 {
            if let Some(result) = futures.next().await {
                handle_result(
                    result,
                    &mut num_ingested_bytes,
                    &mut num_ingestion_error_bytes,
                    start,
                )
                .unwrap();
            }
        }
    }

    sink.commit().await?;
    let index_info = sink.index_info().await?;

    let elapsed_time: f64 = start.elapsed().as_secs_f64();
    let doc_per_second = index_info.num_docs as f64 / elapsed_time;
    let megabytes_per_second = num_ingested_bytes as f64 / 1_000_000.0 / elapsed_time;
    info!("Indexing ended in {:.2} min. Final indexing throughput: {:.2} MB/s, {:.2} docs/s.\n\
          {:.2} MBs successfully ingested, {:.2} MBs with ingestion errors.",
        elapsed_time / 60.0, megabytes_per_second, doc_per_second,
        num_ingested_bytes as f64 / 1_000_000., num_ingestion_error_bytes as f64 / 1_000_000.);

    let results = json!({
        "engine": args.engine.as_ref(),
        "index": args.index,
        "num_ingested_bytes": num_ingested_bytes,
        "num_indexed_docs": index_info.num_docs,
        "num_indexed_bytes": index_info.num_bytes,
        "num_splits": index_info.num_splits,
        "indexing_duration_secs": elapsed_time,
        "doc_per_second": doc_per_second,
        "megabytes_per_second": megabytes_per_second,
        "build_info": build_info,
    });
    std::fs::write(output_path, serde_json::to_string_pretty(&results)?)?;

    Ok(())
}

async fn send_with_retry(
    sink: &Box<dyn sink::Sink>,
    doc_batch: DocumentBatch,
    batch_num_bytes: u64,
    retry: bool,
) -> Result<u64, u64> {
    loop {
        match sink.send(&doc_batch).await {
            Ok(()) => return Ok(batch_num_bytes),
            Err(err) => {
                error!(err=?err);
                if !retry {
                    return Err(batch_num_bytes);
                }
                tokio::time::sleep(tokio::time::Duration::from_millis(300)).await;
                info!("Retrying...");
            },
        }
    }
}

fn handle_result(
    result: Result<u64, u64>,
    num_ingested_bytes: &mut u64,
    num_ingestion_error_bytes: &mut u64,
    start: std::time::Instant,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    match result {
        Ok(bytes) => {
            *num_ingested_bytes += bytes;
            let elapsed_time: f64 = start.elapsed().as_secs_f64();
            let megabytes_per_second =
                *num_ingested_bytes as f64 / 1_000_000.0 / elapsed_time;
            info!("Ingest throughput: {:.2} MB/s", megabytes_per_second);
        },
        Err(bytes) => {
            *num_ingestion_error_bytes += bytes;
        },
    }
    Ok(())
}

#[derive(Debug, Copy, Clone, PartialEq, Eq)]
pub enum Engine {
    Quickwit,
    Elasticsearch,
    Opensearch,
    Loki,
    Parseable,
    Signoz,
    ZincObserve,
}

impl Engine {
    pub fn default_host(&self) -> &'static str {
        match self {
            Engine::Quickwit => "127.0.0.1:7280",
            Engine::Elasticsearch => "127.0.0.1:9200",
            Engine::Opensearch => "127.0.0.1:9301",
            Engine::Loki => "127.0.0.1:3100",
            Engine::Parseable => "127.0.0.1:8000",
            Engine::Signoz => "127.0.0.1:3301",
            Engine::ZincObserve => "127.0.0.1:5080",
        }
    }
}

impl Display for Engine {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.as_ref())
    }
}

impl FromStr for Engine {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        let engine = match s {
            "quickwit" => Engine::Quickwit,
            "elasticsearch" => Engine::Elasticsearch,
            "opensearch" => Engine::Opensearch,
            "loki" => Engine::Loki,
            "parseable" => Engine::Parseable,
            "signoz" => Engine::Signoz,
            "zincobserve" => Engine::ZincObserve,
            _ => return Err(format!("Unknown engine {s:?}")),
        };

        Ok(engine)
    }
}

impl AsRef<str> for Engine {
    fn as_ref(&self) -> &str {
        match self {
            Engine::Quickwit => "quickwit",
            Engine::Elasticsearch => "elasticsearch",
            Engine::Opensearch => "opensearch",
            Engine::Loki => "loki",
            Engine::Parseable => "parseable",
            Engine::Signoz => "signoz",
            Engine::ZincObserve => "zincobserve",
        }
    }
}
