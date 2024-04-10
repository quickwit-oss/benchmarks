use std::time::Duration;

use anyhow::{bail, Context};
use async_trait::async_trait;
use http::{header, StatusCode};
use reqwest::{Client, Url};

use super::{BuildInfo, IndexInfo, Sink};
use crate::source::DocumentBatch;

#[derive(Clone)]

pub struct QuickwitSink {
    api_root_url: Url,
    index_url: Url,
    ingest_url: Url,
    client: Client,
}

impl QuickwitSink {
    pub fn new(host: &str, index_id: &str, ingest_v2: bool) -> Self {
        let api_root_url =
            Url::parse(&format!("http://{host}/api/v1/")).expect("Invalid quickwit URL");
        let index_url = Url::parse(&format!("http://{host}/api/v1/indexes/{index_id}/"))
            .expect("Invalid quickwit URL");
        let ingest_url_component = if ingest_v2 { "ingest-v2" } else { "ingest" };
        let ingest_url = Url::parse(&format!(
            "http://{host}/api/v1/{index_id}/{ingest_url_component}"
        ))
        .expect("Invalid quickwit URL");
        let client = Client::builder()
            .connect_timeout(Duration::from_secs(5))
            .timeout(Duration::from_secs(60))
            .build()
            .unwrap();
        Self {
            api_root_url,
            ingest_url,
            index_url,
            client,
        }
    }
}

#[async_trait]
impl Sink for QuickwitSink {
    async fn send(&self, document_batch: &DocumentBatch) -> anyhow::Result<()> {
        let ingest_url = if document_batch.last {
            let mut url = self.ingest_url.clone();
            url.set_query(Some("commit=force"));
            info!("Forcing commit to quickwit...");
            url
        } else {
            self.ingest_url.clone()
        };
        let mut sent = false;
        while !sent {
            let response = self
                .client
                .post(ingest_url.clone())
                .header(header::CONTENT_TYPE, "application/json")
                .body(document_batch.bytes.clone())
                .send()
                .await?;
            if response.status() == StatusCode::TOO_MANY_REQUESTS {
                warn!("Too many requests, waiting 1s...");
                tokio::time::sleep(Duration::from_secs(1)).await;
            } else if response.status() != StatusCode::OK {
                error!(resp=?response, "Quickwit API error");
                bail!(
                    "http error with status code {}: {:?}",
                    response.status(),
                    response
                );
            } else {
                sent = true;
            }
        }
        Ok(())
    }

    async fn commit(&self) -> anyhow::Result<()> {
        Ok(())
    }

    async fn index_info(&self) -> anyhow::Result<IndexInfo> {
        let describe_url = self
            .index_url
            .join("describe")
            .expect("Invalid quickwit URL");
        let response = self
            .client
            .get(describe_url)
            .header(header::CONTENT_TYPE, "application/json")
            .send()
            .await
            .with_context(|| "Quickwit request error")?;
        if response.status() != StatusCode::OK {
            error!(resp=?response, "Quickwit API error");
            bail!(
                "http error with status code {}: {:?}",
                response.status(),
                response
            );
        }

        let data: serde_json::Value = response.json().await?;
        let num_docs = data["num_published_docs"]
            .as_u64()
            .expect("num_published_docs field must be a u64");
        let num_splits = data["num_published_splits"]
            .as_u64()
            .expect("num_published_splits field must be a u64");
        let num_bytes = data["size_published_splits"]
            .as_u64()
            .expect("size_published_docs_uncompressed field must be a u64");

        Ok(IndexInfo {
            num_docs,
            num_bytes,
            num_splits,
        })
    }

    async fn build_info(&self) -> anyhow::Result<BuildInfo> {
        let build_url = self
            .api_root_url
            .join("version")
            .expect("Invalid quickwit URL");
        let response = self
            .client
            .get(build_url)
            .header(header::CONTENT_TYPE, "application/json")
            .send()
            .await
            .with_context(|| "Quickwit request error")?;
        if response.status() != StatusCode::OK {
            error!(resp=?response, "Quickwit API error");
            bail!(
                "http error with status code {}: {:?}",
                response.status(),
                response
            );
        }
        let data: serde_json::Value = response.json().await?;
        let build_info_json = data["build"]
            .as_object()
            .expect("build field must be an object");
        let version = build_info_json["version"]
            .as_str()
            .expect("version field must be a string")
            .to_string();
        let commit_date = build_info_json["commit_date"]
            .as_str()
            .expect("commit_date field must be a string")
            .to_string();
        let commit_hash = build_info_json["commit_hash"]
            .as_str()
            .expect("commit_hash field must be a string")
            .to_string();
        let build_target = build_info_json["build_target"]
            .as_str()
            .expect("build_target field must be a string")
            .to_string();

        Ok(super::BuildInfo {
            version,
            commit_date,
            commit_hash,
            build_target,
        })
    }
}
