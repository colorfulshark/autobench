import asyncio
import copy
import json
from pathlib import PurePosixPath

import pytest
from conftest import assert_no_removed_outputs

from autobench.errors import ConnectionLost
from autobench.scheduler import Scheduler
from autobench.storage import Store


@pytest.mark.parametrize(
    ("probe", "expected"),
    [
        ({"efi": {"current": "0001"}}, True),
        ({"efi": {"current": "0002"}}, False),
        ({"efi": {}}, False),
        ({}, False),
    ],
)
def test_target_matches_current_boot_entry(config, probe, expected):
    assert Scheduler.is_target(probe, config.machines[0].systems[0]) is expected


class World:
    def __init__(self, config):
        self.config = config
        self.current = {m.id: m.systems[0] for m in config.machines}
        self.boot = {m.id: "boot-1" for m in config.machines}
        self.jobs = {}
        self.starts = []
        self.events = []
        self.drop_start = False
        self.crash_controller = False
        self.drop_download = False
        self.uncertain = False
        self.fail = False
        self.active = 0
        self.max_active = 0
        self.common_downloaded = set()

    def factory(self, machine, system, policy):
        return FakeRemote(self, machine, system)


class FakeRemote:
    def __init__(self, world, machine, system):
        self.world, self.machine, self.system = world, machine, system

    async def probe(self, requirements=None):
        current = self.world.current[self.machine.id]
        return {
            "boot_id": self.world.boot[self.machine.id],
            "home": "/home/bench",
            "python": [3, 8, 0],
            "systemd_version": 245,
            "systemd_available": True,
            "disk_free": 10**10,
            "commands": {name: "/bin/" + name for name in requirements or []},
            "efi": {
                "current": current.boot_entry,
                "entries": {s.boot_entry: {"active": True} for s in self.machine.systems},
            },
        }

    def root(self, probe):
        return "/home/bench/.local/state/autobench"

    async def switch(self, root, previous_boot_id, boot_entry, token):
        assert self.world.boot[self.machine.id] == previous_boot_id
        assert all(
            j.get("acked")
            for j in self.world.jobs.values()
            if j["manifest"]["machine"] == self.machine.id
        )
        self.world.events.append((self.machine.id, "switch"))
        self.world.current[self.machine.id] = next(
            s for s in self.machine.systems if s.boot_entry == boot_entry
        )
        self.world.boot[self.machine.id] += "-new"
        raise ConnectionLost("reboot dropped SSH")

    async def stage(self, job_id, directory, manifest, source):
        self.world.jobs.setdefault(job_id, {"phase": "staged", "manifest": copy.deepcopy(manifest)})

    async def start(self, job_id, directory):
        assert job_id not in self.world.starts
        self.world.starts.append(job_id)
        self.world.events.append((self.machine.id, "start"))
        self.world.jobs[job_id]["phase"] = "active"
        self.world.jobs[job_id]["polls"] = 0
        self.world.active += 1
        self.world.max_active = max(self.world.max_active, self.world.active)
        if self.world.crash_controller:
            self.world.crash_controller = False
            raise asyncio.CancelledError()
        if self.world.drop_start:
            self.world.drop_start = False
            raise ConnectionLost("start reply lost")

    async def status(self, job_id, directory):
        job = self.world.jobs.get(job_id)
        if job is None:
            return {"phase": "absent"}
        if self.world.uncertain and job["phase"] != "staged":
            return {"phase": "uncertain"}
        if job["phase"] == "active":
            job["polls"] += 1
            if job["polls"] >= 2:
                job["phase"] = "terminal"
                job["outcome"] = "failed" if self.world.fail else "succeeded"
        return {
            "phase": job["phase"],
            "state": {"status": job.get("outcome", "running")},
            "inventory": {"schema_version": 1, "files": []},
            "common": {
                "directory": self.root({}) + "/builds/" + job["manifest"]["build_id"],
                "inventory": {"schema_version": 1, "files": []},
            },
        }

    async def download(self, directory, inventory, destination):
        if self.world.drop_download:
            self.world.drop_download = False
            raise ConnectionLost("download interrupted")
        destination.mkdir(parents=True, exist_ok=True)
        if PurePosixPath(directory).parent.name == "builds":
            (destination / "build.json").write_text(
                json.dumps({"build_id": PurePosixPath(directory).name})
            )
            self.world.common_downloaded.add(PurePosixPath(directory).name)
            self.world.events.append((self.machine.id, "common_download"))
            return
        job = self.world.jobs[PurePosixPath(directory).name]
        job["downloaded"] = True
        self.world.events.append((self.machine.id, "download"))

    async def acknowledge(self, job_id, directory):
        job = self.world.jobs[job_id]
        assert job["downloaded"]
        assert job["manifest"]["build_id"] in self.world.common_downloaded
        if not job.get("acked"):
            self.world.active -= 1
        job["acked"] = True
        self.world.events.append((self.machine.id, "ack"))

    async def cancel(self, job_id, directory):
        job = self.world.jobs.get(job_id)
        if job is None:
            return {"phase": "absent"}
        job["phase"], job["outcome"] = "terminal", "cancelled"
        return await self.status(job_id, directory)


