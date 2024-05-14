use std::collections::VecDeque;
use std::ops::Range;
use std::path::Path;
use std::{io, mem};

use anyhow::bail;
use async_compression::tokio::bufread::GzipDecoder;
use async_trait::async_trait;
use bytes::Bytes;
use futures_util::TryStreamExt;
use once_cell::sync::Lazy;
use regex::Regex;
use tokio::io::{AsyncBufReadExt, AsyncRead, BufReader};
use tokio_util::compat::FuturesAsyncReadCompatExt;

mod http;

pub use self::http::UriSource;

/// The maximum size of the body to be sent as a single request. (5MB)
pub(crate) const DEFAULT_MAX_BODY_SIZE: usize = 5_000_000;

static URI_EXPAND_PATTERN: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(\{\d+..\d+})").unwrap());

#[derive(Default)]
pub struct DocumentBatch {
    pub bytes: Vec<u8>,
    pub last: bool,
}

#[async_trait]
pub trait Source: Sync + Send + 'static {
    /// Creates a new data source which produces request bodies.
    async fn batch_stream(
        &self,
        batch_size: usize,
    ) -> anyhow::Result<flume::Receiver<anyhow::Result<DocumentBatch>>>;

    fn uris(&self) -> Vec<String>;
}

pub(crate) struct BatchLineReader {
    buf_reader: BufReader<Box<dyn AsyncRead + Send + Sync + Unpin>>,
    buffer: Vec<u8>,
    alloc_num_bytes: usize,
    max_batch_num_bytes: usize,
    num_lines: usize,
    has_next: bool,
}

impl BatchLineReader {
    pub async fn from_uri(
        uri: String,
        max_batch_num_bytes: usize,
    ) -> anyhow::Result<Self> {
        if uri.starts_with("http") {
            Self::from_http_uri(uri, max_batch_num_bytes).await
        } else {
            Self::from_file(uri, max_batch_num_bytes).await
        }
    }

    pub async fn from_http_uri(
        uri: String,
        max_batch_num_bytes: usize,
    ) -> anyhow::Result<Self> {
        let decompress_gzip = uri.ends_with(".gz");
        let client = reqwest::Client::new();
        let response = client.get(uri.clone()).send().await?;
        if response.status() != reqwest::StatusCode::OK {
            bail!(
                "http error with status code {}: {:?}",
                response.status(),
                response
            );
        }
        let stream = response
            .bytes_stream()
            .map_err(|e| io::Error::new(io::ErrorKind::Other, e))
            .into_async_read()
            .compat();
        let reader = if decompress_gzip {
            Box::new(GzipDecoder::new(BufReader::new(stream)))
                as Box<dyn AsyncRead + Unpin + Send + Sync>
        } else {
            Box::new(stream) as Box<dyn AsyncRead + Unpin + Send + Sync>
        };
        Ok(Self::new(reader, max_batch_num_bytes))
    }

    pub async fn from_file(
        uri: String,
        max_batch_num_bytes: usize,
    ) -> anyhow::Result<Self> {
        let decompress_gzip = uri.ends_with(".gz");
        let file = tokio::fs::File::open(&Path::new(&uri)).await?;
        let reader = if decompress_gzip {
            Box::new(GzipDecoder::new(BufReader::new(file)))
                as Box<dyn AsyncRead + Unpin + Send + Sync>
        } else {
            Box::new(file) as Box<dyn AsyncRead + Unpin + Send + Sync>
        };
        Ok(Self::new(reader, max_batch_num_bytes))
    }

    pub fn new(
        reader: Box<dyn AsyncRead + Send + Sync + Unpin>,
        max_batch_num_bytes: usize,
    ) -> Self {
        let alloc_num_bytes = max_batch_num_bytes + 100 * 1024; // Add 100 KiB headroom to avoid reallocation.
        Self {
            buf_reader: BufReader::new(reader),
            buffer: Vec::with_capacity(alloc_num_bytes),
            alloc_num_bytes,
            max_batch_num_bytes,
            num_lines: 0,
            has_next: true,
        }
    }

    pub async fn next_batch(&mut self) -> io::Result<Option<Bytes>> {
        loop {
            let line_num_bytes =
                self.buf_reader.read_until(b'\n', &mut self.buffer).await?;

            if line_num_bytes > self.max_batch_num_bytes {
                warn!(
                    "Skipping line {}, which exceeds the maximum allowed content length ({} vs. \
                     {} bytes).",
                    self.num_lines + 1,
                    line_num_bytes,
                    self.max_batch_num_bytes
                );
                let new_len = self.buffer.len() - line_num_bytes;
                self.buffer.truncate(new_len);
                continue;
            }
            if self.buffer.len() > self.max_batch_num_bytes {
                let mut new_buffer = Vec::with_capacity(self.alloc_num_bytes);
                let new_len = self.buffer.len() - line_num_bytes;
                new_buffer.extend_from_slice(&self.buffer[new_len..]);
                self.buffer.truncate(new_len);
                let batch = mem::replace(&mut self.buffer, new_buffer);
                return Ok(Some(Bytes::from(batch)));
            }
            if line_num_bytes == 0 {
                self.has_next = false;
                if self.buffer.is_empty() {
                    return Ok(None);
                }
                let batch = mem::take(&mut self.buffer);
                return Ok(Some(Bytes::from(batch)));
            }
            self.num_lines += 1;
        }
    }

    /// Returns whether there is still data available
    ///
    /// This can spuriously return `true` when there was no data
    /// to send at all.
    pub fn has_next(&self) -> bool {
        self.has_next
    }
}

pub struct RangeExpand<'a> {
    replace_str: &'a str,
    range: Range<usize>,
    zero_pad_by: usize,
}

/// Expands a uri with the range syntax into the exported/expected uris.
fn expand_uris(uri: String) -> VecDeque<String> {
    let mut total_variants = 0;
    let mut ranges = Vec::new();
    for capture in URI_EXPAND_PATTERN.captures_iter(&uri) {
        let cap = capture.get(0).unwrap();
        let replace_str = cap.as_str();

        let range_str = replace_str.trim_matches('{').trim_matches('}');
        let (start, end) = range_str.split_once("..").unwrap();
        let pad_start = start.starts_with('0');
        let zero_pad_by = if pad_start { start.len() } else { 0 };
        let start = start.parse::<usize>().unwrap();
        let end = end.parse::<usize>().unwrap();
        let range = start..end;

        total_variants += range.len();
        ranges.push(RangeExpand {
            replace_str,
            range,
            zero_pad_by,
        })
    }

    let mut uris = VecDeque::with_capacity(total_variants);
    uris.push_back(uri.clone());

    // This is likely horrifically un-optimised, but it does work for convenience.
    for range in ranges {
        for _ in 0..uris.len() {
            let uri = uris.pop_front().unwrap();

            for i in range.range.clone() {
                let value = format!("{i:0>pad_by$}", pad_by = range.zero_pad_by);
                let populated_uri = uri.replacen(range.replace_str, &value, 1);
                uris.push_back(populated_uri);
            }
        }
    }

    uris
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_uri_expand() {
        let uri = "http://localhost:3000/{0..5}.json";
        let uris = expand_uris(uri.to_string());

        assert_eq!(
            uris,
            vec![
                "http://localhost:3000/0.json",
                "http://localhost:3000/1.json",
                "http://localhost:3000/2.json",
                "http://localhost:3000/3.json",
                "http://localhost:3000/4.json",
            ]
        )
    }
}
