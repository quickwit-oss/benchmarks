use std::collections::VecDeque;
use std::mem;

use async_trait::async_trait;

use super::{expand_uris, DocumentBatch};
use crate::source::{BatchLineReader, Source};

/// A dataset source that produces data by streaming from a 3rd party HTTP
/// server or from local files.
///
/// This source can expand range short hand to produce multiple uris e.g.
///
/// `https://data.gharchive.org/2015-01-{01..31}-{0..23}.json.gz` to download the
/// entire month of the 2015 Jan dataset.
///
/// The source will also automatically decompress data if a uri ends with `.gz`.
pub struct UriSource {
    uris: VecDeque<String>,
}

impl UriSource {
    pub fn new(uri: &str) -> Self {
        let uris = expand_uris(uri.to_string());
        Self { uris }
    }
}

async fn send_documents_from_uri(
    uri: String,
    batch_tx: flume::Sender<anyhow::Result<DocumentBatch>>,
    last_uri: bool,
    batch_size: usize,
) -> anyhow::Result<()> {
    info!("Send data from uri: {uri:?}", uri = uri);
    let mut batch_reader = BatchLineReader::from_uri(uri, batch_size).await?;
    let mut total_bytes = 0;
    let mut bytes: Vec<u8> = Vec::new();
    while let Some(batch) = batch_reader.next_batch().await? {
        if total_bytes + batch.len() > batch_size {
            let bytes_to_send = mem::take(&mut bytes);
            batch_tx.send(Ok(DocumentBatch {
                bytes: bytes_to_send,
                last: last_uri && !batch_reader.has_next(),
            }))?;
            total_bytes = 0;
        }

        total_bytes += batch.len();
        bytes.extend_from_slice(&batch);
    }
    Ok::<_, anyhow::Error>(())
}

async fn send_documents_from_uris(
    uris: VecDeque<String>,
    batch_tx: flume::Sender<anyhow::Result<DocumentBatch>>,
    batch_size: usize,
) -> anyhow::Result<()> {
    for (uri_idx, uri) in uris.iter().enumerate() {
        let last = uri_idx == uris.len() - 1;
        if let Err(error) =
            send_documents_from_uri(uri.clone(), batch_tx.clone(), last, batch_size)
                .await
        {
            error!(uri_idx, uri = uri.as_str(), error = ?error, "Failed to send documents from uri");
            batch_tx.send(Err(error))?;
        }
    }
    Ok::<_, anyhow::Error>(())
}

#[async_trait]
impl Source for UriSource {
    async fn batch_stream(
        &self,
        batch_size: usize,
    ) -> anyhow::Result<flume::Receiver<anyhow::Result<DocumentBatch>>> {
        let (batch_tx, batch_rx) = flume::bounded(1);
        let uris = self.uris.clone();
        tokio::task::spawn(send_documents_from_uris(uris, batch_tx, batch_size));
        Ok(batch_rx)
    }
}
