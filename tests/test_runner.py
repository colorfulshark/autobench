import ast
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import source_archive

from autobench import remote_runner as rr
from autobench.adapters import get_adapter
from autobench.config import inspect_archive


def manifest_for(config, tmp_path):
    benchmark = config.benchmarks[0]
    job_id = "a" * 32
    directory = tmp_path / "remote" / "jobs" / job_id
    manifest = {
        "schema_version": 1,
        "job_id": job_id,
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
    config.machines[0].systems[0].env.update({"EMPTY": "", "LITERAL": "$(echo hi) $PATH `id`\n雪"})
    directory, _, _ = manifest_for(config, tmp_path)
    monkeypatch.setenv("BASE_ONLY", "base-value")
    process = launch(directory)
    stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, (stdout, stderr)
    artifacts = directory / "artifacts"
    observed = json.loads((artifacts / "results/observed.json").read_text())
    archived = json.loads((artifacts / "environment.json").read_text())
    assert observed == archived
    assert observed["SHARED"] == "benchmark"
    assert observed["SYSTEM_ONLY"] == "yes" and observed["BASE_ONLY"] == "base-value"
    assert observed["EMPTY"] == "" and observed["LITERAL"] == "$(echo hi) $PATH `id`\n雪"
    build = json.loads((artifacts / "build-environment.json").read_text())
    assert "RUNTIME_ONLY" not in build
    assert "yes" not in (artifacts / "build.stdout").read_text()
    inventory = json.loads((directory / "artifacts.json").read_text())
    for item in inventory["files"]:
        assert rr.digest(artifacts / item["path"]) == item["sha256"]
    assert (artifacts.stat().st_mode & 0o777) == 0o700
    assert (artifacts / "environment.json").stat().st_mode & 0o777 == 0o600


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
    state = rr.read_json(directory / "state.json")
    assert state["status"] == "failed"
    assert (directory / "artifacts.json").exists()
    assert (directory / "artifacts/system-after.json").exists()
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
