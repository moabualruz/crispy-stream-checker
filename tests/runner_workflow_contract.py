from pathlib import Path
import io
import os
import re
import subprocess
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
GATES = ("fmt", "clippy", "cargo-test", "doc", "package")
JUSTFILE = ROOT / "justfile"


def job_block(workflow, name):
    jobs = workflow.split("jobs:\n", 1)[1]
    match = re.search(
        rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  [a-z0-9_-]+:\n|\Z)",
        jobs,
    )
    if not match:
        raise AssertionError(f"missing workflow job: {name}")
    return match.group(1)


def named_step_script(workflow, job, name):
    lines = job_block(workflow, job).splitlines()
    name_line = next(
        index for index, line in enumerate(lines)
        if line in (f"        name: {name}", f"      - name: {name}")
    )
    run_line = next(
        index for index in range(name_line + 1, len(lines))
        if lines[index].startswith("        run:")
    )
    if lines[run_line] != "        run: |":
        return lines[run_line].removeprefix("        run: ")
    script = []
    for line in lines[run_line + 1:]:
        if line and not line.startswith("          "):
            break
        script.append(line[10:] if line.startswith("          ") else "")
    return "\n".join(script)


def named_step_block(workflow, job, name):
    lines = job_block(workflow, job).splitlines()
    start = lines.index(f"      - name: {name}")
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("      - ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def action_step_block(workflow, job, action):
    lines = job_block(workflow, job).splitlines()
    start = lines.index(f"      - uses: {action}")
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("      - ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def cargo_step_script(workflow, job):
    line = next(
        line for line in job_block(workflow, job).splitlines()
        if line.startswith('      - run: cd "$PREPARED_SOURCE_DIR" && cargo ')
    )
    return line.removeprefix("      - run: ")


def archive_command(workflow):
    lines = workflow.splitlines()
    for index, line in enumerate(lines):
        if line != "      - name: Prepare pinned source and lockfile":
            continue
        run_index = next(
            (i for i in range(index + 1, len(lines)) if lines[i] == "        run: |"),
            None,
        )
        if run_index is None:
            break
        body = []
        for line in lines[run_index + 1:]:
            if line.startswith("      - ") or (line.startswith("        ") and not line.startswith("          ")):
                break
            if line.startswith("          "):
                body.append(line[10:])
        return "\n".join(body)
    raise AssertionError("missing pinned source preparation step")


class RunnerWorkflowContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text()

    def test_parallel_gates_share_one_prepared_source_and_isolate_writes(self):
        self.assertEqual(
            re.findall(r"(?m)^  ([a-z][a-z0-9_-]*):$", self.workflow.split("jobs:\n", 1)[1]),
            ["prepare", *GATES, "test"],
        )
        prepare = job_block(self.workflow, "prepare")
        self.assertNotIn("actions/checkout@", prepare)
        self.assertIn('git -C "$repository" fetch --no-tags --depth=1 "$SOURCE_URL" "$GITHUB_SHA"', archive_command(self.workflow))
        self.assertIn('test "$fetched_sha" = "$GITHUB_SHA"', archive_command(self.workflow))
        self.assertIn('git -C "$repository" archive --format=tar "$fetched_sha"', archive_command(self.workflow))
        prepare_source = archive_command(self.workflow)
        contract_check = "python3 tests/runner_workflow_contract.py"
        source_archive = 'git -C "$repository" archive --format=tar "$fetched_sha"'
        self.assertNotIn("Verify runner workflow contract", prepare)
        self.assertLess(prepare_source.index(source_archive), prepare_source.index(contract_check))
        self.assertLess(prepare_source.index('cd "$source"'), prepare_source.index(contract_check))
        self.assertLess(prepare_source.index(contract_check), prepare_source.index("cargo --locked metadata"))
        self.assertLess(prepare_source.index('unset GITHUB_TOKEN auth'), prepare_source.index(contract_check))
        self.assertLess(prepare_source.index("cargo --locked metadata"), prepare_source.index('tar -cf - -C "$source" .'))
        self.assertIn("export GIT_CONFIG_COUNT=1", prepare_source)
        self.assertIn("export GIT_CONFIG_KEY_0=http.extraheader", prepare_source)
        self.assertIn('export GIT_CONFIG_VALUE_0="$auth"', prepare_source)
        self.assertIn(
            'git -C "$repository" fetch --no-tags --depth=1 "$SOURCE_URL" "$GITHUB_SHA"',
            prepare_source,
        )
        self.assertNotIn(' -c http.extraheader="$auth"', prepare_source)
        self.assertIn(
            "unset GITHUB_TOKEN auth GIT_CONFIG_VALUE_0 GIT_CONFIG_KEY_0 GIT_CONFIG_COUNT",
            prepare_source,
        )

        artifact = "source-${{ github.run_id }}-${{ github.run_attempt }}"
        self.assertNotIn("actions/checkout@", prepare)
        for gate in GATES:
            block = job_block(self.workflow, gate)
            suffix = "test" if gate == "cargo-test" else gate
            self.assertIn("needs: prepare", block, gate)
            self.assertIn("needs.prepare.outputs.runs_on", block, gate)
            self.assertIn(artifact, block, gate)
            self.assertIn(f"cargo-target-%s-%s-{suffix}", block, gate)
            download = action_step_block(self.workflow, gate, "actions/download-artifact@v8")
            self.assertNotIn("if:", download, gate)
            self.assertIn(f"artifact-${{{{ github.run_id }}}}-${{{{ github.run_attempt }}}}-{suffix}", download, gate)
            unpack = named_step_block(self.workflow, gate, "Unpack prepared source")
            self.assertNotIn("if:", unpack, gate)
            self.assertIn('source_dir="$(mktemp -d "$RUNNER_TEMP/prepared-source-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT-' + suffix + '.XXXXXX")"', unpack, gate)
            self.assertIn('tar -xf "$RUNNER_TEMP/artifact-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT-' + suffix + '/source-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT.tar.gz" -C "$source_dir"', unpack, gate)
            self.assertIn("PREPARED_SOURCE_DIR=%s", unpack, gate)
            cargo_command = cargo_step_script(self.workflow, gate)
            self.assertIn('cd "$PREPARED_SOURCE_DIR" && cargo --locked ', cargo_command, gate)
            self.assertNotIn("GITHUB_WORKSPACE", unpack, gate)
            self.assertNotIn("working-directory:", block, gate)
            self.assertNotIn("actions/checkout@", block, gate)

        self.assertIn(
            'cd "$PREPARED_SOURCE_DIR" && cargo --locked package --list',
            cargo_step_script(self.workflow, "package"),
        )
        summary = job_block(self.workflow, "test")
        self.assertIn("if: ${{ always() }}", summary)
        self.assertIn("needs: [prepare, fmt, clippy, cargo-test, doc, package]", summary)
        self.assertIn("runs-on: ubuntu-latest", summary)
        self.assertNotIn("needs.prepare.outputs.runs_on ||", summary)
        for result in ("PREPARE_RESULT", "FMT_RESULT", "CLIPPY_RESULT", "CARGO_TEST_RESULT", "DOC_RESULT", "PACKAGE_RESULT"):
            self.assertIn(result, summary)
        self.assertIn('[[ "$result" != success ]]', summary)
        self.assertNotIn("cargo package because crispy-media-probe 0.1.2 is not published on crates.io", self.workflow)
        self.assertNotIn("generate-lockfile", self.workflow)
        self.assertIn("cancel-in-progress: false", self.workflow)
        self.assertIn("format('pr-{0}-{1}', github.repository_id, github.event.pull_request.number)", self.workflow)
        self.assertEqual(
            [line.strip() for line in JUSTFILE.read_text().splitlines() if line.startswith("    ")],
            [
                "python3 tests/runner_workflow_contract.py",
                "cargo --locked fmt --check",
                "cargo --locked clippy --all-targets --all-features -- -D warnings",
                "cargo --locked test --all-features",
                "cargo --locked doc --no-deps",
                "cargo --locked package --list --allow-dirty",
            ],
        )

    def test_runner_route_separates_internal_pr_fork_and_push(self):
        route = named_step_script(self.workflow, "prepare", "Select runner route")
        prepare = job_block(self.workflow, "prepare")
        runner_expression = next(line for line in prepare.splitlines() if line.startswith("    runs-on:"))
        self.assertIn('pr-{0}-{1}', runner_expression)
        self.assertNotIn("github.run_id", runner_expression)
        self.assertNotIn("github.run_attempt", runner_expression)
        with tempfile.TemporaryDirectory() as temp:
            def select(event, head, base, run_id="42", run_attempt="2"):
                output = Path(temp, "github-output")
                output.unlink(missing_ok=True)
                env = dict(os.environ)
                env.update(
                    EVENT_NAME=event,
                    HEAD_REPOSITORY=head,
                    BASE_REPOSITORY=base,
                    REPOSITORY_ID="123",
                    PR_NUMBER="4",
                    RUN_ID=run_id,
                    RUN_ATTEMPT=run_attempt,
                    GITHUB_OUTPUT=str(output),
                )
                subprocess.run(["bash", "-e", "-c", route], env=env, check=True)
                return output.read_text()

            expected_pr_route = 'runs_on=["self-hosted","linux","x64","generic","pr-123-4"]\n'
            self.assertEqual(select("pull_request", "owner/repo", "owner/repo"), expected_pr_route)
            self.assertEqual(select("pull_request", "owner/repo", "owner/repo", "99", "3"), expected_pr_route)
            self.assertEqual(
                select("pull_request", "fork/repo", "owner/repo"),
                'runs_on="ubuntu-latest"\n',
            )
            self.assertEqual(
                select("push", "owner/repo", "owner/repo"),
                'runs_on=["self-hosted","linux","x64","generic"]\n',
            )

    def test_cancelled_prerequisite_runs_aggregate_and_fails_closed(self):
        summary = job_block(self.workflow, "test")
        lines = summary.splitlines()
        run_index = lines.index("        run: |", lines.index("      - name: Preserve required test status"))
        script = []
        for line in lines[run_index + 1:]:
            if line and not line.startswith("      "):
                break
            script.append(line[6:] if line.startswith("      ") else "")
        env = dict(os.environ)
        env.update({name: "success" for name in ("PREPARE_RESULT", "FMT_RESULT", "CLIPPY_RESULT", "CARGO_TEST_RESULT", "DOC_RESULT")})
        env["CLIPPY_RESULT"] = "cancelled"
        result = subprocess.run(["bash", "-e", "-c", "\n".join(script)], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Required workflow gate did not succeed: cancelled", result.stderr)

    def test_prepared_archive_uses_workflow_sha_not_working_tree(self):
        with tempfile.TemporaryDirectory() as repo, tempfile.TemporaryDirectory() as runner_temp, tempfile.TemporaryDirectory() as fake_bin, tempfile.TemporaryDirectory() as workspace:
            def git(*args):
                return subprocess.run(
                    ["git", *args], cwd=repo, check=True, capture_output=True, text=True
                ).stdout.strip()

            git("init", "-q")
            git("config", "user.email", "runner-contract@example.invalid")
            git("config", "user.name", "Runner Contract")
            git("branch", "-m", "main")
            Path(repo, "Cargo.toml").write_text('[package]\nname="fixture"\nversion="0.1.0"\nedition="2021"\n')
            Path(repo, "tracked.txt").write_text("committed source\n")
            Path(repo, "tests").mkdir()
            Path(repo, "tests/runner_workflow_contract.py").write_text(
                "import os\nassert 'GITHUB_TOKEN' not in os.environ\n"
            )
            git("add", "Cargo.toml", "tracked.txt", "tests/runner_workflow_contract.py")
            git("commit", "-qm", "workflow source")
            workflow_sha = git("rev-parse", "HEAD")
            Path(repo, "tracked.txt").write_text("working tree edit\n")
            Path(repo, "untracked.txt").write_text("must not be archived\n")

            fake_cargo = Path(fake_bin, "cargo")
            fake_cargo.write_text("#!/bin/sh\nprintf 'resolved once\\n' > Cargo.lock\n")
            fake_cargo.chmod(0o755)
            env = dict(os.environ)
            env.update(
                GITHUB_SHA=workflow_sha,
                SOURCE_URL=repo,
                GITHUB_TOKEN="test-token",
                RUNNER_TEMP=runner_temp,
                GITHUB_RUN_ID="42",
                GITHUB_RUN_ATTEMPT="2",
                GITHUB_WORKSPACE=workspace,
                PATH=f"{fake_bin}:{env['PATH']}",
            )
            subprocess.run(
                ["bash", "-e", "-c", archive_command(self.workflow)],
                cwd=repo,
                env=env,
                check=True,
            )

            source_archive = Path(runner_temp, "source-42-2.tar.gz")
            with tarfile.open(source_archive, "r:gz") as archive:
                files = {
                    member.name.removeprefix("./"): archive.extractfile(member).read()
                    for member in archive.getmembers()
                    if member.isfile()
                }
            self.assertEqual(files["tracked.txt"], b"committed source\n")
            self.assertEqual(files["Cargo.lock"], b"resolved once\n")
            self.assertFalse(Path(workspace, "Cargo.lock").exists())
            self.assertNotIn("untracked.txt", files)

    def test_each_gate_uses_fresh_prepared_source_without_stale_runner_files(self):
        with tempfile.TemporaryDirectory() as runner_temp, tempfile.TemporaryDirectory() as workspace:
            fake_bin = Path(runner_temp, "bin")
            fake_bin.mkdir()
            fake_cargo = fake_bin / "cargo"
            fake_cargo.write_text(
                '#!/bin/sh\nprintf \'%s\\n\' "$PWD"\ntest ! -e build.rs\ntest ! -e .cargo/config.toml\n'
            )
            fake_cargo.chmod(0o755)
            for gate in GATES:
                suffix = "test" if gate == "cargo-test" else gate
                artifact_dir = Path(runner_temp, f"artifact-42-2-{suffix}")
                artifact_dir.mkdir()
                archive_path = artifact_dir / "source-42-2.tar.gz"
                with tarfile.open(archive_path, "w:gz") as archive:
                    for name, content in (
                        ("Cargo.toml", f"prepared source for {gate}\n"),
                        ("Cargo.lock", f"prepared lock for {gate}\n"),
                    ):
                        data = content.encode()
                        member = tarfile.TarInfo(name)
                        member.size = len(data)
                        archive.addfile(member, io.BytesIO(data))

                gate_workspace = Path(workspace, gate)
                gate_workspace.mkdir()
                Path(gate_workspace, "Cargo.toml").write_text("stale runner workspace\n")
                Path(gate_workspace, "build.rs").write_text("panic!(\"stale build script executed\");\n")
                stale_cargo_config = Path(gate_workspace, ".cargo")
                stale_cargo_config.mkdir()
                Path(stale_cargo_config, "config.toml").write_text('[alias]\ncheck = "!false"\n')
                github_env = Path(runner_temp, f"github-env-{gate}")
                env = dict(os.environ)
                env.update(
                    RUNNER_TEMP=runner_temp,
                    GITHUB_RUN_ID="42",
                    GITHUB_RUN_ATTEMPT="2",
                    GITHUB_WORKSPACE=str(gate_workspace),
                    GITHUB_ENV=str(github_env),
                    PATH=f"{fake_bin}:{os.environ['PATH']}",
                )
                subprocess.run(
                    ["bash", "-e", "-c", named_step_script(self.workflow, gate, "Unpack prepared source")],
                    env=env,
                    check=True,
                )
                prepared_dir = Path(github_env.read_text().strip().split("=", 1)[1])
                self.assertEqual(Path(prepared_dir, "Cargo.toml").read_text(), f"prepared source for {gate}\n")
                self.assertEqual(Path(prepared_dir, "Cargo.lock").read_text(), f"prepared lock for {gate}\n")
                self.assertFalse(Path(prepared_dir, "build.rs").exists())
                self.assertFalse(Path(prepared_dir, ".cargo/config.toml").exists())
                result = subprocess.run(
                    ["bash", "-e", "-c", cargo_step_script(self.workflow, gate)],
                    cwd=gate_workspace,
                    env={**env, "PREPARED_SOURCE_DIR": str(prepared_dir)},
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.stdout.strip(), str(prepared_dir))
                self.assertTrue(Path(gate_workspace, "build.rs").exists())
                self.assertTrue(Path(gate_workspace, ".cargo/config.toml").exists())


if __name__ == "__main__":
    unittest.main()