def setup(config):
    store = Store(config.policy.state_dir)
    run_id = store.create(config)
    world = World(config)
    return store, run_id, world


async def execute(store, run_id, world, **kwargs):
    return await Scheduler(
        store, run_id, client_factory=world.factory, report=lambda message: None
    ).execute(**kwargs)


async def test_warmup_only_on_first_round_of_each_enabled_group(config):
    enabled = config.benchmarks[0]
    enabled.repetitions = 3
    disabled = enabled.model_copy(deep=True)
    disabled.id = "no-warmup"
    disabled.warmup = False
    config.benchmarks.append(disabled)
    store, run_id, world = setup(config)
    try:
        assert await execute(store, run_id, world) == "succeeded"
        assert len(store.jobs(run_id)) == len(world.starts) == 12
        for job in world.jobs.values():
            manifest = job["manifest"]
            assert manifest["warmup"] is (
                manifest["benchmark"] == enabled.id and manifest["repetition"] == 1
            )
        assert len({j["manifest"]["build_id"] for j in world.jobs.values()}) == 4
        assert len(list((config.policy.output_dir / run_id).rglob("round-*"))) == 12
    finally:
        store.close()


async def test_legacy_frozen_run_does_not_gain_warmup(config):
    store, run_id, world = setup(config)
    try:
        frozen = json.loads(store.run(run_id)["config"])
        del frozen["benchmarks"][0]["warmup"]
        with store.db:
            store.db.execute("UPDATE runs SET config=? WHERE id=?", (json.dumps(frozen), run_id))
        assert await execute(store, run_id, world) == "succeeded"
        assert all(job["manifest"]["warmup"] is False for job in world.jobs.values())
    finally:
        store.close()


@pytest.mark.parametrize("failed", [True, False])
async def test_warmup_status_resume_and_failure_follow_existing_job_policy(
    config, monkeypatch, failed
):
    config.benchmarks[0].repetitions = 2
    store, run_id, world = setup(config)
    original_status = FakeRemote.status

    async def warming_up(self, job_id, directory):
        result = await original_status(self, job_id, directory)
        if result["phase"] == "active":
            result["state"]["status"] = "warming_up"
        return result

    monkeypatch.setattr(FakeRemote, "status", warming_up)
    world.fail = failed
    reports = []

    def stop_controller(message):
        reports.append(message)
        if "预热中" in message:
            raise asyncio.CancelledError()

    try:
        scheduler = Scheduler(store, run_id, client_factory=world.factory, report=stop_controller)
        assert await scheduler.execute() == "paused"
        first = store.jobs(run_id)[0]
        assert first["status"] == "warming_up"
        assert first["details"]["manifest"]["warmup"] is True
        manifest = copy.deepcopy(first["details"]["manifest"])
        # Resuming must reconcile the original service, including its warmup.
        config.benchmarks[0].warmup = False
        assert await execute(store, run_id, world) == ("failed" if failed else "succeeded")
        assert world.starts.count(first["id"]) == 1
        assert world.jobs[first["id"]]["manifest"] == manifest
        assert any("预热中" in message for message in reports)
        if failed:
            assert world.starts == [first["id"]]
            assert all(job["status"] == "blocked" for job in store.jobs(run_id)[1:])
        else:
            assert len(world.starts) == 4
    finally:
        store.close()


