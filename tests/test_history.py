import io
import sqlite3
import time
import zipfile
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from test_reports import unixbench_text, write_round

from autobench import history
from autobench.errors import AutoBenchError
from autobench.storage import Store
from autobench.web import create_app


@pytest.fixture
def client(tmp_path, config):
    with TestClient(
        create_app(base_dir=tmp_path, state_dir=config.policy.state_dir),
        base_url="http://127.0.0.1:8765",
    ) as client:
        yield client


def completed_run(store, config, scores=(10,), *, status="succeeded"):
    config = config.model_copy(deep=True)
    config.machines[0].systems = config.machines[0].systems[:1]
    config.benchmarks[0].repetitions = len(scores)
    run_id = store.create(config)
    root = store.result_directory(run_id)
    jobs = store.jobs(run_id)
    for job, score in zip(jobs, scores, strict=True):
        store.update(job["id"], status)
        if score is not None:
            write_round(root, job["system"], job["repetition"], unixbench_text(score, score))
    store.event(run_id, jobs[0]["id"], "recorded test event secret-history-password")
    store.set_run_status(run_id, status)
    return run_id, root


def test_rename_overrides_only_the_record_name_and_can_clear_it(client, config):
    config.policy.plan_name = "原测试计划"
    with closing(Store(config.policy.state_dir)) as store:
        run_id, root = completed_run(store, config)
        other, _ = completed_run(store, config)
        store.set_run_status(run_id, "running")
        original = store.run(run_id)
        # Simulate an existing database from before names could be edited in history.
        with store.db:
            store.db.execute("DROP TABLE run_plan_names")

    body = {"state_dir": str(config.policy.state_dir)}
    detail_url = f"/api/runs/{run_id}"
    url = f"{detail_url}/rename"

    def names():
        return {record["id"]: record["plan_name"] for record in client.get("/api/runs").json()}

    assert names() == {run_id: "原测试计划", other: "原测试计划"}
    assert client.get(detail_url).json()["plan_name"] == "原测试计划"
    for invalid in (3, "第一行\n第二行"):
        response = client.post(url, json={**body, "plan_name": invalid})
        assert response.status_code == 422
        assert response.json()["errors"][0]["path"] == ["plan_name"]

    with closing(Store(config.policy.state_dir)) as store, store.run_lock(run_id):
        response = client.post(url, json={**body, "plan_name": "  内核对比 B  "})
        assert response.json() == {"run_id": run_id, "plan_name": "内核对比 B"}
        assert names() == {run_id: "内核对比 B", other: "原测试计划"}
        assert client.get(detail_url).json()["plan_name"] == "内核对比 B"
        assert store.run(run_id) == original
        assert store.result_directory(run_id) == root

    assert client.post(url, json={**body, "plan_name": "  "}).json()["plan_name"] is None
    assert names() == {run_id: None, other: "原测试计划"}
    assert client.get(detail_url).json()["plan_name"] is None
    with closing(Store(config.policy.state_dir)) as store:
        store.set_run_status(run_id, "succeeded")
        assert client.post(f"/api/runs/{run_id}/delete", json=body).status_code == 200
        assert client.post(url, json={**body, "plan_name": "新版"}).status_code == 404
        assert store.db.execute("SELECT * FROM run_plan_names").fetchall() == []


def test_delete_removes_only_the_selected_record_and_files(client, config, tmp_path):
    store = Store(config.policy.state_dir)
    run_id, root = completed_run(store, config)
    other, other_root = completed_run(store, config)
    outside = tmp_path / "outside.txt"
    outside.write_text("preserved")
    (root / "link").symlink_to(outside)
    response = client.post(f"/api/runs/{run_id}/delete", json={"state_dir": str(store.directory)})
    assert response.json() == {"run_id": run_id, "cleanup_pending": False}
    assert store.jobs(run_id) == [] and store.events(run_id) == []
    assert not root.exists() and outside.read_text() == "preserved"
    assert other_root.is_dir() and store.jobs(other) and store.events(other)
    other_root.rename(tmp_path / "already-moved")
    assert (
        client.post(
            f"/api/runs/{other}/delete", json={"state_dir": str(store.directory)}
        ).status_code
        == 200
    )
    assert store.runs() == []
    store.close()


def test_deleted_ids_are_not_reused(client, config, monkeypatch):
    now = time.time()
    monkeypatch.setattr("autobench.storage.time.time", lambda: now)
    store = Store(config.policy.state_dir)
    run_id, _ = completed_run(store, config)
    body = {"state_dir": str(store.directory)}
    assert client.post(f"/api/runs/{run_id}/delete", json=body).status_code == 200
    new_id, _ = completed_run(store, config)
    assert new_id == f"{run_id}-02"
    assert client.post(f"/api/runs/{run_id}/delete", json=body).status_code == 404
    assert store.runs()[0]["id"] == new_id
    store.close()


