import io
import json
import tarfile

import pytest
from pydantic import ValidationError

from autobench.config import Benchmark, System, inspect_archive, load_config, merged_env
from autobench.storage import Store


@pytest.mark.parametrize(
    "env",
    [
        {"BAD-NAME": "x"},
        {"9BAD": "x"},
        {"OK": 3},
        {"OK": None},
        {"OK": True},
        {"OK": "a\x00b"},
        {"UB_RESULTDIR": "/tmp/out"},
    ],
)
def test_reject_invalid_environment(env, archive):
    with pytest.raises(ValidationError):
        Benchmark(id="test", source_archive=archive, env=env)
    with pytest.raises(ValidationError):
        System(id="os", boot_entry="0001", machine_id="1" * 32, env=env)


def test_environment_precedence_literals_and_isolation():
    base = {"PATH": "/bin", "A": "base"}
    result = merged_env(
        base,
        {"A": "system", "EMPTY": ""},
        {"A": "test", "LITERAL": "$(touch /tmp/nope) $PATH `id`\n雪"},
    )
    assert result["A"] == "test" and result["EMPTY"] == ""
    assert result["LITERAL"].startswith("$(touch")
    assert base == {"PATH": "/bin", "A": "base"}


def test_source_autodetect_and_digest(config):
    benchmark = config.benchmarks[0]
    assert benchmark.source_subdir == "UnixBench"
    assert len(benchmark.source_sha256) == 64
    benchmark.source_sha256 = "0" * 64
    with pytest.raises(ValueError, match="checksum"):
        inspect_archive(benchmark)


@pytest.mark.parametrize("kind", ["traversal", "symlink", "hardlink", "duplicate"])
def test_unsafe_archive(tmp_path, kind):
    path = tmp_path / "bad.tar"
    with tarfile.open(path, "w") as archive:
        member = tarfile.TarInfo("../escape" if kind == "traversal" else "file")
        if kind in ("symlink", "hardlink"):
            member.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
            member.linkname = "/etc/passwd"
        archive.addfile(member, io.BytesIO(b""))
        if kind == "duplicate":
            archive.addfile(member, io.BytesIO(b""))
    with pytest.raises(ValueError):
        inspect_archive(Benchmark(id="ub", source_archive=path))


def test_frozen_config_and_stable_job_ids(config, monkeypatch):
    monkeypatch.setenv("LOGIN_SECRET", "do-not-persist")
    config.machines[0].ssh.password_env = "LOGIN_SECRET"
    store = Store(config.policy.state_dir)
    run_id = store.create(config)
    original_ids = [job["id"] for job in store.jobs(run_id)]
    config.benchmarks[0].env["SHARED"] = "changed-after-creation"
    frozen = store.config(run_id)
    assert frozen.benchmarks[0].env["SHARED"] == "benchmark"
    assert "do-not-persist" not in store.run(run_id)["config"]
    store.close()
    reopened = Store(config.policy.state_dir)
    assert [job["id"] for job in reopened.jobs(run_id)] == original_ids
    reopened.close()


def test_relative_paths_resolved_against_yaml(tmp_path, config):
    value = config.model_dump(mode="json")
    value["benchmarks"][0]["source_archive"] = "source.tar.gz"
    value["policy"]["state_dir"] = "state"
    value["policy"]["output_dir"] = "output"
    path = tmp_path / "config.yaml"
    path.write_text(json.dumps(value))
    parsed = load_config(path)
    assert parsed.benchmarks[0].source_archive == tmp_path / "source.tar.gz"
    assert parsed.policy.output_dir == tmp_path / "output"


def test_duplicate_os_identity_rejected(config):
    value = config.model_dump()
    value["machines"][0]["systems"][1]["machine_id"] = "1" * 32
    with pytest.raises(ValidationError, match="duplicate"):
        type(config).model_validate(value)


@pytest.mark.parametrize(
    "proxy",
    [
        {"host": ""},
        {"host": "   "},
        {"host": "a\x00b"},
        {"host": "localhost", "port": 0},
        {"host": "localhost", "port": 65536},
        {"host": "localhost", "username": "user"},
        {"host": "localhost", "password_env": "PROXY_PASSWORD"},
        {"host": "localhost", "username": "", "password_env": "PROXY_PASSWORD"},
        {"host": "localhost", "username": "雪", "password_env": "PROXY_PASSWORD"},
        {"host": "localhost", "username": "x" * 256, "password_env": "PROXY_PASSWORD"},
        {"host": "localhost", "username": "user", "password_env": "BAD-NAME"},
        {"host": "localhost", "username": "user", "password_env": ""},
    ],
)
def test_invalid_proxy_rejected(proxy):
    from autobench.config import SOCKS5Proxy

    with pytest.raises(ValidationError):
        SOCKS5Proxy.model_validate(proxy)


def test_proxy_inheritance_replacement_clear_and_snapshot(config, monkeypatch):
    from autobench.config import SOCKS5Proxy, SSHOverride

    machine = config.machines[0]
    assert machine.connection().proxy is None
    machine.ssh.proxy = SOCKS5Proxy(
        host="proxy.local", username="proxy-user", password_env="PROXY_PASSWORD"
    )
    assert machine.ssh.proxy.port == 1080
    assert machine.connection(machine.systems[0]).proxy == machine.ssh.proxy
    machine.systems[1].ssh = SSHOverride(proxy={"host": "other-proxy", "port": 1081})
    replacement = machine.connection(machine.systems[1]).proxy
    assert replacement.host == "other-proxy" and replacement.username is None
    monkeypatch.setenv("PROXY_PASSWORD", "never-persist-this-secret")
    store = Store(config.policy.state_dir)
    try:
        run_id = store.create(config)
        frozen = store.config(run_id).machines[0]
        assert frozen.connection(frozen.systems[0]).proxy == machine.ssh.proxy
        assert frozen.connection(frozen.systems[1]).proxy == replacement
        machine.systems[1].ssh = SSHOverride(proxy=None)
        assert machine.connection(machine.systems[1]).proxy is None
        run_id = store.create(config)
        frozen = store.config(run_id).machines[0]
        assert frozen.connection(frozen.systems[0]).proxy == machine.ssh.proxy
        assert frozen.connection(frozen.systems[1]).proxy is None
        assert "never-persist-this-secret" not in store.run(run_id)["config"]
        store.snapshot(run_id, config.policy.output_dir)
        assert (
            "never-persist-this-secret"
            not in (config.policy.output_dir / "config.json").read_text()
        )
    finally:
        store.close()
