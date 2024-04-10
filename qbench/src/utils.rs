// use http::HeaderValue;

// pub fn basic_auth<U, P>(username: U, password: Option<P>) -> HeaderValue
// where
//     U: std::fmt::Display,
//     P: std::fmt::Display,
// {
//     use std::io::Write;

//     use base64::prelude::BASE64_STANDARD;
//     use base64::write::EncoderWriter;

//     let mut buf = b"Basic ".to_vec();
//     {
//         let mut encoder = EncoderWriter::new(&mut buf, &BASE64_STANDARD);
//         let _ = write!(encoder, "{username}:");
//         if let Some(password) = password {
//             let _ = write!(encoder, "{password}");
//         }
//     }
//     let mut header =
//         HeaderValue::from_bytes(&buf).expect("base64 is always valid HeaderValue");
//     header.set_sensitive(true);
//     header
// }
