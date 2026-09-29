ci:
    python3 tests/runner_workflow_contract.py
    cargo --locked fmt --check
    cargo --locked clippy --all-targets --all-features -- -D warnings
    cargo --locked test --all-features
    cargo --locked doc --no-deps
    # full cargo package is blocked on crispy-media-probe 0.1.2 being published: moabualruz/crispy-stream-checker#3
    cargo --locked package --list --allow-dirty
