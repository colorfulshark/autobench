import asyncio
import io
import stat
import threading
import time
import zipfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from test_scheduler import FakeRemote, World

from autobench.storage import Store
from autobench.web import create_app
from autobench.web.files import ConfigFiles, ConflictError


@pytest.fixture
def client(tmp_path, config):
    with TestClient(
        create_app(base_dir=tmp_path, state_dir=config.policy.state_dir),
        base_url="http://127.0.0.1:8765",
    ) as value:
        yield value


@pytest.fixture
def saved(client, config, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(config.model_dump_json())
    return client.post("/api/config/load", json={"path": str(path)}).json()


def reference(saved):
    return {key: saved[key] for key in ("path", "revision")}


def wait_operation(client, operation):
    deadline = time.monotonic() + 5
    while operation["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
        operation = client.get(f"/api/operations/{operation['id']}").json()
    assert operation["status"] == "completed", operation
    return operation["result"]


def test_config_roundtrip_preserves_raw_paths_types_and_overrides(client, tmp_path):
    text = """# keep this comment\nversion: 1
machines:
  - id: host
    ssh:
      host: localhost
      username: bench
      password: '123456'
      known_hosts: ./known_hosts
      proxy: {host: localhost, port: 1080}
    systems:
      - id: os
        boot_entry: "0001"
        ssh: {password: null, client_keys: [], proxy: null}
        env: {EMPTY: '', NUMBER: '3', LITERAL: '$PATH $(id) `id` 雪'}
benchmarks: [{id: ub, source_archive: ./source.tar.gz}]
"""
    parsed = client.post("/api/config/parse", json={"text": text}).json()["document"]
    rendered = client.post("/api/config/render", json={"document": parsed}).json()
    assert yaml.safe_load(rendered["text"]) == parsed
    assert rendered["document"]["machines"][0]["systems"][0]["ssh"] == {
        "password": None,
        "client_keys": [],
        "proxy": None,
    }
    response = client.post("/api/config/save", json={"path": "new.yaml", "text": text})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["text"] == text
    assert value["path"] == str(tmp_path / "new.yaml")
    assert "policy" not in value["document"]
    assert stat.S_IMODE((tmp_path / "new.yaml").stat().st_mode) == 0o600
    assert client.post("/api/config/load", json={"path": "new.yaml"}).json()["text"] == text


def test_save_incomplete_config_and_detect_external_changes(client, tmp_path):
    response = client.post(
        "/api/config/save", json={"path": "draft.yaml", "text": "machines: []\n"}
    )
    assert response.status_code == 200
    value = response.json()
    assert client.post("/api/config/validate", json=reference(value)).status_code == 422
    assert (
        client.post("/api/config/save", json={"path": "draft.yaml", "text": "{}"}).status_code
        == 409
    )
    path = tmp_path / "draft.yaml"
    path.write_text("# external\nmachines: []\n")
    assert (
        client.post("/api/config/save", json={**reference(value), "text": "{}"}).status_code == 409
    )
    assert client.post("/api/runs", json=reference(value)).status_code == 409
    assert client.post("/api/inspections", json=reference(value)).status_code == 409
    assert path.read_text().startswith("# external")


def test_atomic_save_failure_keeps_original_and_cleans_temp(tmp_path, monkeypatch):
    files = ConfigFiles(tmp_path)
    value = files.save("draft.yaml", "version: 1\n", None)

    def fail(*args):
        raise OSError("disk failure")

    monkeypatch.setattr("autobench.web.files.os.replace", fail)
    with pytest.raises(OSError, match="disk failure"):
        files.save("draft.yaml", "version: 2\n", value["revision"])
    assert (tmp_path / "draft.yaml").read_text() == "version: 1\n"
    assert list(tmp_path.iterdir()) == [tmp_path / "draft.yaml"]
    (tmp_path / "draft.yaml").unlink()
    with pytest.raises(ConflictError):
        files.save("draft.yaml", "version: 1\n", value["revision"])


@pytest.mark.parametrize("text", ["[]", "null", "a: &a [*a]", "date: 2026-01-01", "a: .nan"])
def test_reject_unrepresentable_editor_documents(client, text):
    assert client.post("/api/config/parse", json={"text": text}).status_code == 422


def test_yaml_errors_keep_source_and_hide_secret_snippets(client, tmp_path):
    text = 'password: "private-secret\n'
    (tmp_path / "broken.yaml").write_text(text)
    value = client.post("/api/config/load", json={"path": "broken.yaml"}).json()
    assert value["text"] == text and value["document"] is None
    assert "行" in value["parse_error"]
    response = client.post("/api/config/parse", json={"text": text})
    assert response.status_code == 422
    assert "行" in response.json()["detail"] and "private-secret" not in response.text
    assert (
        client.post("/api/config/save", json={"path": "invalid.yaml", "text": text}).status_code
        == 422
    )
    assert not (tmp_path / "invalid.yaml").exists()


def test_full_validation_uses_yaml_directory_and_never_rewrites(client, config, tmp_path):
    value = config.model_dump(mode="json")
    value["benchmarks"][0].pop("source_sha256")
    value["benchmarks"][0].pop("source_subdir")
    value["benchmarks"][0]["source_archive"] = "source.tar.gz"
    value["policy"]["state_dir"] = "state"
    value["policy"]["output_dir"] = "output"
    text = yaml.safe_dump(value)
    saved = client.post("/api/config/save", json={"path": "relative.yaml", "text": text}).json()
    response = client.post("/api/config/validate", json=reference(saved))
    assert response.status_code == 200, response.text
    assert response.json()["benchmarks"][0]["source_subdir"] == "UnixBench"
    assert len(response.json()["benchmarks"][0]["source_sha256"]) == 64
    assert response.json()["state_dir"] == str(tmp_path / "state")
    assert (tmp_path / "relative.yaml").read_text() == text


def test_local_access_guards_and_static_resources(client):
    for path in ("/", "/static/app.js", "/static/style.css"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/info").json()["schema"]["$defs"]["SOCKS5Proxy"]
    assert client.get("/api/info", headers={"Host": "attacker.example"}).status_code == 403
    assert client.get("/api/info", headers={"Origin": "http://attacker.example"}).status_code == 403
    assert client.post("/api/config/parse", data={"text": "{}"}).status_code == 415
    response = client.post(
        "/api/config/parse", json={"text": "{}"}, headers={"Origin": "http://attacker.example"}
    )
    assert response.status_code == 403
    assert client.get("/api/runs/not-an-id").status_code == 422
    assert client.get("/api/operations/missing").status_code == 404


def test_cli_history_status_events_and_downloads_are_safe(client, config, tmp_path):
    config.machines[0].ssh.password = "private-login-secret"
    store = Store(config.policy.state_dir)
    run_id = store.create(config)
    root = config.policy.output_dir / run_id
    store.snapshot(run_id, root)
    store.event(run_id, None, "first event private-login-secret")
    store.event(run_id, None, "second event")
    store.close()
    (root / "run.stdout").write_text("benchmark finished\n")
    (root / "report.html").write_text("<script>alert(1)</script>")
    (root / "outside").symlink_to(tmp_path / "known_hosts")
    (root / "linked-dir").symlink_to(tmp_path, target_is_directory=True)
    assert client.get("/api/runs").json()[0]["id"] == run_id
    response = client.get(f"/api/runs/{run_id}")
    assert response.status_code == 200
    assert "config" not in response.json() and "private-login-secret" not in response.text
    events = client.get(f"/api/runs/{run_id}/events").json()
    assert events[0]["message"] == "first event ********"
    assert (
        len(
            client.get(f"/api/runs/{run_id}/events", params={"after": events[0]["sequence"]}).json()
        )
        == 1
    )
    items = client.get(f"/api/runs/{run_id}/artifacts").json()
    assert all("linked-dir" not in item["path"] and item["path"] != "outside" for item in items)
    url = f"/api/runs/{run_id}/artifact"
    assert (
        client.get(url, params={"path": "run.stdout", "preview": True}).json()["text"]
        == "benchmark finished\n"
    )
    html = client.get(url, params={"path": "report.html"})
    assert html.headers["content-type"] == "application/octet-stream"
    assert "attachment" in html.headers["content-disposition"]
    assert client.get(url, params={"path": "report.html", "preview": True}).status_code == 415
    for path in ("../known_hosts", "/etc/passwd", "outside", "linked-dir/known_hosts"):
        assert client.get(url, params={"path": path}).status_code == 403
    archive = client.get(f"/api/runs/{run_id}/archive")
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        assert zipped.read(f"{run_id}/run.stdout") == b"benchmark finished\n"
        assert "private-login-secret" not in zipped.read(f"{run_id}/config.json").decode()
        assert not any(
            "linked-dir" in name or name.endswith("/outside") for name in zipped.namelist()
        )


def test_inspections_use_shared_read_only_logic(client, saved, config, monkeypatch):
    world = World(config)
    monkeypatch.setattr("autobench.scheduler.RemoteClient", world.factory)
    for kind in ("discover", "doctor"):
        response = client.post(
            "/api/inspections", json={**reference(saved), "kind": kind, "machine": "host1"}
        )
        assert response.status_code == 202, response.text
        result = wait_operation(client, response.json())
        assert result[0]["systems"][0]["current"]
        assert not result[0]["systems"][1]["dependencies_checked"]
    assert not world.jobs and not world.events
    assert (
        client.post("/api/inspections", json={**reference(saved), "machine": "unknown"}).status_code
        == 422
    )


def test_web_run_resume_uses_frozen_config_without_replay(client, saved, config, monkeypatch):
    world = World(config)
    world.crash_controller = True
    monkeypatch.setattr("autobench.scheduler.RemoteClient", world.factory)
    response = client.post("/api/runs", json=reference(saved))
    assert response.status_code == 202, response.text
    run = response.json()
    assert wait_operation(client, run["operation"]) == "paused"
    original = world.starts[0]
    Path(saved["path"]).unlink()
    response = client.post(
        f"/api/runs/{run['run_id']}/resume", json={"state_dir": run["state_dir"]}
    )
    assert wait_operation(client, response.json()) == "succeeded"
    assert world.starts.count(original) == 1
    assert world.jobs[original]["manifest"]["benchmark_env"]["SHARED"] == "benchmark"
    assert client.get(f"/api/runs/{run['run_id']}").json()["status"] == "succeeded"


def test_fetch_never_submits_or_switches(client, config, monkeypatch):
    world = World(config)
    monkeypatch.setattr("autobench.scheduler.RemoteClient", world.factory)
    store = Store(config.policy.state_dir)
    run_id = store.create(config)
    store.close()
    response = client.post(
        f"/api/runs/{run_id}/fetch", json={"state_dir": str(config.policy.state_dir)}
    )
    assert wait_operation(client, response.json()) == "paused"
    assert not world.jobs and not world.events


def test_active_external_controller_rejects_resume_but_persists_cancel(client, config):
    store = Store(config.policy.state_dir)
    run_id = store.create(config)
    body = {"state_dir": str(config.policy.state_dir)}
    with store.run_lock(run_id):
        for action in ("resume", "fetch"):
            assert client.post(f"/api/runs/{run_id}/{action}", json=body).status_code == 409
        response = client.post(f"/api/runs/{run_id}/cancel", json=body)
        assert response.status_code == 202
        assert response.json()["status"] == "cancel_requested"
        assert store.run(run_id)["cancel_requested"]
    store.close()


def test_cancel_active_web_run_stops_remote_and_recovers_logs(client, saved, config, monkeypatch):
    world = World(config)
    original_status = FakeRemote.status
    started = threading.Event()

    async def slow_status(self, job_id, directory):
        if world.jobs.get(job_id, {}).get("phase") == "active":
            started.set()
            await asyncio.sleep(0.01)
            return {"phase": "active", "state": {"status": "running"}}
        return await original_status(self, job_id, directory)

    monkeypatch.setattr(FakeRemote, "status", slow_status)
    monkeypatch.setattr("autobench.scheduler.RemoteClient", world.factory)
    value = client.post("/api/runs", json=reference(saved)).json()
    assert started.wait(2)
    url = f"/api/runs/{value['run_id']}"
    body = {"state_dir": value["state_dir"]}
    assert client.post(url + "/resume", json=body).status_code == 409
    result = client.post(url + "/cancel", json=body)
    assert result.status_code == 202 and result.json()["status"] == "cancel_requested"
    assert wait_operation(client, value["operation"]) == "cancelled"
    assert all(job["acked"] for job in world.jobs.values())
    detail = client.get(url).json()
    assert detail["cancel_requested"] and all(
        job["status"] == "cancelled" for job in detail["jobs"]
    )


def test_shutdown_releases_controller_without_cancel_and_restart_resumes(
    tmp_path, config, monkeypatch
):
    world = World(config)
    original_status = FakeRemote.status
    started = threading.Event()
    hold = True

    async def held_status(self, job_id, directory):
        if hold and world.jobs.get(job_id, {}).get("phase") == "active":
            started.set()
            await asyncio.Event().wait()
        return await original_status(self, job_id, directory)

    monkeypatch.setattr(FakeRemote, "status", held_status)
    monkeypatch.setattr("autobench.scheduler.RemoteClient", world.factory)
    path = tmp_path / "config.yaml"
    path.write_text(config.model_dump_json())
    app = create_app(base_dir=tmp_path, state_dir=config.policy.state_dir)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        saved = client.post("/api/config/load", json={"path": str(path)}).json()
        value = client.post("/api/runs", json=reference(saved)).json()
        assert started.wait(2)
        original = world.starts[0]
    store = Store(config.policy.state_dir)
    assert store.run(value["run_id"])["status"] == "paused"
    assert not store.run(value["run_id"])["cancel_requested"]
    with store.run_lock(value["run_id"]):
        pass
    store.close()
    assert world.jobs[original]["phase"] == "active"
    hold = False
    with TestClient(create_app(base_dir=tmp_path), base_url="http://127.0.0.1") as client:
        response = client.post(
            f"/api/runs/{value['run_id']}/resume", json={"state_dir": value["state_dir"]}
        )
        assert wait_operation(client, response.json()) == "succeeded"
    assert world.starts.count(original) == 1


@pytest.mark.parametrize("password", ["a", "1"])
def test_password_redaction_does_not_corrupt_identifiers_or_states(client, config, password):
    config.machines[0].ssh.password = password
    store = Store(config.policy.state_dir)
    run_id = store.create(config)
    store.close()
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["run_id"] == run_id and detail["status"] == "pending"
    assert detail["jobs"][0]["machine"] == "host1"
    operation = client.post(
        f"/api/runs/{run_id}/fetch", json={"state_dir": str(config.policy.state_dir)}
    ).json()
    assert wait_operation(client, operation) == "paused"


def test_malformed_host_is_rejected(client):
    for host in ("[broken", "user@127.0.0.1", "127.0.0.1:not-a-port"):
        assert client.get("/api/info", headers={"Host": host}).status_code == 403


@pytest.mark.parametrize(
    "listen_host, authority, allowed",
    [
        ("127.0.0.1", "192.168.1.100:8765", False),
        ("192.168.1.100", "192.168.1.100:8765", True),
        ("192.168.1.100", "192.168.1.101:8765", False),
        ("192.168.1.100", "localhost:8765", True),
        ("192.168.1.100", "127.0.0.1:8765", True),
        ("2001:db8::1", "[2001:0DB8:0:0::1]:8765", True),
        ("2001:db8::1", "[2001:db8::2]:8765", False),
        ("0.0.0.0", "192.168.1.100:8765", True),
        ("0.0.0.0", "192.168.1.101:8765", True),
        ("0.0.0.0", "localhost:8765", True),
        ("::", "[2001:db8::2]:8765", True),
        ("::", "[::1]:8765", True),
    ],
)
def test_listener_host_access_rules(tmp_path, listen_host, authority, allowed):
    with TestClient(
        create_app(base_dir=tmp_path, host=listen_host), base_url="http://localhost"
    ) as client:
        headers = {"Host": authority, "Origin": f"http://{authority}"}
        response = client.get("/api/info", headers=headers)
        assert response.status_code == (200 if allowed else 403)
        if allowed:
            assert response.json()["listen_host"] == listen_host
            assert (
                client.post("/api/config/parse", json={"text": "{}"}, headers=headers).status_code
                == 200
            )
            assert (
                client.post(
                    "/api/config/parse",
                    json={"text": "{}"},
                    headers={"Host": authority, "Origin": "http://attacker.example"},
                ).status_code
                == 403
            )
            assert (
                client.post("/api/config/parse", data={"text": "{}"}, headers=headers).status_code
                == 415
            )


@pytest.mark.parametrize("listen_host", ["0.0.0.0", "::"])
@pytest.mark.parametrize(
    "authority",
    [
        "attacker.example",
        "256.1.1.1",
        "[broken",
        "user@127.0.0.1",
        "127.0.0.1:no-port",
        "127.0.0.1:0",
        "127.0.0.1:65536",
        "127.0.0.1:",
        "127.0.0.1/",
        "127.0.0.1?",
        "localhost#",
    ],
)
def test_wildcard_listeners_still_reject_domains_and_invalid_host(tmp_path, listen_host, authority):
    with TestClient(
        create_app(base_dir=tmp_path, host=listen_host), base_url="http://localhost"
    ) as client:
        assert client.get("/api/info", headers={"Host": authority}).status_code == 403


def test_replaced_output_directory_cannot_redirect_artifact_access(client, config, tmp_path):
    store = Store(config.policy.state_dir)
    run_id = store.create(config)
    store.close()
    outside = tmp_path / "outside-results"
    root = outside / run_id
    root.mkdir(parents=True)
    (root / "private.txt").write_text("outside the frozen output directory")
    config.policy.output_dir.symlink_to(outside, target_is_directory=True)
    assert client.get(f"/api/runs/{run_id}/artifacts").status_code == 403
    assert client.get(f"/api/runs/{run_id}/archive").status_code == 403
    assert (
        client.get(f"/api/runs/{run_id}/artifact", params={"path": "private.txt"}).status_code
        == 403
    )


def test_file_browser_lists_directories_without_reading_contents(client, tmp_path):
    (tmp_path / "folder").mkdir()
    (tmp_path / "alpha.yaml").write_text("password: private-secret")
    (tmp_path / ".hidden").touch()
    response = client.get("/api/files", params={"path": str(tmp_path)})
    assert response.status_code == 200
    result = response.json()
    assert result["path"] == str(tmp_path) and result["parent"] == str(tmp_path.parent)
    assert result["entries"][0]["name"] == "folder"
    assert any(entry["name"] == ".hidden" for entry in result["entries"])
    assert "private-secret" not in response.text
    assert client.get("/api/files", params={"path": "alpha.yaml"}).json()["path"] == str(tmp_path)
    assert client.get("/api/files", params={"path": "absent"}).status_code == 404


def test_validation_errors_expose_paths_but_not_inputs(client, saved):
    document = saved["document"]
    document["machines"][0]["ssh"]["password"] = "private-probe-secret"
    document["machines"][0]["ssh"]["port"] = "private-probe-secret"
    saved = client.post(
        "/api/config/save", json={**reference(saved), "text": yaml.safe_dump(document)}
    ).json()
    result = client.post("/api/config/validate", json=reference(saved))
    assert result.status_code == 422
    assert result.json()["errors"][0]["path"] == ["machines", 0, "ssh", "port"]
    assert "private-probe-secret" not in result.text
    assert "input" not in result.json()["errors"][0]
    syntax = client.post("/api/config/parse", json={"text": "machines: [\n"}).json()
    assert syntax["errors"][0]["line"] == 2 and syntax["errors"][0]["column"] == 1


def test_draft_probe_needs_only_connection_and_resolves_paths(client, tmp_path, monkeypatch):
    received = []

    class Client:
        def __init__(self, ssh, policy):
            received.append((ssh, policy))

        async def probe(self, requirements):
            return {
                "hostname": "test-host",
                "boot_id": "boot-1",
                "efi": {"current": "0001", "entries": {"0001": {"active": True, "label": "Linux"}}},
                "python": [3, 11],
                "systemd_available": True,
                "systemd_version": 250,
                "commands": {name: "/bin/" + name for name in requirements},
                "disk_free": 10**10,
            }

    monkeypatch.setattr("autobench.services.SSHClient", Client)
    folder = tmp_path / "configs"
    folder.mkdir()
    (folder / "known_hosts").touch()
    (folder / "id_key").touch()
    body = {
        "config_path": "configs/new.yaml",
        "ssh": {
            "host": "localhost",
            "username": "bench",
            "password": "secret-not-returned",
            "known_hosts": "known_hosts",
            "client_keys": ["id_key"],
        },
        "connect_timeout": 2,
    }
    response = client.post("/api/connections/probe", json=body)
    assert response.status_code == 202, response.text
    result = wait_operation(client, response.json())
    assert result["probe"]["efi"]["current"] == "0001"
    assert all(check["status"] == "passed" for check in result["checks"])
    assert received[0][0].known_hosts == folder / "known_hosts"
    assert received[0][0].client_keys == [folder / "id_key"]
    assert received[0][1].connect_timeout == 2
    assert not (folder / "new.yaml").exists() and client.get("/api/runs").json() == []
    assert "secret-not-returned" not in client.get("/api/operations").text


def test_draft_probe_reports_missing_trust_file_as_actionable_check(client):
    result = client.post(
        "/api/connections/probe",
        json={
            "ssh": {
                "host": "localhost",
                "username": "bench",
                "known_hosts": "absent",
                "password": "no-echo",
            }
        },
    )
    checks = wait_operation(client, result.json())["checks"]
    assert checks[0]["code"] == "known_hosts_missing"
    assert checks[0]["path"] == ["ssh", "known_hosts"]
    assert "no-echo" not in client.get("/api/operations").text


def test_run_status_explains_system_stabilization_wait(client, config):
    store = Store(config.policy.state_dir)
    run_id = store.create(config)
    job = store.jobs(run_id)[0]
    store.update(job["id"], ready_at=time.time() + 30)
    result = client.get(f"/api/runs/{run_id}").json()
    assert result["jobs"][0]["phase"] == "stabilizing"
    assert 1 <= result["jobs"][0]["wait_seconds"] <= 30
    assert store.job(job["id"])["status"] == "pending"
    store.close()


def test_missing_connection_file_error_locates_advanced_field(client, saved):
    data = saved["document"]
    data["machines"][0]["ssh"].update(known_hosts="missing-hosts", password="a")
    saved = client.post(
        "/api/config/save", json={**reference(saved), "text": yaml.safe_dump(data)}
    ).json()
    response = client.post("/api/config/validate", json=reference(saved))
    assert response.status_code == 422
    assert response.json()["errors"][0]["path"] == ["machines", 0, "ssh", "known_hosts"]
    assert response.json()["errors"][0]["code"] == "file_missing"


def test_validation_ignores_hosts_only_for_unverified_connections(client, saved):
    document = saved["document"]
    document["machines"][0]["ssh"].update(verify_host_key=False, known_hosts="missing-hosts")
    saved = client.post(
        "/api/config/save", json={**reference(saved), "text": yaml.safe_dump(document)}
    ).json()
    assert client.post("/api/config/validate", json=reference(saved)).status_code == 200
    document["machines"][0]["systems"][1]["ssh"] = {"verify_host_key": True}
    saved = client.post(
        "/api/config/save", json={**reference(saved), "text": yaml.safe_dump(document)}
    ).json()
    result = client.post("/api/config/validate", json=reference(saved))
    assert result.status_code == 422
    assert result.json()["errors"][0]["path"] == ["machines", 0, "systems", 1, "ssh", "known_hosts"]
    document["machines"][0]["systems"][1]["ssh"] = {}
    document["machines"][0]["ssh"]["client_keys"] = ["missing-private-key"]
    saved = client.post(
        "/api/config/save", json={**reference(saved), "text": yaml.safe_dump(document)}
    ).json()
    result = client.post("/api/config/validate", json=reference(saved))
    assert result.status_code == 422
    assert result.json()["errors"][0]["path"] == ["machines", 0, "ssh", "client_keys", 0]


def test_draft_probe_ignores_absent_hosts_and_reports_actual_mode(client, tmp_path, monkeypatch):
    from autobench.errors import AuthenticationFailed

    class Client:
        def __init__(self, ssh, policy):
            assert ssh.verify_host_key is False

        async def probe(self, requirements):
            return {
                "efi": {"current": "0001"},
                "python": [3, 11],
                "systemd_available": True,
                "systemd_version": 250,
                "disk_free": 10**10,
                "commands": {name: "/bin/" + name for name in requirements},
            }

    monkeypatch.setattr("autobench.services.SSHClient", Client)
    body = {
        "ssh": {
            "host": "localhost",
            "username": "bench",
            "password": "no-echo",
            "verify_host_key": False,
            "known_hosts": "absent-hosts",
        }
    }
    response = client.post("/api/connections/probe", json=body)
    result = wait_operation(client, response.json())
    assert result["checks"][0]["message"] == "连接成功，已忽略主机密钥校验"
    assert result["checks"][0]["status"] == "passed"
    assert "no-echo" not in client.get("/api/operations").text
    body["ssh"]["client_keys"] = ["absent-key"]
    result = wait_operation(client, client.post("/api/connections/probe", json=body).json())
    assert result["checks"][0]["code"] == "key_missing"

    class BadPassword(Client):
        async def probe(self, requirements):
            raise AuthenticationFailed("wrong password")

    monkeypatch.setattr("autobench.services.SSHClient", BadPassword)
    body["ssh"]["client_keys"] = []
    result = wait_operation(client, client.post("/api/connections/probe", json=body).json())
    assert result["checks"][0]["code"] == "authentication"