def test_history_mutations_reject_unsealed_busy_and_redirected_runs(client, config, tmp_path):
    store = Store(config.policy.state_dir)
    run_id, root = completed_run(store, config)
    other, _ = completed_run(store, config)
    pending = store.create(config)
    body = {"state_dir": str(store.directory)}
    delete_url = f"/api/runs/{run_id}/delete"
    assert client.post(f"/api/runs/{pending}/delete", json=body).status_code == 409
    assert (
        client.post("/api/runs/merge", json={**body, "run_ids": [run_id, run_id]}).status_code
        == 422
    )
    with store.run_lock(run_id):
        assert client.post(delete_url, json=body).status_code == 409
        assert (
            client.post("/api/runs/merge", json={**body, "run_ids": [run_id, other]}).status_code
            == 409
        )

    job = store.jobs(run_id)[0]
    store.update(job["id"], "failed", remote_dir="test/job", archived=False)
    store.set_run_status(run_id, "failed")
    assert not next(item for item in client.get("/api/runs").json() if item["id"] == run_id)[
        "can_merge"
    ]
    assert client.post(delete_url, json=body).status_code == 409
    store.update(job["id"], archived=True)
    moved = tmp_path / "moved"
    root.rename(moved)
    root.symlink_to(moved)
    assert client.post(delete_url, json=body).status_code == 403
    assert (
        client.post("/api/runs/merge", json={**body, "run_ids": [run_id, other]}).status_code == 403
    )
    assert moved.is_dir() and root.is_symlink() and len(store.runs()) == 3
    assert (
        client.post(delete_url, json=body, headers={"Origin": "http://example.invalid"}).status_code
        == 403
    )
    store.close()


def test_delete_rejects_overlapping_result_directories(client, config):
    store = Store(config.policy.state_dir)
    run_id, root = completed_run(store, config)
    body = {"state_dir": str(store.directory)}
    nested = config.model_copy(deep=True)
    nested.policy.output_dir = root / "nested"
    nested_id, nested_root = completed_run(store, nested)
    assert client.post(f"/api/runs/{run_id}/delete", json=body).status_code == 403
    assert nested_root.is_dir()
    assert client.post(f"/api/runs/{nested_id}/delete", json=body).status_code == 403
    assert {row["id"] for row in store.runs()} == {run_id, nested_id}
    store.close()


def test_delete_recovers_before_and_after_database_commit(client, config, monkeypatch):
    store = Store(config.policy.state_dir)
    run_id, root = completed_run(store, config)
    body = {"state_dir": str(store.directory)}
    with store.db:
        change = history._plan_change(store, run_id, "delete", root)
    nested = config.model_copy(deep=True)
    nested.policy.output_dir = root / "nested"
    with pytest.raises(AutoBenchError, match="待清理"):
        store.create(nested)
    root.rename(root.with_name(change["stage"]))
    assert client.get("/api/runs").json()[0]["id"] == run_id
    assert root.is_dir() and not root.with_name(change["stage"]).exists()
    with store.db:
        store.db.execute("""
            CREATE TRIGGER interrupt_delete BEFORE DELETE ON runs
            BEGIN SELECT RAISE(ABORT, 'test failure'); END
        """)
    with pytest.raises(sqlite3.IntegrityError, match="test failure"):
        history.delete(store, run_id)
    assert root.is_dir() and store.jobs(run_id) and store.events(run_id)
    with store.db:
        store.db.execute("DROP TRIGGER interrupt_delete")

    def denied(*args, **kwargs):
        raise PermissionError("test cleanup failure")

    url = f"/api/runs/{run_id}/delete"
    with monkeypatch.context() as patch:
        patch.setattr(history.os, "rename", denied)
        assert client.post(url, json=body).status_code == 403
        assert root.is_dir() and store.jobs(run_id)
    with monkeypatch.context() as patch:
        patch.setattr(history.shutil, "rmtree", denied)
        assert client.post(url, json=body).json()["cleanup_pending"]
        assert len(list(root.parent.glob(".autobench-delete-*"))) == 1
    assert client.get("/api/runs").json() == []
    assert not list(root.parent.glob(".autobench-delete-*"))
    store.close()


