import asyncio
import copy
import json
from pathlib import PurePosixPath

from autobench.errors import ConnectionLost
from autobench.scheduler import Scheduler
from autobench.storage import Store


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

    def factory(self, machine, system, policy, credentials):
        return FakeRemote(self, machine, system)


class FakeRemote:
    def __init__(self, world, machine, system):
        self.world, self.machine, self.system = world, machine, system

    async def probe(self, requirements=None):
        current = self.world.current[self.machine.id]
        return {
            "boot_id": self.world.boot[self.machine.id],
            "machine_id": current.machine_id,
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
        }

    async def download(self, directory, inventory, destination):
        if self.world.drop_download:
            self.world.drop_download = False
            raise ConnectionLost("download interrupted")
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "archive-manifest.json").write_text(json.dumps(inventory))
        job = self.world.jobs[PurePosixPath(directory).name]
        job["downloaded"] = True
        self.world.events.append((self.machine.id, "download"))

    async def acknowledge(self, job_id, directory):
        job = self.world.jobs[job_id]
        assert job["downloaded"]
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
    # Neither modifying the caller's config nor reconstructing the scheduler changes the manifest.
    config.benchmarks[0].env["SHARED"] = "new-value"
    store.close()
    store = Store(config.policy.state_dir)
    assert await execute(store, run_id, world) == "succeeded"
    assert world.starts.count(original) == 1
    assert world.jobs[original]["manifest"]["benchmark_env"]["SHARED"] == "benchmark"
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


async def test_wrong_boot_identity_never_runs(config):
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
