use std::io::Write;

use anyhow::{bail, Context};
use async_trait::async_trait;
use http::{header, StatusCode};
use reqwest::{Client, Url};
use tokio::io::{AsyncBufReadExt, BufReader};

use super::{BuildInfo, IndexInfo, Sink};
use crate::source::DocumentBatch;

#[derive(Clone)]
pub struct ElasticsearchSink {
    api_root_url: Url,
    index_url: Url,
    ingest_url: Url,
    client: Client,
    merge: bool,
}

impl ElasticsearchSink {
    pub fn new(host: &str, index_id: &str, merge: bool) -> Self {
        debug!(host=?host, index_id=?index_id, "elasticsearch client");
        let api_root_url = Url::parse(&format!("http://{host}/", host = host))
            .expect("Invalid elastic URL");
        let index_url = Url::parse(&format!(
            "http://{host}/{index_id}/",
            host = host,
            index_id = index_id
        ))
        .expect("Invalid elastic URL");
        let ingest_url = Url::parse(&format!("http://{host}/{index_id}/_bulk"))
            .expect("Invalid elastic URL");
        let client = Client::new();
        Self {
            api_root_url,
            index_url,
            ingest_url,
            client,
            merge,
        }
    }
}

#[async_trait]
impl Sink for ElasticsearchSink {
    async fn send(&self, document_batch: &DocumentBatch) -> anyhow::Result<()> {
        let mut payload = Vec::new();
        let mut lines = BufReader::new(document_batch.bytes.as_slice()).lines();
        while let Ok(Some(line)) = lines.next_line().await {
            if line.is_empty() {
                continue;
            }
            writeln!(&mut payload, r#"{{"create": {{  }}}}"#,)?;
            payload.extend_from_slice(line.as_bytes());
            payload.extend_from_slice(b"\n");
        }

        let response = self
            .client
            .post(self.ingest_url.clone())
            .header(header::CONTENT_TYPE, "application/json")
            .header(header::CONTENT_LENGTH, payload.len().to_string())
            .body(payload)
            .send()
            .await
            .with_context(|| "elasticsearch request error")?;
        if response.status() != StatusCode::OK {
            error!(resp=?response, "Elasticsearch bulk request error");
            bail!(
                "Error on bulk request, got status code {}: {:?}",
                response.status(),
                response
            );
        }
        let data: serde_json::Value = response.json().await?;
        if let Some(errors) = data.get("errors") {
            let has_errors = errors.as_bool().expect("errors field must be a boolean");
            if has_errors {
                error!(data=?data, "Errors contained in bulk response");
                bail!("Error on bulk request");
            }
        }
        Ok(())
    }

    async fn commit(&self) -> anyhow::Result<()> {
        info!("Forcing commit to elasticsearch...");
        let refresh_url = self
            .index_url
            .join("_refresh")
            .expect("Invalid refresh URL");
        let response = self
            .client
            .post(refresh_url)
            .header(header::CONTENT_TYPE, "application/json")
            .body(Vec::new())
            .send()
            .await
            .with_context(|| "elasticsearch request error")?;
        if response.status() != StatusCode::OK {
            bail!(
                "Error on refresh, got status code {}: {:?}",
                response.status(),
                response
            );
        }
        if self.merge {
            info!("Force merge segments into one...");
            let force_merge_url = self
                .index_url
                .join("_forcemerge")
                .expect("Invalid force merge URL");
            let response = self
                .client
                .post(force_merge_url)
                .header(header::CONTENT_TYPE, "application/json")
                .body(Vec::new())
                .query(&[("max_num_segments", "1")])
                .send()
                .await
                .with_context(|| "elasticsearch request error")?;
            if response.status() != StatusCode::OK {
                bail!(
                    "Error on refresh, got status code {}: {:?}",
                    response.status(),
                    response
                );
            }
        }
        Ok(())
    }

    async fn index_info(&self) -> anyhow::Result<IndexInfo> {
        info!("Fetching index info from elasticsearch  commit to elasticsearch...");
        let describe_url = self.index_url.join("_stats").unwrap();
        let response = self
            .client
            .get(describe_url)
            .header(header::CONTENT_TYPE, "application/json")
            .send()
            .await
            .with_context(|| "Elasticsearch request error")?;
        if response.status() != StatusCode::OK {
            error!(resp=?response, "Elasticsearch API error");
            bail!(
                "http error with status code {}: {:?}",
                response.status(),
                response
            );
        }

        let data: serde_json::Value = response.json().await?;
        let total = &data["_all"]["total"];
        let num_docs = total["docs"]["count"]
            .as_u64()
            .expect("docs count field must be a u64");
        let num_splits = total["segments"]["count"]
            .as_u64()
            .expect("segments count field must be a u64");
        let num_bytes = total["store"]["size_in_bytes"]
            .as_u64()
            .expect("segments memory_in_bytes field must be a u64");

        Ok(IndexInfo {
            num_docs,
            num_bytes,
            num_splits,
        })
    }

    async fn build_info(&self) -> anyhow::Result<BuildInfo> {
        let response = self
            .client
            .get(self.api_root_url.clone())
            .header(header::CONTENT_TYPE, "application/json")
            .send()
            .await
            .with_context(|| "Elasticsearch request error")?;
        if response.status() != StatusCode::OK {
            error!(resp=?response, "Elasticsearch API error");
            bail!(
                "http error with status code {}: {:?}",
                response.status(),
                response
            );
        }
        let data: serde_json::Value = response.json().await?;
        let version = data["version"]["number"]
            .as_str()
            .expect("version field must be a string")
            .to_string();
        let build_hash = data["version"]["build_hash"]
            .as_str()
            .expect("build_hash field must be a string")
            .to_string();
        let build_type = data["version"]["build_type"]
            .as_str()
            .expect("build_type field must be a string")
            .to_string();
        Ok(BuildInfo {
            version,
            commit_date: "".to_string(),
            commit_hash: build_hash,
            build_target: build_type,
        })
    }
}
