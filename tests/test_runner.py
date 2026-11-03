import ast
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
from conftest import assert_no_removed_outputs, source_archive

from autobench import remote_runner as rr
from autobench.adapters import get_adapter
from autobench.config import inspect_archive


def manifest_for(config, tmp_path, job_id="a" * 32, build_id=None):
    benchmark = config.benchmarks[0]
    directory = tmp_path / "remote" / "jobs" / job_id
    manifest = {
        "schema_version": 2,
        "job_id": job_id,
        "build_id": build_id or job_id,
        "settle_seconds": config.policy.settle_seconds,
        "boot_id": rr.boot_id(),
        "source_path": str(benchmark.source_archive),
        "source_sha256": benchmark.source_sha256,
        "system_env": config.machines[0].systems[0].env,
        "benchmark_env": benchmark.env,
        "execution": get_adapter("unixbench").execution(benchmark),
    }
    payload = {"job_id": job_id, "job_dir": str(directory), "manifest": manifest}
    rr.stage(payload)
    return directory, manifest, payload


def launch(directory, **kwargs):
    return subprocess.Popen(
        [sys.executable, rr.__file__, "worker", str(directory / "manifest.json")],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **kwargs,
    )


def test_remote_runner_is_python38_compatible():
    ast.parse(Path(rr.__file__).read_text(), feature_version=(3, 8))


@pytest.mark.parametrize(
    "args,tests",
    [
        ([], []),
        (["-c", "1", "-i", "3"], ["execl"]),
        (["-c", "1", "-i", "3"], ["execl", "shell"]),
        (["-c", "1", "pipe"], []),
        (["-c", "1", "pipe"], ["execl", "shell"]),
    ],
)
def test_worker_receives_selected_tests(config, tmp_path, args, tests):
    benchmark = config.benchmarks[0]
    source_archive(
        benchmark.source_archive,
        run="""import json, os, pathlib, sys
out = pathlib.Path(os.environ["UB_RESULTDIR"])
(out / "argv.json").write_text(json.dumps(sys.argv[1:]))
""",
    )
    benchmark.source_sha256 = None
    benchmark.args = args
    benchmark.tests = tests
    inspect_archive(benchmark)
    adapter = get_adapter("unixbench")
    assert adapter.execution(benchmark)["run_argv"] == ["./Run", *args, *tests]
    assert adapter.metadata(benchmark)["tests"] == tests
    directory, _, _ = manifest_for(config, tmp_path)
    process = launch(directory)
    stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, (stdout, stderr)
    observed = json.loads((directory / "artifacts/results/argv.json").read_text())
    assert observed == [*args, *tests]


def test_probe_reads_only_boot_state_from_files(monkeypatch):
    def read_boot(path):
        assert path == Path("/proc/sys/kernel/random/boot_id")
        return "boot-1\n"

    monkeypatch.setattr(Path, "read_text", read_boot)
    monkeypatch.setattr(rr, "efi", lambda: {"current": "0001", "entries": {}})
    monkeypatch.setattr(rr, "command", lambda *args, **kwargs: "systemd 250\n")
    probe = rr.probe({})
    assert probe["boot_id"] == "boot-1"
    assert probe["efi"]["current"] == "0001"
    assert probe["systemd_available"] is True


