use std::io::{BufRead as _, BufReader};

use anyhow::{bail, Context};
use async_trait::async_trait;
use fnv::FnvHashMap;
use reqwest::{header, Client, StatusCode, Url};

use super::{BuildInfo, IndexInfo, Sink};
use crate::source::DocumentBatch;

pub struct LokiSink {
    push_url: Url,
    metrics_url: Url,
    version_url: Url,
    flush_url: Url,
    client: Client,
}

impl LokiSink {
    pub fn new(host: &str) -> Self {
        debug!(host=?host, "loko client");
        let push_url =
            Url::parse(&format!("http://{host}/loki/api/v1/push")).expect("Invalid URL");
        let metrics_url =
            Url::parse(&format!("http://{host}/metrics")).expect("Invalid URL");
        let flush_url =
            Url::parse(&format!("http://{host}/flush")).expect("Invalid URL");
        let version_url =
            Url::parse(&format!("http://{host}/loki/api/v1/status/buildinfo"))
                .expect("Invalid URL");

        let client = Client::new();
        Self {
            push_url,
            metrics_url,
            version_url,
            flush_url,
            client,
        }
    }

    /// Loki Format
    /// {
    ///   "streams": [
    ///     {
    ///       "stream": {
    ///         "app": "myApp",
    ///         "environment": "production"
    ///       },
    ///       "values
    ///         ["1605117339000000000", "This is a log line", {"key": "value"}],
    ///         ["1605117340000000000", "This is another log line", {"key": "value"}],
    ///       ]
    ///     }
    ///   ]
    /// }
    async fn send_chunk(
        &self,
        values: &mut Vec<(String, serde_json::Value)>,
    ) -> anyhow::Result<()> {
        // Construct the Loki payload

        let mut buffer = String::new();
        let body = LokiBody {
            streams: vec![LokiStream {
                // Stream seems to be similar to an index id or a partition key
                stream: LokiStreamInfo { label: "benchmark" },
                values: values
                    .drain(..)
                    .map(|(ts, json)| {
                        let log_line = json.to_string();

                        buffer.clear();
                        let mut structured_metadata = FnvHashMap::default();
                        flatten_json(json, &mut buffer, &mut structured_metadata);

                        (ts, log_line, structured_metadata)
                    })
                    .collect(),
            }],
        };

        // Serialize the LokiBody to JSON
        let serialized_body = serde_json::to_string(&body)
            .with_context(|| "Failed to serialize body to JSON")
            .unwrap();

        //println!("{}", serialized_body);
        // Send the serialized JSON to Loki
        let response = self
            .client
            .post(self.push_url.clone())
            .header("Content-Type", "application/json")
            .body(serialized_body)
            .send()
            .await
            .with_context(|| "Failed to send data to Loki")?;

        match response.status() {
            StatusCode::NO_CONTENT | StatusCode::OK => Ok(()),
            _ => {
                let error_msg = response
                    .text()
                    .await
                    .unwrap_or_else(|_| "Failed to read response text".to_string());
                bail!("Failed to push logs to Loki: {}", error_msg)
            },
        }
    }
}

const MAX_CHUNK_SIZE: usize = 2 * 1024 * 1024; // 2MB limit

#[async_trait]
impl Sink for LokiSink {
    fn batch_size(&self) -> usize {
        MAX_CHUNK_SIZE
    }
    async fn send(&self, document_batch: &DocumentBatch) -> anyhow::Result<()> {
        let reader = BufReader::new(document_batch.bytes.as_slice());
        let mut values: Vec<(String, serde_json::Value)> = Vec::new();

        for line_result in reader.lines() {
            let line = line_result?;
            let doc: serde_json::Value =
                serde_json::from_str(&line).with_context(|| {
                    format!("Failed to parse document line as JSON: {}", line)
                })?;

            // Extract the timestamp from the JSON document
            let timestamp_str = doc
                .get("timestamp")
                .and_then(|ts| ts.as_str())
                .expect("no `timestamp` field found");
            // Convert timestamp to Loki's expected format
            let timestamp =
                parse_timestamp_to_nanoseconds(timestamp_str).with_context(|| {
                    format!("Failed to parse timestamp: {}", timestamp_str)
                })?;
            values.push((timestamp, doc));
        }

        self.send_chunk(&mut values).await?;

        Ok(())
    }

    async fn commit(&self) -> anyhow::Result<()> {
        let response = self
            .client
            .post(self.flush_url.clone())
            .header("Content-Type", "application/json")
            .send()
            .await
            .with_context(|| "Failed to send flush request to Loki")?;

        if response.status().is_success() {
            Ok(())
        } else {
            let status = response.status();
            let error_msg = response
                .text()
                .await
                .unwrap_or_else(|_| "Failed to read response text".to_string());
            bail!("Failed to flush Loki chunks: HTTP {} {}", status, error_msg)
        }
    }

    async fn index_info(&self) -> anyhow::Result<IndexInfo> {
        let response = self
            .client
            .get(self.metrics_url.clone())
            .send()
            .await
            .with_context(|| "Error fetching metrics for index info")?;

        if response.status() != StatusCode::OK {
            bail!(
                "Failed to fetch metrics, got status code {}: {:?}",
                response.status(),
                response.text().await?
            );
        }

        let text = response.text().await?;
        let num_docs =
            parse_number_from_metrics(&text, "loki_ingester_chunk_entries_sum");

        // # HELP loki_ingester_chunk_size_bytes Distribution of stored chunk sizes (when stored).
        let num_bytes =
            parse_number_from_metrics(&text, "loki_ingester_chunk_size_bytes_sum");

        let num_splits =
            parse_number_from_metrics(&text, "loki_ingester_chunks_stored_total");

        Ok(IndexInfo {
            num_docs,
            num_bytes,
            num_splits,
        })
    }

