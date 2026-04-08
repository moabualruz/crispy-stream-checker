# crispy-stream-checker

Concurrent IPTV stream validation crate.

## What This Crate Is

`crispy-stream-checker` validates IPTV stream URLs at scale with bounded concurrency. It supports simple liveness checks and deeper inspection when used together with `crispy-media-probe`.

## What It Provides

- single-stream validation
- bulk validation with reports
- optional deeper media inspection
- categorization of alive/dead/problematic streams
- deduplication and checkpoint helpers
- URL normalization utilities

## Installation

```toml
[dependencies]
crispy-stream-checker = "0.1.1"
```

MSRV: Rust `1.85`

## Quick Start

```rust
use crispy_stream_checker::{check_stream, CheckOptions};

# async fn demo() {
let opts = CheckOptions::default();
let result = check_stream("http://example.com/stream.m3u8", &opts).await;
println!("available: {}", result.info.available);
# }
```

## Typical Uses

- provider QA pipelines
- dead-link detection
- validating imported playlists before they hit production

## Current Limitations

- deep checks may inherit runtime/tooling assumptions from `crispy-media-probe`
- network-heavy validation can be expensive; callers should choose concurrency and timeout settings intentionally
- this crate does not perform persistence or queueing for you

## License

See `LICENSE.md` and `NOTICE.md`.
