use async_trait::async_trait;

use super::{BuildInfo, IndexInfo, Sink};
use crate::source::DocumentBatch;

#[derive(Clone)]
pub struct ZincSink {
    // uri: Uri,
    // auth_header: HeaderValue,
    // index_id: String,
}

// impl ZincSink {
//     pub fn new(
//         uri: Uri,
//         username: &str,
//         password: &str,
//         index_id: &str,
//     ) -> Self {
//         let auth_header = crate::utils::basic_auth(username, Some(password));
//         let path_uri = Uri::builder()
//             .path_and_query(uri.path_and_query().unwrap().clone())
//             .build()
//             .unwrap();
//         Self {
//             uri,
//             auth_header,
//             index_id: index_id.to_string(),
//         }
//     }
// }

#[async_trait]
impl Sink for ZincSink {
    async fn send(&self, _document_batch: &DocumentBatch) -> anyhow::Result<()> {
        todo!()
        // let mut payload = Vec::new();
        // for line in body {
        //     writeln!(
        //         &mut payload,
        //         r#"{{"create": {{ "_index": "{}"}}}}"#,
        //         self.index_id
        //     )?;
        //     payload.extend_from_slice(line.as_bytes());
        //     payload.extend_from_slice(b"\n");
        // }

        // let request = http::Request::builder()
        //     .method(Method::POST)
        //     .header(header::AUTHORIZATION, self.auth_header.clone())
        //     .header(header::CONTENT_TYPE, "application/json")
        //     .header(header::CONTENT_LENGTH, payload.len().to_string())
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