    async fn build_info(&self) -> anyhow::Result<BuildInfo> {
        let response = self
            .client
            .get(self.version_url.clone())
            .header(header::CONTENT_TYPE, "application/json")
            .send()
            .await
            .with_context(|| "Loki request error for build info")?;

        if response.status() != StatusCode::OK {
            bail!(
                "Error fetching build info, got status code {}: {:?}",
                response.status(),
                response.text().await?
            );
        }

        let data: serde_json::Value = response.json().await?;
        Ok(BuildInfo {
            version: data["version"].as_str().unwrap_or_default().to_string(),
            commit_date: data["buildDate"].as_str().unwrap_or_default().to_string(),
            commit_hash: data["revision"].as_str().unwrap_or_default().to_string(),
            build_target: "".to_string(),
        })
    }
}

fn parse_number_from_metrics(metrics: &str, metric_name: &str) -> u64 {
    metrics
        .lines()
        .find(|line| line.starts_with(metric_name))
        // may be scientific notation
        .map(|line| {
            let number = line.split_whitespace().nth(1).unwrap_or("0");
            number.parse::<f64>().expect(&format!("[metric {metric_name}]: Could not parse number({number:?}) from line: {line:?}")) as u64
        })
        .unwrap_or(0)
}

/// Helper function to convert rfc3339 timestamp string to a nanosecond precision string
fn parse_timestamp_to_nanoseconds(timestamp_str: &str) -> anyhow::Result<String> {
    let dt = chrono::DateTime::parse_from_rfc3339(timestamp_str)
        .with_context(|| format!("Failed to parse timestamp: {}", timestamp_str))?;
    Ok(dt
        .timestamp_nanos_opt()
        .expect("timestamp out of range")
        .to_string())
}

#[derive(serde::Serialize)]
struct LokiBody {
    streams: Vec<LokiStream>,
}

#[derive(serde::Serialize)]
struct LokiStream {
    stream: LokiStreamInfo,
    // timestamp, log line, json (structured metadata)
    values: Vec<(String, String, FnvHashMap<String, LokoValue>)>,
}

#[derive(serde::Serialize)]
struct LokiStreamInfo {
    label: &'static str,
}

use serde_json::Value;

#[derive(Debug, PartialEq)]
enum LokoValue {
    String(String),
    // Unused: Loki cannot handle numbers in structured metadata???
    #[allow(dead_code)]
    Number(f64),
}
impl serde::Serialize for LokoValue {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        match self {
            LokoValue::String(s) => serializer.serialize_str(s),
            LokoValue::Number(n) => serializer.serialize_f64(*n),
        }
    }
}

/// Loki cannot handle nested JSON, so we need to flatten it
fn flatten_json(
    value: Value,
    prefix: &mut String,
    flattened: &mut FnvHashMap<String, LokoValue>,
) {
    match value {
        Value::Object(obj) => {
            let previous_len = prefix.len(); // Remember the current length of prefix
            for (k, v) in obj {
                if !prefix.is_empty() {
                    prefix.push('.'); // Add a dot only if prefix is not empty
                }
                prefix.push_str(&k);
                flatten_json(v, prefix, flattened);
                prefix.truncate(previous_len); // Reset prefix to its previous state
            }
        },
        Value::Array(arr) => {
            let previous_len = prefix.len();
            for (i, v) in arr.into_iter().enumerate() {
                // The first element in the array will not have an index
                if i != 0 {
                    let index_str = format!("[{}]", i);
                    prefix.push_str(&index_str);
                }
                flatten_json(v, prefix, flattened);
                prefix.truncate(previous_len);
            }
        },
        _ => {
            // Convert values to strings
            // loki cannot handle numbers -.-
            let value_str = match value {
                Value::Number(_) => LokoValue::String(value.to_string()),
                Value::String(_) => {
                    LokoValue::String(value.as_str().unwrap().to_string())
                },
                _ => LokoValue::String(value.to_string()),
            };
            flattened.insert(prefix.clone(), value_str);
        },
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn test_flatten_json() {
        let json_value = json!({
            "a": 1,
            "b": {
                "c": "2",
                "d": ["3", 4]
            }
        });

        let mut flattened = FnvHashMap::default();
        let mut prefix = String::new();
        flatten_json(json_value, &mut prefix, &mut flattened);

        // Expected flattened JSON
        let expected = vec![
            ("a".to_string(), LokoValue::String("1".into())),
            ("b.c".to_string(), LokoValue::String("2".to_string())),
            ("b.d".to_string(), LokoValue::String("3".to_string())),
            ("b.d[1]".to_string(), LokoValue::String("4".into())),
        ]
        .into_iter()
        .collect::<FnvHashMap<String, LokoValue>>();

        assert_eq!(flattened, expected);
    }

    #[test]
    fn test_parse_timestamp_to_nanoseconds() {
        // Define a sample RFC3339 timestamp
        let timestamp_str = "2020-01-01T12:00:00Z"; // January 1, 2020, at 12:00:00 UTC

        // Expected nanoseconds since the UNIX epoch for the given timestamp
        // This value is calculated based on the specific timestamp above
        let expected = "1577880000000000000"; // This value should be the equivalent in nanoseconds

        // Call the function with the sample timestamp
        let result = parse_timestamp_to_nanoseconds(timestamp_str).unwrap();

        // Assert that the result is as expected
        assert_eq!(
            result, expected,
            "The parsed timestamp did not match the expected value"
        );
    }
}