def test_worker_actual_environment_and_artifact_hashes(config, tmp_path, monkeypatch):
    # Record the environment at each executable's entry before make modifies it.
    tool_dir = tmp_path / "toolchain"
    tool_dir.mkdir()
    for name in ("make", "gcc"):
        executable = shutil.which(name)
        assert executable is not None
        wrapper = tool_dir / name
        wrapper.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            'phase = "version" if sys.argv[1:] == ["--version"] else "build"\n'
            f"output = pathlib.Path({str(tool_dir)!r}) / ({name!r} + '-' + phase + '.json')\n"
            "output.write_text(json.dumps(dict(os.environ)))\n"
            f"os.execv({executable!r}, [sys.argv[0], *sys.argv[1:]])\n"
        )
        wrapper.chmod(0o755)
    config.machines[0].systems[0].env.update(
        {
            "PATH": str(tool_dir) + os.pathsep + os.environ["PATH"],
            "EMPTY": "",
            "LITERAL": "$(echo hi) $PATH `id`\n雪",
            "UB_TMPDIR": str(tmp_path / "custom tmp"),
        }
    )
    directory, _, _ = manifest_for(config, tmp_path)
    monkeypatch.setenv("BASE_ONLY", "base-value")
    monkeypatch.setenv("SHARED", "base")
    monkeypatch.setenv("SYSTEM_ONLY", "base")
    for name in ("UB_BINDIR", "UB_RESULTDIR", "MAKE"):
        monkeypatch.delenv(name, raising=False)
    process = launch(directory)
    stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, (stdout, stderr)
    artifacts = directory / "artifacts"
    common = rr.build_path(directory, rr.read_json(directory / "manifest.json")) / "artifacts"
    observed = json.loads((artifacts / "results/observed.json").read_text())
    archived = json.loads((artifacts / "run-environment.json").read_text())
    assert observed == archived
    assert observed["SHARED"] == "benchmark"
    assert observed["SYSTEM_ONLY"] == "yes" and observed["BASE_ONLY"] == "base-value"
    assert observed["EMPTY"] == "" and observed["LITERAL"] == "$(echo hi) $PATH `id`\n雪"
    assert observed["UB_TMPDIR"] == str(tmp_path / "custom tmp")
    build = json.loads((common / "build-environment.json").read_text())
    assert build == rr.read_json(tool_dir / "make-build.json")
    assert build == rr.read_json(tool_dir / "make-version.json")
    assert build == rr.read_json(tool_dir / "gcc-version.json")
    assert build["BASE_ONLY"] == "base-value"
    for overrides in (config.machines[0].systems[0].env, config.benchmarks[0].env):
        for name in overrides:
            assert build[name] == observed[name]
    assert build["BENCHMARK_ONLY"] == "yes"
    assert "yes" in (common / "build.stdout").read_text()
    for name in ("UB_BINDIR", "UB_RESULTDIR", "MAKE"):
        assert name not in build and name in observed
    toolchain = rr.read_json(common / "toolchain.json")
    assert all("output" in toolchain[name] for name in ("compiler", "make"))
    inventory = json.loads((directory / "artifacts.json").read_text())
    for item in inventory["files"]:
        assert rr.digest(artifacts / item["path"]) == item["sha256"]
    assert (artifacts.stat().st_mode & 0o777) == 0o700
    assert (artifacts / "run-environment.json").stat().st_mode & 0o777 == 0o600
    assert (common / "build-environment.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("mode", ["build", "run", "timeout", "checksum"])
def test_worker_failures_still_seal_logs(config, tmp_path, mode):
    benchmark = config.benchmarks[0]
    if mode == "build":
        source_archive(benchmark.source_archive, make="all:\n\t@echo broken >&2\n\t@exit 7\n")
    elif mode == "run":
        source_archive(benchmark.source_archive, run="import sys\nprint('failed')\nsys.exit(9)\n")
    elif mode == "timeout":
        source_archive(benchmark.source_archive, run="import time\ntime.sleep(60)\n")
        benchmark.timeout = 0.05
    if mode != "checksum":
        benchmark.source_sha256 = None
        inspect_archive(benchmark)
    directory, _, _ = manifest_for(config, tmp_path)
    if mode == "checksum":
        benchmark.source_archive.write_bytes(b"corrupt")
    process = launch(directory)
    _, stderr = process.communicate(timeout=30)
    assert process.returncode == 1, stderr
    assert_no_removed_outputs(tmp_path / "remote")
    state = rr.read_json(directory / "state.json")
    assert state["status"] == "failed"
    assert (directory / "artifacts.json").exists()
    if mode in {"run", "timeout"}:
        assert (directory / "artifacts/run-environment.json").is_file()
    else:
        assert not (directory / "artifacts/run-environment.json").exists()
    if mode == "timeout":
        assert state["commands"]["run"]["timed_out"] is True
        assert state["commands"]["run"]["exit_code"] < 0


def test_worker_cancel_kills_descendants(config, tmp_path):
    benchmark = config.benchmarks[0]
    source_archive(
        benchmark.source_archive,
        run="""import os, pathlib, subprocess, time
out = pathlib.Path(os.environ["UB_RESULTDIR"])
child = subprocess.Popen(["sleep", "60"])
(out / "child.pid").write_text(str(child.pid))
time.sleep(60)
""",
    )
    benchmark.source_sha256 = None
    inspect_archive(benchmark)
    directory, _, _ = manifest_for(config, tmp_path)
    process = launch(directory)
    pid_file = directory / "artifacts/results/child.pid"
    try:
        deadline = time.monotonic() + 15
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_file.exists()
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=15)
        assert rr.read_json(directory / "state.json")["status"] == "cancelled"
        child = Path("/proc") / pid_file.read_text() / "stat"
        assert not child.exists() or child.read_text().split()[2] == "Z"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_start_response_uncertainty_never_replays(config, tmp_path, monkeypatch):
    directory, _, payload = manifest_for(config, tmp_path)
    calls = []

    def command(argv, **kwargs):
        assert (directory / "submitted.json").exists()
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, 30)

    monkeypatch.setattr(rr, "command", command)
    monkeypatch.setattr(
        rr, "service", lambda d: {"LoadState": "not-found", "ActiveState": "inactive"}
    )
    assert rr.start(payload)["uncertain"]
    assert rr.start(payload)["existing"]
    assert rr.status(payload)["phase"] == "uncertain"
    assert len(calls) == 1


