use async_trait::async_trait;
use serde::Serialize;

use crate::source::{DocumentBatch, DEFAULT_MAX_BODY_SIZE};
pub mod elasticsearch;
pub mod loki;
pub mod parseable;
pub mod quickwit;
pub mod zincobserve;

pub struct IndexInfo {
    pub num_docs: u64,
    pub num_splits: u64,
    pub num_bytes: u64,
}

#[derive(Serialize)]
pub struct BuildInfo {
    pub version: String,
    pub commit_date: String,
    pub commit_hash: String,
    pub build_target: String,
}

#[async_trait]
pub trait Sink: Sync + Send + 'static {
    /// The maximum size of the batch to be sent to `send`
    fn batch_size(&self) -> usize {
        DEFAULT_MAX_BODY_SIZE
    }
    async fn send(&self, document_batch: &DocumentBatch) -> anyhow::Result<()>;
    async fn commit(&self) -> anyhow::Result<()>;
    async fn index_info(&self) -> anyhow::Result<IndexInfo>;
    async fn build_info(&self) -> anyhow::Result<BuildInfo>;
}
