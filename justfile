ci:
    python3 tests/runner_workflow_contract.py
    cargo --locked fmt --check
    cargo --locked clippy --all-targets --all-features -- -D warnings
    cargo --locked test --all-features
    cargo --locked doc --no-deps
    cargo --locked package --list --allow-dirty
