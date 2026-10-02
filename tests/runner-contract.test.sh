#!/usr/bin/env bash
set -euo pipefail

workflow="$(cd "$(dirname "$0")/.." && pwd)/.github/workflows/ci.yml"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

extract_run() {
  awk -v wanted="$1" '
    $0 == "      - name: " wanted { step = 1; next }
    $0 == "        name: " wanted { step = 1; next }
    step && $0 == "        run: |" { run = 1; next }
    step && /^        run: / { sub(/^        run: /, ""); print; exit }
    step && /^      - / { exit }
    run { sub(/^          /, ""); print }
  ' "$workflow"
}

guard="$(extract_run 'Verify host-prepared PR checkout')"
archive="$(extract_run 'Create source archive')"
route="$(extract_run 'Select trusted runner route')"
test -n "$guard"
test -n "$archive"
test -n "$route"
grep -Fq 'persist-credentials: false' "$workflow"
grep -Fq 'github.sha' "$workflow"
grep -Fq "group: \${{ github.event_name == 'pull_request' && format('pr-{0}-{1}', github.repository_id, github.event.pull_request.number) || format('ref-{0}-{1}', github.repository_id, github.ref) }}" "$workflow"
grep -Fq 'cancel-in-progress: false' "$workflow"
if grep -Fq 'pr-%s-%s-run-%s-attempt-%s' "$workflow"; then
  echo 'runner label must remain stable across workflow runs for one PR' >&2
  exit 1
fi
if grep -Fq 'runs-on: [self-hosted, linux, x64, generic, group:' "$workflow"; then
  echo 'workflow uses the old runner group syntax' >&2
  exit 1
fi
if grep -Fq 'always()' "$workflow"; then
  echo 'workflow aggregate must tolerate failures without overriding cancellation' >&2
  exit 1
fi
if grep -Fq 'cancel-in-progress: true' "$workflow"; then
  echo 'workflow may cancel required CI runs' >&2
  exit 1
fi
if grep -Eq '^  package:' "$workflow" || grep -Eq 'cargo package' "$workflow"; then
  echo 'registry package publishing check is intentionally skipped' >&2
  exit 1
fi
grep -Fq 'needs: [prepare, fmt, clippy, cargo-test, doc]' "$workflow"
grep -Fq 'cargo doc --no-deps' "$workflow"
if grep -Eq 'cargo package' "$(dirname "$workflow")/../../justfile"; then
  echo 'local CI must skip cargo package while its git dependency is unpublished' >&2
  exit 1
fi

# Internal PR jobs must all consume the one serialized prepared checkout. Only
# the prepare job may call checkout, and only for forks or non-PR events.
test "$(grep -Fc 'uses: actions/checkout@v4' "$workflow")" -eq 1
grep -Fq '      - uses: actions/checkout@v4' "$workflow"
checkout_if="$(awk '
  /^      - uses: actions\/checkout@v4$/ { in_checkout = 1; next }
  in_checkout && /^        if: / { sub(/^        if: /, ""); print; exit }
  in_checkout && /^      - / { exit }
' "$workflow")"
test "$checkout_if" = "\${{ github.event_name != 'pull_request' || github.event.pull_request.head.repo.full_name != github.repository }}"
select_route() {
  event="$1"
  head="$2"
  run_id="$3"
  attempt="$4"
  output="$tmp/route-output"
  rm -f "$output"
  EVENT_NAME="$event" \
    HEAD_REPOSITORY="$head" \
    BASE_REPOSITORY=owner/repo \
    REPOSITORY_ID=123 \
    PR_NUMBER=4 \
    RUN_ID="$run_id" \
    RUN_ATTEMPT="$attempt" \
    GITHUB_OUTPUT="$output" \
    bash -e -c "$route"
  cat "$output"
}
test "$(select_route pull_request owner/repo 42 2)" = 'runs_on=["self-hosted","linux","x64","generic","pr-123-4"]'
test "$(select_route pull_request owner/repo 99 1)" = 'runs_on=["self-hosted","linux","x64","generic","pr-123-4"]'
test "$(select_route pull_request fork/repo 42 2)" = 'runs_on="ubuntu-latest"'
test "$(select_route push owner/repo 42 2)" = 'runs_on=["self-hosted","linux","x64","generic"]'
awk '
  function check_job() {
    if (job != "" && job != "prepare" && !has_prepare) {
      print "job does not depend on prepare: " job > "/dev/stderr"; failed = 1
    }
  }
  /^jobs:$/ { in_jobs = 1; next }
  in_jobs && /^  [A-Za-z0-9_-]+:$/ { check_job(); job = $1; sub(/:$/, "", job); has_prepare = 0; next }
  in_jobs && /^    needs:/ && $0 ~ /prepare/ { has_prepare = 1 }
  in_jobs && job != "prepare" && /^      - uses: actions\/checkout@/ {
    print "gate checks out a second source tree: " job > "/dev/stderr"; failed = 1
  }
  END { check_job(); exit failed }