def test_remote_reboot_seals_interruption(config, tmp_path, monkeypatch):
    directory, _, payload = manifest_for(config, tmp_path)
    monkeypatch.setattr(rr, "boot_id", lambda: "new-boot")
    monkeypatch.setattr(
        rr, "service", lambda d: {"LoadState": "not-found", "ActiveState": "inactive"}
    )
    status = rr.status(payload)
    assert status["state"]["status"] == "interrupted"
    assert (directory / "artifacts.json").exists()
    assert_no_removed_outputs(directory.parent.parent)


def test_remote_reservation_released_only_after_ack(config, tmp_path, monkeypatch):
    directory, _, payload = manifest_for(config, tmp_path)
    monkeypatch.setattr(rr, "command", lambda *a, **kw: "")
    monkeypatch.setattr(
        rr, "service", lambda d: {"LoadState": "not-found", "ActiveState": "inactive"}
    )
    rr.start(payload)
    root = directory.parent.parent
    assert (root / "reservation.json").exists()
    with pytest.raises(RuntimeError, match="unsealed"):
        rr.acknowledge(payload)
    rr.finalize(directory, {"status": "failed"})
    rr.acknowledge(payload)
    assert not (root / "reservation.json").exists()


def test_efi_parse_and_switch_guards(tmp_path, monkeypatch):
    monkeypatch.setattr(
        rr,
        "command",
        lambda *a, **kw: (
            "BootCurrent: 0001\nBootNext: 0002\nBoot0001* Linux One\nBoot0002* Linux Two\n"
        ),
    )
    parsed = rr.efi()
    assert parsed["current"] == "0001" and parsed["next"] == "0002"
    assert parsed["entries"]["0002"]["active"]
    payload = {
        "root": str(tmp_path / "remote"),
        "previous_boot_id": rr.boot_id(),
        "boot_entry": "0002",
        "token": "a" * 32,
    }
    with pytest.raises(RuntimeError, match="BootNext"):
        rr.switch(payload)
    commands = []
    sequence = [dict(parsed, next=None), dict(parsed, next="0002")]
    monkeypatch.setattr(rr, "efi", lambda: sequence.pop(0))
    monkeypatch.setattr(rr, "command", lambda argv, **kw: commands.append(argv) or "")
    assert rr.switch(payload)["reboot_requested"]
    assert commands == [["efibootmgr", "-n", "0002"], ["systemctl", "reboot", "--no-block"]]