async def test_lost_start_reply_and_transfer_resume_never_duplicate(config, monkeypatch):
    store, run_id, world = setup(config)
    world.drop_start = world.drop_download = True
    monkeypatch.setattr("autobench.scheduler.random.uniform", lambda *a: 0)
    assert await execute(store, run_id, world) == "succeeded"
    assert len(world.starts) == 2 and len(set(world.starts)) == 2
    switch_index = world.events.index(("host1", "switch"))
    assert world.events[switch_index - 1] == ("host1", "ack")
    assert all(j["details"]["archived"] for j in store.jobs(run_id))
    assert world.max_active == 1
    store.close()


async def test_controller_crash_then_frozen_resume(config):
    store, run_id, world = setup(config)
    world.crash_controller = True
    assert await execute(store, run_id, world) == "paused"
    original = world.starts[0]
    destinations = {
        job["id"]: Scheduler(store, run_id).destination(job) for job in store.jobs(run_id)
    }
    # Neither modifying the caller's config nor reconstructing the scheduler changes the manifest.
    config.benchmarks[0].env["SHARED"] = "new-value"
    store.close()
    store = Store(config.policy.state_dir)
    assert await execute(store, run_id, world) == "succeeded"
    assert world.starts.count(original) == 1
    assert world.jobs[original]["manifest"]["benchmark_env"]["SHARED"] == "benchmark"
    assert {job["id"] for job in store.jobs(run_id)} == set(destinations)
    for job in store.jobs(run_id):
        assert job["details"]["output"] == str(destinations[job["id"]])
        assert destinations[job["id"]].is_dir()
    store.close()


async def test_readable_archives_separate_machines_systems_benchmarks_and_rounds(config):
    other_machine = config.machines[0].model_copy(deep=True, update={"id": "host2"})
    config.machines.append(other_machine)
    config.benchmarks[0].repetitions = 2
    config.benchmarks.append(config.benchmarks[0].model_copy(deep=True, update={"id": "ub2"}))
    store, run_id, world = setup(config)
    assert await execute(store, run_id, world) == "succeeded"
    root = config.policy.output_dir / run_id
    expected = {
        root / machine / system / benchmark / f"round-{repetition:03d}"
        for machine in ("host1", "host2")
        for system in ("os1", "os2")
        for benchmark in ("ub", "ub2")
        for repetition in (1, 2)
    }
    assert {path for path in root.rglob("round-*") if path.is_dir()} == expected
    assert {path.parent for path in root.rglob("build.json")} == {path.parent for path in expected}
    assert_no_removed_outputs(root)
    assert {job["details"]["output"] for job in store.jobs(run_id)} == {
        str(path) for path in expected
    }
    store.close()


def test_round_names_expand_past_three_digits(config):
    config.benchmarks[0].repetitions = 1000
    store, run_id, _ = setup(config)
    scheduler = Scheduler(store, run_id)
    jobs = store.jobs(run_id)
    assert scheduler.destination(jobs[998]).name == "round-999"
    assert scheduler.destination(jobs[999]).name == "round-1000"
    assert len({scheduler.destination(job) for job in jobs}) == 2000
    store.close()