' "$workflow"
awk '
  /^      - uses: actions\/download-artifact@/ { artifact = 1; next }
  artifact && /^        if: / {
    if ($0 !~ /github.event_name != '\''pull_request'\''/ || $0 !~ /head.repo.full_name != github.repository/) {
      print "artifact restore is not fork/non-PR scoped" > "/dev/stderr"; exit 1
    }
    artifact = 0
  }
  END { if (artifact) { print "artifact restore has no event condition" > "/dev/stderr"; exit 1 } }
' "$workflow"

for gate in fmt clippy cargo-test doc; do
  block="$(awk -v wanted="$gate" '
    /^  [A-Za-z0-9_-]+:$/ {
      if (job == wanted) exit
      job = $1; sub(/:$/, "", job)
    }
    job == wanted { print }
  ' "$workflow")"
  printf '%s\n' "$block" | grep -Fq 'needs: prepare'
  if printf '%s\n' "$block" | grep -Fq 'actions/checkout@'; then
    echo "gate checks out a second source tree: $gate" >&2
    exit 1
  fi
  if printf '%s\n' "$block" | grep -Fq 'working-directory:'; then
    echo "gate does not use the root brokered workspace: $gate" >&2
    exit 1
  fi
  target_suffix="$gate"
  if [[ "$gate" == cargo-test ]]; then
    target_suffix="test"
  fi
  printf '%s\n' "$block" | grep -Fq "cargo-target-%s-%s-$target_suffix"
  test "$(printf '%s\n' "$block" | grep -Fc 'uses: actions/download-artifact@v8')" -eq 1
  test "$(printf '%s\n' "$block" | grep -Fc "tar -xf \"\$RUNNER_TEMP/source/source.tar.gz\" -C \"\$GITHUB_WORKSPACE\"")" -eq 1
done

repo="$tmp/repo"
mkdir "$repo"
git -C "$repo" init -q
git -C "$repo" config user.name 'Runner contract test'
git -C "$repo" config user.email 'runner-contract@example.invalid'
printf 'merge source\n' > "$repo/source.txt"
git -C "$repo" add source.txt
git -C "$repo" commit -qm 'merge commit'
merge_sha="$(git -C "$repo" rev-parse HEAD)"
printf 'PR head source\n' > "$repo/source.txt"
git -C "$repo" commit -qam 'PR head commit'
head_sha="$(git -C "$repo" rev-parse HEAD)"
test "$merge_sha" != "$head_sha"
git -C "$repo" worktree add -q --detach "$tmp/pr-worktree" "$head_sha"
ln -s "$tmp/pr-worktree" "$tmp/brokered-workspace"
git clone -q "$repo" "$tmp/unrelated-checkout"

(
  cd "$tmp/brokered-workspace"
  GITHUB_EVENT_NAME=pull_request GITHUB_WORKSPACE="$tmp/brokered-workspace" GITHUB_SHA="$head_sha" bash -e -c "$guard"
)
if (
  cd "$tmp/brokered-workspace"
  GITHUB_EVENT_NAME=pull_request GITHUB_WORKSPACE="$tmp/brokered-workspace" GITHUB_SHA="$merge_sha" bash -e -c "$guard"
); then
  echo 'same-repo guard accepted a checkout at the wrong commit' >&2
  exit 1
fi
if (
  cd "$tmp/unrelated-checkout"
  GITHUB_EVENT_NAME=pull_request GITHUB_WORKSPACE="$tmp/brokered-workspace" GITHUB_SHA="$head_sha" bash -e -c "$guard"
); then
  echo 'same-repo guard accepted an unrelated checkout at the right commit' >&2
  exit 1
fi

mkdir "$tmp/runner-temp"
printf 'untracked secret-shaped fixture\n' > "$repo/untracked-secret.txt"
(
  cd "$repo"
  GITHUB_SHA="$merge_sha" RUNNER_TEMP="$tmp/runner-temp" bash -e -c "$archive"
)
tar -tzf "$tmp/runner-temp/source.tar.gz" > "$tmp/archive-files.txt"
grep -Fxq source.txt "$tmp/archive-files.txt"
if grep -Fq '.git' "$tmp/archive-files.txt"; then
  echo 'source archive contains Git metadata' >&2
  exit 1
fi
if grep -Fq untracked-secret.txt "$tmp/archive-files.txt"; then
  echo 'source archive contains untracked content' >&2
  exit 1
fi
mkdir "$tmp/restore"
tar -xzf "$tmp/runner-temp/source.tar.gz" -C "$tmp/restore"
grep -Fxq 'merge source' "$tmp/restore/source.txt"
echo 'runner workflow contract passed'