def completed_worker(directory, expected=0, **kwargs):
    process = launch(directory, **kwargs)
    stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == expected, (stdout, stderr, rr.read_json(directory / "state.json"))
    assert_no_removed_outputs(directory.parent.parent)
    return rr.read_json(directory / "state.json")


def test_three_rounds_build_once_and_record_complete_environment(config, tmp_path):
    benchmark = config.benchmarks[0]
    source_archive(
        benchmark.source_archive,
        make="all:\n\t@mkdir -p pgms\n\t$(CC) -o pgms/probe src/probe.c\n\t@echo built >> build-count\n",
        run="""import hashlib, json, os, pathlib, subprocess
out = pathlib.Path(os.environ["UB_RESULTDIR"])
assert os.system(os.environ["MAKE"] + " check") == 0
assert os.system(os.environ["MAKE"] + " all") == 0
binary = pathlib.Path(os.environ["UB_BINDIR"]) / "probe"
assert subprocess.check_output([str(binary)]).strip() == b"built"
assert pathlib.Path("testdir/input").read_text() == "fresh input\\n"
pathlib.Path("testdir/input").write_text("changed in this round")
(out / "observed.json").write_text(json.dumps(dict(os.environ)))
(out / "binary.json").write_text(json.dumps({"path": str(binary), "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}))
""",
    )
    benchmark.source_sha256 = None
    inspect_archive(benchmark)
    config.policy.settle_seconds = 0.08
    group = tmp_path / "remote with spaces"
    observations = []
    common_inventory = None
    for index, char in enumerate("abc"):
        directory, manifest, _ = manifest_for(config, group, job_id=char * 32, build_id="a" * 32)
        env = dict(os.environ)
        env.update({"ROUND_CHANGE": str(index), "ROUND_EMPTY": ""})
        env.update({"INVOCATION_ID": char * 32, "JOURNAL_STREAM": f"round-{index}"})
        if index == 0:
            env["FIRST_ONLY"] = "removed later"
        else:
            env["LATER_ONLY"] = "added later"
        state = completed_worker(directory, env=env)
        build = rr.build_path(directory, manifest)
        common = build / "artifacts"
        inventory = rr.read_json(build / "artifacts.json")
        if common_inventory is None:
            common_inventory = inventory
            assert rr.read_json(common / "build-environment.json") == {
                **env,
                **config.machines[0].systems[0].env,
                **benchmark.env,
            }
        assert inventory == common_inventory
        ready = rr.read_json(build / "ready.json")
        assert ready["binaries"] == rr.file_inventory(build / "source/UnixBench/pgms")
        assert {path.name for path in common.iterdir()} == {
            "build.json",
            "build.stdout",
            "build.stderr",
            "build-environment.json",
            "toolchain.json",
        }
        for item in inventory["files"]:
            assert rr.digest(common / item["path"]) == item["sha256"]
        assert (build / "source/UnixBench/build-count").read_text() == "built\n"
        build_record = rr.read_json(common / "build.json")
        assert datetime.fromisoformat(build_record["finished"]) <= datetime.fromisoformat(
            state["wait_started"]
        )
        assert (
            datetime.fromisoformat(state["commands"]["run"]["started"]).timestamp()
            >= state["ready_at"]
        )
        assert (
            datetime.fromisoformat(state["wait_finished"]).timestamp()
            - datetime.fromisoformat(state["wait_started"]).timestamp()
            >= 0.08
        )
        artifacts = directory / "artifacts"
        assert datetime.fromisoformat(
            state["commands"]["run"]["started"]
        ) >= datetime.fromisoformat(state["wait_finished"])
        archived = rr.read_json(artifacts / "run-environment.json")
        actual = rr.read_json(artifacts / "results/observed.json")
        assert archived == actual
        assert actual["ROUND_EMPTY"] == ""
        assert actual["ROUND_CHANGE"] == str(index)
        assert actual["UB_RESULTDIR"] == str(artifacts / "results")
        assert actual["UB_TMPDIR"] == str(directory / "work/tmp")
        assert actual["INVOCATION_ID"] == char * 32
        assert actual["JOURNAL_STREAM"] == f"round-{index}"
        if index:
            assert "FIRST_ONLY" not in actual and actual["LATER_ONLY"] == "added later"
        observations.append(rr.read_json(artifacts / "results/binary.json"))
        assert set(path.name for path in artifacts.iterdir()) == {
            "run-environment.json",
            "results",
            "run.stdout",
            "run.stderr",
        }
        assert "build" not in state["commands"]
    assert observations == [observations[0]] * 3


