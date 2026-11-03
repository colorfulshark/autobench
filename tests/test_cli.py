import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from autobench.cli import app
from autobench.storage import Store

runner = CliRunner()


def test_cli_help_and_validation(config, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(config.model_dump_json())
    assert runner.invoke(app, ["--help"]).exit_code == 0
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 0, result.output
    assert "SHA256" in result.output


def test_invalid_environment_cli_exits_nonzero(config, tmp_path):
    value = config.model_dump(mode="json")
    value["benchmarks"][0]["env"]["UB_RESULTDIR"] = "/tmp/escape"
    path = tmp_path / "bad.yaml"
    path.write_text(json.dumps(value))
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 1 and "reserved" in result.output


def test_status_uses_local_snapshot_without_connecting(config):
    store = Store(config.policy.state_dir)
    run_id = store.create(config)
    store.close()
    result = runner.invoke(app, ["status", run_id, "--state-dir", str(config.policy.state_dir)])
    assert result.exit_code == 0, result.output
    value = json.loads(result.output)
    assert value["run_id"] == run_id and len(value["jobs"]) == 2


def test_unknown_run_has_clean_error(tmp_path):
    result = runner.invoke(app, ["status", "missing", "--state-dir", str(tmp_path)])
    assert result.exit_code == 1 and "unknown run" in result.output


@pytest.mark.parametrize("command", ["resume", "fetch", "cancel"])
def test_control_commands_pass_readable_run_names(config, monkeypatch, command):
    store = Store(config.policy.state_dir)
    run_id = store.create(config)
    store.close()
    calls = []

    async def execute(name, state_dir, **options):
        calls.append((name, state_dir, options["fetch_only"], options["cancel"]))
        return "cancelled" if command == "cancel" else "succeeded"

    monkeypatch.setattr("autobench.services.execute_run", execute)
    result = runner.invoke(app, [command, run_id, "--state-dir", str(config.policy.state_dir)])
    assert result.exit_code == 0, result.output
    assert run_id in result.output
    assert calls == [(run_id, config.policy.state_dir, command == "fetch", command == "cancel")]


def test_web_command_launches_loopback_with_startup_options(tmp_path, monkeypatch):
    import uvicorn

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: calls.append((app, options)))
    result = runner.invoke(
        app,
        [
            "web",
            "--config",
            str(tmp_path / "example.yaml"),
            "--state-dir",
            str(tmp_path),
            "--port",
            "9876",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "http://127.0.0.1:9876" in result.output
    assert calls[0][1] == {"host": "127.0.0.1", "port": 9876, "access_log": False}
    assert runner.invoke(app, ["web", "--port", "0"]).exit_code != 0


@pytest.mark.parametrize(
    "host, normalized, browser_host",
    [
        ("192.168.1.100", "192.168.1.100", "192.168.1.100"),
        ("0.0.0.0", "0.0.0.0", "127.0.0.1"),
        ("::", "::", "[::1]"),
        ("::1", "::1", "[::1]"),
        ("2001:0DB8:0:0::1", "2001:db8::1", "[2001:db8::1]"),
    ],
)
def test_web_host_controls_listener_and_browser(host, normalized, browser_host, monkeypatch):
    import threading
    import webbrowser

    import uvicorn

    calls = []
    opened = []
    timers = []

    class Timer:
        def __init__(self, interval, function, args):
            self.function, self.args = function, args
            timers.append(interval)

        def start(self):
            self.function(*self.args)

    monkeypatch.setattr(uvicorn, "run", lambda app, **options: calls.append((app, options)))
    monkeypatch.setattr(webbrowser, "open", opened.append)
    monkeypatch.setattr(threading, "Timer", Timer)
    result = runner.invoke(app, ["web", "--host", host, "--port", "9876", "--open-browser"])
    assert result.exit_code == 0, result.output
    assert calls[0][1] == {"host": normalized, "port": 9876, "access_log": False}
    with TestClient(calls[0][0], base_url="http://localhost") as client:
        assert client.get("/api/info").json()["listen_host"] == normalized
    authority = f"[{normalized}]" if ":" in normalized else normalized
    assert f"http://{authority}:9876" in result.output
    assert opened == [f"http://{browser_host}:9876"]
    assert timers == [0.8]


@pytest.mark.parametrize("host", ["localhost", "example.com", "256.0.0.1", "[::1]", "", "bad::ip"])
def test_web_rejects_invalid_bind_address_before_launch(host, monkeypatch):
    import uvicorn

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append(args))
    result = runner.invoke(app, ["web", "--host", host])
    assert result.exit_code == 1 and "Error:" in result.output
    assert not calls


@pytest.mark.parametrize("verify_host_key", [True, False])
def test_validate_requires_hosts_only_when_verification_enabled(config, tmp_path, verify_host_key):
    data = config.model_dump(mode="json")
    data["machines"][0]["ssh"].update(
        known_hosts=str(tmp_path / "absent-hosts"), verify_host_key=verify_host_key
    )
    path = tmp_path / "verify.yaml"
    path.write_text(json.dumps(data))
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == (1 if verify_host_key else 0), result.output
    if not verify_host_key:
        assert "SHA256" in result.output
