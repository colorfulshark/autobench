import re
import stat
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from autobench.storage import RUN_ID_PATTERN, Store

NAME = "2026-11-03_14-30-25"


@pytest.fixture
def frozen_clock(monkeypatch):
    # mktime interprets the tuple in the controller's local timezone.
    timestamp = time.mktime((2026, 11, 3, 14, 30, 25, 0, 0, -1))
    monkeypatch.setattr("autobench.storage.time.time", lambda: timestamp)
    return timestamp


def test_local_timestamp_and_persisted_internal_ids(config, frozen_clock):
    store = Store(config.policy.state_dir)
    run_id = store.create(config)
    jobs = store.jobs(run_id)
    assert run_id == NAME
    assert store.run(run_id)["created"] == frozen_clock
    assert stat.S_IMODE((config.policy.output_dir / run_id).stat().st_mode) == 0o700
    assert all(uuid.UUID(job["id"]).version == 4 for job in jobs)
    store.close()
    reopened = Store(config.policy.state_dir)
    assert reopened.runs()[0]["id"] == run_id
    assert reopened.jobs(run_id) == jobs
    reopened.close()


def test_same_second_names_and_suffix_beyond_99(config, frozen_clock):
    store = Store(config.policy.state_dir)
    expected = [NAME, *(f"{NAME}-{i:02d}" for i in range(2, 102))]
    names = [store.create(config) for _ in expected]
    assert names == expected
    assert all(re.fullmatch(RUN_ID_PATTERN, name) for name in names)
    assert all((config.policy.output_dir / name).is_dir() for name in names)
    store.close()


def test_name_in_database_remains_reserved_when_directory_is_removed(config, frozen_clock):
    store = Store(config.policy.state_dir)
    assert store.create(config) == NAME
    (config.policy.output_dir / NAME).rmdir()
    assert store.create(config) == f"{NAME}-02"
    assert len(store.runs()) == 2
    store.close()


def test_existing_results_files_and_broken_links_are_never_overwritten(config, frozen_clock):
    output = config.policy.output_dir
    root = output / NAME
    root.mkdir(parents=True)
    (root / "keep.txt").write_text("existing results")
    (output / f"{NAME}-02").write_text("existing file")
    (output / f"{NAME}-03").symlink_to(output / "missing")
    store = Store(config.policy.state_dir)
    assert store.create(config) == f"{NAME}-04"
    assert (root / "keep.txt").read_text() == "existing results"
    assert (output / f"{NAME}-02").read_text() == "existing file"
    assert (output / f"{NAME}-03").is_symlink()
    store.close()


@pytest.mark.parametrize("shared_state", [True, False])
def test_concurrent_controllers_reserve_unique_names(config, frozen_clock, shared_state):
    count = 6
    configs = []
    for index in range(count):
        item = config.model_copy(deep=True)
        if not shared_state:
            item.policy.state_dir = config.policy.state_dir / str(index)
        # Initialize the databases before exercising concurrent creation.
        Store(item.policy.state_dir).close()
        configs.append(item)
    barrier = threading.Barrier(count, timeout=10)

    def create(item):
        store = Store(item.policy.state_dir)
        try:
            barrier.wait()
            name = store.create(item)
            return name, [job["id"] for job in store.jobs(name)]
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=count) as pool:
        runs = list(pool.map(create, configs))
    expected = {NAME, *(f"{NAME}-{i:02d}" for i in range(2, count + 1))}
    assert {name for name, _ in runs} == expected
    assert {path.name for path in config.policy.output_dir.iterdir()} == expected
    assert len({job_id for _, ids in runs for job_id in ids}) == count * 2


def test_failed_creation_rolls_back_database_and_directory(config, frozen_clock, monkeypatch):
    store = Store(config.policy.state_dir)
    original_uuid4 = uuid.uuid4
    calls = 0

    def fail_second_job():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("failed to create second job")
        return original_uuid4()

    with monkeypatch.context() as patch:
        patch.setattr("autobench.storage.uuid.uuid4", fail_second_job)
        with pytest.raises(RuntimeError, match="second job"):
            store.create(config)
    assert store.runs() == []
    assert store.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert list(config.policy.output_dir.iterdir()) == []
    assert store.create(config) == NAME
    store.close()