@pytest.mark.parametrize("damage", ["change", "remove", "add", "directory", "boot"])
def test_damaged_build_fails_without_rebuilding(config, tmp_path, damage):
    first, manifest, _ = manifest_for(config, tmp_path)
    completed_worker(first)
    build = rr.build_path(first, manifest)
    sealed = (build / "artifacts.json").read_bytes()
    program = build / "source/UnixBench/pgms/probe"
    if damage == "change":
        program.write_text("changed")
    elif damage == "remove":
        program.unlink()
    elif damage == "add":
        program.with_name("extra").write_text("added")
    elif damage == "directory":
        shutil.rmtree(program.parent)
    else:
        # A fresh worker is on the real boot, but the persisted build is from another boot.
        ready = rr.read_json(build / "ready.json")
        ready["identity"]["boot_id"] = "previous-boot"
        rr.atomic_json(build / "ready.json", ready)
    second, _, _ = manifest_for(config, tmp_path, job_id="b" * 32, build_id="a" * 32)
    state = completed_worker(second, expected=1)
    assert "disabled" in state["reason"] or "missing" in state["reason"]
    assert "run" not in state["commands"]
    assert (build / "artifacts.json").read_bytes() == sealed
    assert not (second / "source").exists()


def test_binary_mutation_during_run_is_a_failure(config, tmp_path):
    benchmark = config.benchmarks[0]
    source_archive(
        benchmark.source_archive,
        run="""import os, pathlib
(pathlib.Path(os.environ["UB_BINDIR"]) / "probe").write_text("mutated")
""",
    )
    benchmark.source_sha256 = None
    inspect_archive(benchmark)
    directory, _, _ = manifest_for(config, tmp_path)
    state = completed_worker(directory, expected=1)
    assert state["commands"]["run"]["exit_code"] == 0
    assert "binaries changed" in state["reason"]


@pytest.mark.parametrize("phase", ["building", "stabilizing"])
def test_cancel_during_build_or_wait_never_rebuilds(config, tmp_path, phase):
    benchmark = config.benchmarks[0]
    if phase == "building":
        source_archive(benchmark.source_archive, make="all:\n\t@sleep 60\n")
        benchmark.source_sha256 = None
        inspect_archive(benchmark)
    else:
        config.policy.settle_seconds = 60
    directory, manifest, _ = manifest_for(config, tmp_path)
    process = launch(directory)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = rr.read_json(directory / "state.json", {})
            if state.get("status") == phase:
                break
            time.sleep(0.01)
        assert state.get("status") == phase
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 1, (stdout, stderr)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    state = rr.read_json(directory / "state.json")
    assert state["status"] == "cancelled" and "run" not in state["commands"]
    assert_no_removed_outputs(directory.parent.parent)
    build = rr.build_path(directory, manifest)
    sealed = (build / "artifacts.json").read_bytes()
    if phase == "building":
        assert not (directory / "artifacts/run-environment.json").exists()
        assert rr.read_json(build / "artifacts/build.json")["status"] == "cancelled"
        second, _, _ = manifest_for(config, tmp_path, job_id="b" * 32, build_id="a" * 32)
        assert "rebuild is disabled" in completed_worker(second, expected=1)["reason"]
    else:
        assert (build / "ready.json").exists()
        assert (directory / "artifacts/run-environment.json").is_file()
    assert (build / "artifacts.json").read_bytes() == sealed
    assert (directory / "artifacts.json").exists()


