import io
import json
import tarfile

import pytest
import yaml
from pydantic import ValidationError

from autobench.config import (
    Benchmark,
    SOCKS5Proxy,
    SSHConfig,
    SSHOverride,
    System,
    inspect_archive,
    load_config,
    merged_env,
)
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


def test_frozen_config_and_stable_job_ids(config):
    config.machines[0].ssh.password = "frozen-login-secret"
    store = Store(config.policy.state_dir)
    run_id = store.create(config)
    original_ids = [job["id"] for job in store.jobs(run_id)]
    config.benchmarks[0].env["SHARED"] = "changed-after-creation"
    config.machines[0].ssh.password = "changed-login-secret"
    frozen = store.config(run_id)
    assert frozen.machines[0].ssh.password == "frozen-login-secret"
    assert frozen.benchmarks[0].env["SHARED"] == "benchmark"
    assert "frozen-login-secret" in store.run(run_id)["config"]
    store.close()
    reopened = Store(config.policy.state_dir)
    assert reopened.config(run_id).machines[0].ssh.password == "frozen-login-secret"
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


@pytest.mark.parametrize("password", [None, "", "123456", "$LOGIN ${SECRET} $(id) `id` # : 雪\n"])
def test_yaml_password_literals_and_system_overrides(tmp_path, config, password):
    value = config.model_dump(mode="json")
    value["machines"][0]["ssh"]["password"] = password
    value["machines"][0]["systems"][1]["ssh"] = {"password": "system-secret"}
    path = tmp_path / "passwords.yaml"
    path.write_text(yaml.safe_dump(value, allow_unicode=True))
    machine = load_config(path).machines[0]
    assert machine.connection(machine.systems[0]).password == password
    assert machine.connection(machine.systems[1]).password == "system-secret"
    machine.systems[1].ssh = SSHOverride(password=None)
    assert machine.connection(machine.systems[1]).password is None


@pytest.mark.parametrize("model", [SSHConfig, SSHOverride, SOCKS5Proxy])
@pytest.mark.parametrize("password", [123456, True, ["secret"]])
def test_password_requires_string(model, password):
    with pytest.raises(ValidationError):
        model.model_validate({"host": "localhost", "username": "bench", "password": password})


@pytest.mark.parametrize("model", [SSHConfig, SSHOverride, SOCKS5Proxy])
def test_password_hidden_in_repr_and_validation_errors(model):
    values = {"host": "localhost", "username": "bench", "password": "private-login-secret"}
    assert "private-login-secret" not in repr(model.model_validate(values))
    with pytest.raises(ValidationError) as failure:
        model.model_validate({**values, "port": 0})
    assert "private-login-secret" not in str(failure.value)
    with pytest.raises(ValidationError, match="Extra inputs") as failure:
        model.model_validate({**values, "unknown_option": "private-login-secret"})
    assert "private-login-secret" not in str(failure.value)


@pytest.mark.parametrize("password", ["x", "x" * 255])
def test_proxy_password_length_boundaries(password):
    proxy = SOCKS5Proxy(host="localhost", username="bench", password=password)
    assert proxy.password == password


@pytest.mark.parametrize("password", ["雪-secret", "secret" * 50])
def test_invalid_proxy_password_is_not_in_validation_error(config, password):
    value = config.model_dump(mode="json")
    value["machines"][0]["ssh"]["proxy"] = {
        "host": "localhost",
        "username": "bench",
        "password": password,
    }
    with pytest.raises(ValidationError) as failure:
        type(config).model_validate(value)
    assert password not in str(failure.value)


@pytest.mark.parametrize("machine_password", ["machine-login-secret", ""])
def test_snapshot_redacts_all_passwords_and_preserves_frozen_overrides(config, machine_password):
    machine = config.machines[0]
    machine.ssh.password = machine_password
    machine.ssh.proxy = SOCKS5Proxy(
        host="proxy.local", username="bench", password="machine-proxy-secret"
    )
    machine.systems[0].ssh = SSHOverride(
        password="system-login-secret",
        proxy=SOCKS5Proxy(
            host="system-proxy.local", username="bench", password="system-proxy-secret"
        ),
    )
    machine.systems[1].ssh = SSHOverride(password=None, proxy=None)
    store = Store(config.policy.state_dir)
    try:
        run_id = store.create(config)
    finally:
        store.close()
    store = Store(config.policy.state_dir)
    try:
        frozen_json = store.run(run_id)["config"]
        store.snapshot(run_id, config.policy.output_dir)
        store.snapshot(run_id, config.policy.output_dir)
        exported_json = (config.policy.output_dir / "config.json").read_text()
        exported = json.loads(exported_json)["machines"][0]
        assert exported["ssh"]["password"] == "********"
        assert exported["ssh"]["proxy"]["password"] == "********"
        assert exported["systems"][0]["ssh"]["password"] == "********"
        assert exported["systems"][0]["ssh"]["proxy"]["password"] == "********"
        assert exported["systems"][1]["ssh"] == {"password": None, "proxy": None}
        for secret in (
            machine_password,
            "machine-proxy-secret",
            "system-login-secret",
            "system-proxy-secret",
        ):
            if secret:
                assert secret not in exported_json
        assert store.run(run_id)["config"] == frozen_json
        frozen = store.config(run_id).machines[0]
        assert frozen.ssh.password == machine_password
        assert frozen.ssh.proxy.password == "machine-proxy-secret"
        assert frozen.connection(frozen.systems[0]).password == "system-login-secret"
        assert frozen.connection(frozen.systems[0]).proxy.password == "system-proxy-secret"
        assert frozen.connection(frozen.systems[1]).password is None
        assert frozen.connection(frozen.systems[1]).proxy is None
        assert (config.policy.output_dir / "config.json").stat().st_mode & 0o777 == 0o600
    finally:
        store.close()


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
        {"host": "localhost", "password": "proxy-secret"},
        {"host": "localhost", "username": "", "password": "proxy-secret"},
        {"host": "localhost", "username": "雪", "password": "proxy-secret"},
        {"host": "localhost", "username": "x" * 256, "password": "proxy-secret"},
        {"host": "localhost", "username": "user", "password": "秘密"},
        {"host": "localhost", "username": "user", "password": ""},
        {"host": "localhost", "username": "user", "password": "x" * 256},
        {"host": "localhost", "username": "user", "password": 123},
    ],
)
def test_invalid_proxy_rejected(proxy):
    with pytest.raises(ValidationError):
        SOCKS5Proxy.model_validate(proxy)


def test_proxy_inheritance_replacement_clear_and_snapshot(config):
    machine = config.machines[0]
    assert machine.connection().proxy is None
    machine.ssh.proxy = SOCKS5Proxy(
        host="proxy.local", username="proxy-user", password="frozen-proxy-secret"
    )
    assert machine.ssh.proxy.port == 1080
    assert machine.connection(machine.systems[0]).proxy == machine.ssh.proxy
    machine.systems[1].ssh = SSHOverride(proxy={"host": "other-proxy", "port": 1081})
    replacement = machine.connection(machine.systems[1]).proxy
    assert replacement.host == "other-proxy" and replacement.username is None
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
        assert "frozen-proxy-secret" in store.run(run_id)["config"]
        store.snapshot(run_id, config.policy.output_dir)
        assert "frozen-proxy-secret" not in (config.policy.output_dir / "config.json").read_text()
    finally:
        store.close()
