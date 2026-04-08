# crispy-stream-checker

Concurrent IPTV stream validation crate.

## Status

Extracted from CrispyTivi. Intended as a reusable Rust crate for batch validation of IPTV stream URLs.

## What This Crate Provides

- bounded-concurrency stream checking
- HTTP HEAD / GET fallback validation
- optional media probing integration
- batch reports and categorized results
- deduplication and checkpoint helpers
- URL normalization helpers

## Installation

```toml
[dependencies]
crispy-stream-checker = "0.1"
```

## Quick Start

```rust
use crispy_stream_checker::{check_stream, CheckOptions};

# async fn demo() {
let opts = CheckOptions::default();
let result = check_stream("http://example.com/stream.m3u8", &opts).await;
println!("available: {}", result.info.available);
# }
```

## Relationship To Other Crates

- uses `crispy-iptv-types`
- can use `crispy-media-probe` for deeper validation/probing

## Primary Use Cases

- playlist QA
- provider validation pipelines
- dead/alive URL audits
- automated IPTV catalog checking

## Caveats

- public release should clearly document network timeouts, probing cost, and runtime dependencies inherited from media probing