def test_failed_build_is_sealed_and_not_retried(config, tmp_path):
    benchmark = config.benchmarks[0]
    source_archive(benchmark.source_archive, make="all:\n\t@echo tried >> attempts\n\t@exit 1\n")
    benchmark.source_sha256 = None
    inspect_archive(benchmark)
    first, manifest, _ = manifest_for(config, tmp_path)
    completed_worker(first, expected=1)
    build = rr.build_path(first, manifest)
    sealed = (build / "artifacts.json").read_bytes()
    second, _, _ = manifest_for(config, tmp_path, job_id="b" * 32, build_id="a" * 32)
    assert "rebuild is disabled" in completed_worker(second, expected=1)["reason"]
    assert (build / "source/UnixBench/attempts").read_text() == "tried\n"
    assert (build / "artifacts.json").read_bytes() == sealed
    assert rr.read_json(build / "artifacts/build.json")["status"] == "failed"


def test_separate_build_groups_build_independently(config, tmp_path):
    directories = []
    for char in "ab":
        directory, manifest, _ = manifest_for(config, tmp_path, job_id=char * 32)
        completed_worker(directory)
        build = rr.build_path(directory, manifest)
        assert rr.read_json(build / "artifacts/build.json")["status"] == "succeeded"
        directories.append(build)
    assert directories[0] != directories[1]
    assert all((build / "source/UnixBench/pgms/probe").is_file() for build in directories)


def test_killed_build_is_sealed_by_status_without_restart(config, tmp_path, monkeypatch):
    benchmark = config.benchmarks[0]
    source_archive(benchmark.source_archive, make="all:\n\t@echo partial-build\n\t@sleep 60\n")
    benchmark.source_sha256 = None
    inspect_archive(benchmark)
    directory, manifest, payload = manifest_for(config, tmp_path)
    build = rr.build_path(directory, manifest)
    process = launch(directory)
    child_groups = []
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            # Wait until make is actually running; its process group models systemd's cgroup.
            children_path = Path(f"/proc/{process.pid}/task/{process.pid}/children")
            children = children_path.read_text().split() if children_path.exists() else []
            if (build / "artifacts/build.stdout").exists() and children:
                child_groups = [os.getpgid(int(pid)) for pid in children]
                break
            time.sleep(0.01)
        assert child_groups
        process.kill()
        for group in child_groups:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.communicate(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    assert not (build / "artifacts.json").exists()
    monkeypatch.setattr(rr, "service", lambda _: {"LoadState": "loaded", "ActiveState": "failed"})
    status = rr.status(payload)
    assert status["phase"] == "terminal" and status["state"]["status"] == "interrupted"
    assert status["common"]["inventory"]["files"]
    assert_no_removed_outputs(directory.parent.parent)
    assert rr.read_json(build / "artifacts/build.json")["status"] == "interrupted"
    sealed = (build / "artifacts.json").read_bytes()
    second, _, _ = manifest_for(config, tmp_path, job_id="b" * 32, build_id="a" * 32)
    assert "rebuild is disabled" in completed_worker(second, expected=1)["reason"]
    assert (build / "artifacts.json").read_bytes() == sealed


def test_completed_build_cannot_be_reused_after_boot_change(config, tmp_path, monkeypatch):
    first, manifest, _ = manifest_for(config, tmp_path)
    completed_worker(first)
    build = rr.build_path(first, manifest)
    monkeypatch.setattr(rr, "boot_id", lambda: "another-boot")
    with pytest.raises(RuntimeError, match="boot ID changed"):
        rr.verify_build(build)


def test_runtime_make_rejects_build_targets(config, tmp_path):
    first, manifest, _ = manifest_for(config, tmp_path)
    completed_worker(first)
    build = rr.build_path(first, manifest)
    for target in ("clean", "programs", "-B"):
        result = subprocess.run(
            [sys.executable, str(build / "runner.py"), "check-build", str(build), target],
            capture_output=True,
        )
        assert result.returncode != 0
        assert b"building during a benchmark is disabled" in result.stderr