def test_merge_deduplicates_origins_and_reports_original_rounds(client, config):
    config.machines[0].ssh.password = "secret-history-password"
    config.benchmarks[0].source_sha256 = "a" * 64
    store = Store(config.policy.state_dir)
    a, a_root = completed_run(store, config, [10])
    different_output = config.model_copy(deep=True)
    different_output.policy.output_dir /= "another-directory"
    b, b_root = completed_run(store, different_output, [20, 60, None], status="failed")
    changed = config.model_copy(deep=True)
    changed.benchmarks[0].args = ["-c", "1"]
    c, c_root = completed_run(store, changed, [80])
    (a_root / "config.json").write_text(config.model_dump_json())
    (a_root / "same-name.log").write_text("from A")
    (b_root / "same-name.log").write_text("from B")
    (c_root / "hidden-link").symlink_to(a_root / "same-name.log")
    warmup = a_root / "host1/os1/ub/round-001/warmup/results"
    warmup.mkdir(parents=True)
    (warmup / "report").write_text(unixbench_text(999, 999))
    body = {"state_dir": str(store.directory)}

    def merge(*ids):
        response = client.post("/api/runs/merge", json={**body, "run_ids": ids})
        assert response.status_code == 201, response.text
        return response.json()["run_id"]

    ab, bc = merge(a, b), merge(b, c)
    runs = {row["id"]: row for row in store.runs()}
    assert set(runs) == {a, b, c, ab, bc}
    assert runs[ab]["plan_name"] is None and runs[bc]["plan_name"] is None
    response = client.post(f"/api/runs/{ab}/rename", json={**body, "plan_name": "合并基准"})
    assert response.json() == {"run_id": ab, "plan_name": "合并基准"}
    assert {record["id"]: record["plan_name"] for record in store.runs()}[ab] == "合并基准"
    assert client.get(f"/api/runs/{ab}").json()["plan_name"] == "合并基准"
    assert store.run(ab)["config"] == "{}"
    for original in (a, b, c):
        assert client.post(f"/api/runs/{original}/delete", json=body).status_code == 200
    merged = merge(ab, bc)
    url = f"/api/runs/{merged}"
    data = client.get(url).json()
    assert data["status"] == "merged"
    assert [source["run_id"] for source in data["sources"]] == [a, b, c]
    assert len(data["jobs"]) == 5
    assert {job["source_run_id"] for job in data["jobs"]} == {a, b, c}
    events = client.get(url + "/events").json()
    assert {event["source_run_id"] for event in events} == {a, b, c}
    assert {event["message"] for event in events} == {"recorded test event ********"}
    assert {event["job_id"] for event in events} <= {job["id"] for job in data["jobs"]}

    report = client.get(url + "/report").json()
    first, second = report["groups"]
    assert (first["planned_runs"], first["parsed_runs"]) == (4, 3)
    assert (second["planned_runs"], second["parsed_runs"]) == (1, 1)
    cells = report["profiles"][0]["overall"]
    assert cells[first["id"]]["mean"] == 30 and cells[second["id"]]["mean"] == 80
    workbook = load_workbook(io.BytesIO(client.get(url + "/report.xlsx").content))
    assert {"30.00 (0.00%)", "80.00 (+166.67%)"} <= {
        cell.value for cell in workbook["Summary"]["B"]
    }
    with zipfile.ZipFile(io.BytesIO(client.get(url + "/archive").content)) as zipped:
        assert zipped.read(f"{merged}/sources/{a}/same-name.log") == b"from A"
        assert zipped.read(f"{merged}/sources/{b}/same-name.log") == b"from B"
        assert not any(name.endswith(("config.json", "hidden-link")) for name in zipped.namelist())
        assert b"secret-history-password" not in zipped.read(f"{merged}/sources/{a}/events.json")
    assert client.post(url + "/fetch", json=body).status_code == 409
    assert client.post(url + "/resume", json=body).status_code == 409
    assert client.post(url + "/cancel", json=body).status_code == 409
    assert client.post(url + "/delete", json=body).status_code == 200
    assert {row["id"] for row in store.runs()} == {ab, bc}
    store.close()


def test_interrupted_merge_cleans_staging_and_published_uncommitted_files(
    client, config, monkeypatch
):
    store = Store(config.policy.state_dir)
    a, _ = completed_run(store, config)
    b, _ = completed_run(store, config)
    body = {"state_dir": str(store.directory), "run_ids": [a, b]}

    def fail(*args):
        raise PermissionError("test merge write failure")

    output = store.directory / "merged-results"
    for step in ("_copy_sources", "_insert_snapshot"):
        with monkeypatch.context() as patch:
            patch.setattr(history, step, fail)
            assert client.post("/api/runs/merge", json=body).status_code == 403
        assert list(output.iterdir()) == []
        assert {row["id"] for row in store.runs()} == {a, b}
    store.close()