async def test_fetch_after_reopening_collects_to_original_readable_path(config):
    store, run_id, world = setup(config)
    world.crash_controller = True
    assert await execute(store, run_id, world) == "paused"
    job = store.jobs(run_id)[0]
    job_id = job["id"]
    destination = Scheduler(store, run_id).destination(job)
    world.jobs[job_id].update(phase="terminal", outcome="succeeded")
    original_starts = world.starts.copy()
    store.close()
    store = Store(config.policy.state_dir)
    assert await execute(store, run_id, world, fetch_only=True) == "paused"
    assert store.job(job_id)["details"]["output"] == str(destination)
    assert destination == config.policy.output_dir / run_id / "host1/os1/ub/round-001"
    assert destination.is_dir()
    assert world.starts == original_starts
    assert ("host1", "switch") not in world.events
    store.close()


async def test_uncertain_submission_pauses_without_replay(config):
    store, run_id, world = setup(config)
    world.uncertain = True
    assert await execute(store, run_id, world) == "paused"
    assert await execute(store, run_id, world) == "paused"
    assert len(world.starts) == 1
    assert ("host1", "switch") not in world.events
    store.close()


async def test_failed_benchmark_archived_and_blocks_next_os(config):
    store, run_id, world = setup(config)
    world.fail = True
    assert await execute(store, run_id, world) == "failed"
    jobs = store.jobs(run_id)
    assert jobs[0]["status"] == "failed" and jobs[0]["details"]["archived"]
    assert jobs[1]["status"] == "blocked"
    assert len(world.starts) == 1 and ("host1", "switch") not in world.events
    store.close()


async def test_fetch_never_starts_or_switches(config):
    store, run_id, world = setup(config)
    assert await execute(store, run_id, world, fetch_only=True) == "paused"
    assert world.starts == [] and world.events == []
    store.close()


async def test_fetch_does_not_apply_pending_cancellation(config):
    store, run_id, world = setup(config)
    world.crash_controller = True
    await execute(store, run_id, world)
    store.request_cancel(run_id)
    await execute(store, run_id, world, fetch_only=True)
    assert world.jobs[world.starts[0]]["phase"] == "active"
    store.close()


async def test_missing_remote_evidence_does_not_replay_submission(config):
    store, run_id, world = setup(config)
    world.crash_controller = True
    await execute(store, run_id, world)
    original = world.starts[0]
    world.jobs.clear()  # Simulate lost/deleted remote job state after submission.
    assert await execute(store, run_id, world) == "paused"
    assert world.starts == [original]
    assert "automatic replay is disabled" in store.jobs(run_id)[0]["details"]["reason"]
    store.close()


async def test_cancellation_before_start(config):
    store, run_id, world = setup(config)
    store.request_cancel(run_id)
    assert await execute(store, run_id, world) == "cancelled"
    assert world.starts == []
    assert all(job["status"] == "cancelled" for job in store.jobs(run_id))
    store.close()


async def test_cancellation_of_existing_job_recovers_artifacts(config):
    store, run_id, world = setup(config)
    world.crash_controller = True
    await execute(store, run_id, world)
    store.request_cancel(run_id)
    assert await execute(store, run_id, world) == "cancelled"
    assert len(world.starts) == 1
    assert store.jobs(run_id)[0]["details"]["archived"]
    store.close()


async def test_machine_parallelism_but_each_machine_serial(config):
    second = config.machines[0].model_copy(deep=True)
    second.id = "host2"
    config.machines.append(second)
    store, run_id, world = setup(config)
    assert await execute(store, run_id, world) == "succeeded"
    assert world.max_active == 2
    assert len(world.starts) == 4
    store.close()


