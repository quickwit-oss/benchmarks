use async_trait::async_trait;

use super::{BuildInfo, IndexInfo, Sink};
use crate::source::DocumentBatch;

#[derive(Clone)]
pub struct ParseableSink {
    // uri: Uri,
    // index_id: String,
    // auth_header: HeaderValue,
}

// impl ParseableSink {
//     pub fn new(
//         uri: Uri,
//         index_id: &str,
//         username: &str,
//         password: &str,
//     ) -> Self {
//         let auth_header = crate::utils::basic_auth(username, Some(password));
//         Self {
//             uri,
//             auth_header,
//             index_id: index_id.to_string(),
//         }
//     }
// }

#[async_trait]
impl Sink for ParseableSink {
    async fn send(&self, _document_batch: &DocumentBatch) -> anyhow::Result<()> {
        todo!()
        // let mut payload = Vec::new();
        // let mut first = true;
        // payload.extend_from_slice(b"[");
        // for line in body {
        //     if first {
        //         first = false;
        //     } else {
        //         payload.extend_from_slice(b",");
        //     }

        //     payload.extend_from_slice(line.as_bytes());
        // }
        // payload.extend_from_slice(b"]");

        // let request = http::Request::builder()
        //     .method(Method::POST)
        //     .header("X-P-Stream", self.index_id.clone())
        //     .header(header::CONTENT_TYPE, "application/json")
        //     .header(header::AUTHORIZATION, self.auth_header.clone())
        //     .uri(self.uri.clone())
        //     .body(payload.into())?;
    }

    async fn commit(&self) -> anyhow::Result<()> {
        todo!()
    }

    async fn index_info(&self) -> anyhow::Result<IndexInfo> {
        todo!()
    }

    async fn build_info(&self) -> anyhow::Result<BuildInfo> {
        todo!()
    }
}