@pytest.mark.parametrize("verify_host_key", [True, False])
async def test_wrong_boot_entry_never_runs(config, verify_host_key):
    config.machines[0].ssh.verify_host_key = verify_host_key
    store, run_id, world = setup(config)
    # Force a reboot to the wrong system while still changing the boot ID.
    original = FakeRemote.switch

    async def wrong_switch(self, *args):
        try:
            await original(self, *args)
        finally:
            self.world.current[self.machine.id] = self.machine.systems[0]

    original_method = FakeRemote.switch
    try:
        FakeRemote.switch = wrong_switch
        await execute(store, run_id, world)
        assert len(world.starts) == 1
        assert store.jobs(run_id)[1]["status"] == "failed"
    finally:
        FakeRemote.switch = original_method
        store.close()


async def test_remote_reboot_recovered_as_interruption_not_rerun(config):
    store, run_id, world = setup(config)
    world.crash_controller = True
    await execute(store, run_id, world)
    job_id = world.starts[0]
    world.boot["host1"] = "another-boot"
    world.jobs[job_id].update(phase="terminal", outcome="interrupted")
    assert await execute(store, run_id, world) == "failed"
    assert world.starts == [job_id]
    assert store.jobs(run_id)[0]["status"] == "interrupted"
    store.close()


async def test_resume_after_first_round_preserves_shared_build(config, monkeypatch):
    config.benchmarks[0].repetitions = 3
    config.policy.settle_seconds = 123
    store, run_id, world = setup(config)
    original = FakeRemote.start

    async def interrupt_second(self, job_id, directory):
        if self.world.jobs[job_id]["manifest"]["repetition"] == 2:
            self.world.crash_controller = True
        await original(self, job_id, directory)

    monkeypatch.setattr(FakeRemote, "start", interrupt_second)
    assert await execute(store, run_id, world) == "paused"
    first, second, *_ = store.jobs(run_id)
    assert first["status"] == "succeeded"
    assert second["status"] == "submitting"
    monkeypatch.setattr(FakeRemote, "start", original)
    assert await execute(store, run_id, world) == "succeeded"
    rounds = [job for job in world.jobs.values() if job["manifest"]["system"] == "os1"]
    assert {job["manifest"]["build_id"] for job in rounds} == {first["id"]}
    assert all(job["manifest"]["settle_seconds"] == 123 for job in rounds)
    assert len(world.starts) == len(set(world.starts)) == 6
    store.close()


async def test_common_download_failure_never_acknowledges_round(config, monkeypatch):
    from autobench.errors import ChecksumError

    store, run_id, world = setup(config)
    original = FakeRemote.download

    async def fail_common(self, directory, inventory, destination):
        if PurePosixPath(directory).parent.name == "builds":
            raise ChecksumError("bad common artifact")
        await original(self, directory, inventory, destination)

    monkeypatch.setattr(FakeRemote, "download", fail_common)
    assert await execute(store, run_id, world) == "paused"
    assert not any(job.get("acked") or job.get("downloaded") for job in world.jobs.values())
    assert len(world.starts) == 1
    monkeypatch.setattr(FakeRemote, "download", original)
    assert await execute(store, run_id, world) == "succeeded"
    assert len(world.starts) == 2
    store.close()


async def test_worker_build_and_wait_phases_are_persisted(config, monkeypatch):
    import time

    store, run_id, world = setup(config)
    original = FakeRemote.status
    seen = []

    async def phases(self, job_id, directory):
        job = self.world.jobs.get(job_id)
        if job and job["phase"] == "active":
            count = job.setdefault("phase_polls", 0)
            job["phase_polls"] += 1
            if count < 2:
                phase = ("building", "stabilizing")[count]
                seen.append(phase)
                return {"phase": "active", "state": {"status": phase, "ready_at": time.time() + 30}}
            if count == 2:
                assert store.job(job_id)["status"] == "stabilizing"
                assert store.job(job_id)["details"]["ready_at"] > time.time()
        return await original(self, job_id, directory)

    monkeypatch.setattr(FakeRemote, "status", phases)
    assert await execute(store, run_id, world) == "succeeded"
    assert seen == ["building", "stabilizing"] * 2
    assert any("stabilizing" in event["message"] for event in store.events(run_id))
    store.close()
