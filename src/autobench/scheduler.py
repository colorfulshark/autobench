from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path, PurePosixPath
from typing import Callable

from autobench.adapters import get_adapter
from autobench.config import Config, Machine, System, merged_env
from autobench.errors import (
    AuthenticationFailed,
    AutoBenchError,
    ChecksumError,
    ConnectionLost,
    Paused,
    RemoteError,
    UncertainSubmission,
)
from autobench.ssh import RemoteClient
from autobench.storage import TERMINAL, Store


def requirements_for(config: Config) -> list[str]:
    return sorted(
        {"python3", "systemd-run", "systemctl", "efibootmgr"}
        | {
            name
            for benchmark in config.benchmarks
            for name in get_adapter(benchmark.type).requirements(benchmark)
        }
    )


def check_probe(probe: dict, *, requirements: list[str] | None = None):
    errors = []
    if tuple(probe.get("python", [])) < (3, 8):
        errors.append("Python >=3.8 required")
    if probe.get("systemd_version", 0) < 240 or not probe.get("systemd_available"):
        errors.append("systemd >=240 and root/passwordless sudo required")
    if not probe.get("efi", {}).get("current"):
        errors.append(
            "UEFI/efibootmgr unavailable: " + probe.get("efi_error", "missing BootCurrent")
        )
    missing = [name for name in requirements or [] if not probe.get("commands", {}).get(name)]
    if missing:
        errors.append("missing dependencies: " + ", ".join(missing))
    if probe.get("disk_free", 0) < 256 * 1024 * 1024:
        errors.append("less than 256 MiB free in remote home")
    if errors:
        raise RemoteError("; ".join(errors))


class Scheduler:
    def __init__(
        self,
        store: Store,
        run_id: str,
        *,
        client_factory: Callable | None = None,
        report: Callable[[str], None] = print,
    ):
        self.store = store
        self.run_id = run_id
        self.config = store.config(run_id)
        self.client_factory = client_factory or RemoteClient
        self.report = report

    def client(self, machine: Machine, system: System | None):
        return self.client_factory(machine, system, self.config.policy)

    def event(self, job: dict | None, message: str):
        self.store.event(self.run_id, job["id"] if job else None, message)
        label = f"{job['machine']}/{job['system']}/{job['id'][:8]}: " if job else ""
        self.report(label + message)

    def cancelled(self) -> bool:
        return bool(self.store.run(self.run_id)["cancel_requested"])

    async def retry(self, operation, *, timeout: float | None = None):
        deadline = time.monotonic() + (timeout or self.config.policy.reconnect_timeout)
        delay = 1.0
        while True:
            try:
                return await operation()
            except (ConnectionLost, ChecksumError) as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Paused(str(exc)) from exc
                await asyncio.sleep(min(remaining, 30, delay * random.uniform(0.8, 1.2)))
                delay = min(delay * 2, 30)

    async def find_current(self, machine: Machine, preferred: System):
        """Try configured profiles; authentication failure on one OS isn't proof of outage."""
        failures = []
        seen = set()
        for system in [preferred, None, *machine.systems]:
            connection = machine.connection(system)
            key = connection.model_dump_json()
            if key in seen:
                continue
            seen.add(key)
            client = self.client(machine, system)
            try:
                return client, await client.probe()
            except (AuthenticationFailed, ConnectionLost) as exc:
                failures.append(exc)
        if failures and all(isinstance(exc, AuthenticationFailed) for exc in failures):
            raise AuthenticationFailed(f"no configured SSH profile authenticates to {machine.id}")
        raise ConnectionLost(f"no configured SSH profile reaches {machine.id}")

    @staticmethod
    def is_target(probe: dict, system: System) -> bool:
        return probe.get("efi", {}).get("current") == system.boot_entry

    async def ensure_system(self, machine: Machine, system: System, job: dict):
        current, probe = await self.retry(lambda: self.find_current(machine, system))
        check_probe(probe)
        details = self.store.job(job["id"])["details"]
        switch = details.get("switch")
        if switch:
            if probe["boot_id"] != switch["previous_boot_id"]:
                if not self.is_target(probe, system):
                    raise RemoteError("reboot reached an unexpected operating system")
            else:
                # A durable intent may mean the SSH request reached the remote host.
                # Never send a second reboot; wait for or reconcile the original one.
                probe = await self.wait_boot(machine, system, switch)
        elif not self.is_target(probe, system):
            if probe.get("efi", {}).get("next"):
                raise RemoteError("existing BootNext conflicts with automatic switching")
            entry = probe["efi"]["entries"].get(system.boot_entry)
            if not entry or not entry["active"]:
                raise RemoteError("target EFI entry is missing or inactive")
            switch = {
                "previous_boot_id": probe["boot_id"],
                "requested": time.time(),
                "deadline": time.time() + self.config.policy.boot_timeout,
            }
            self.store.update(job["id"], "switching", switch=switch)
            self.event(job, f"switching to Boot{system.boot_entry}")
            try:
                await current.switch(
                    current.root(probe), probe["boot_id"], system.boot_entry, job["id"]
                )
            except ConnectionLost:
                pass  # The reboot can close SSH before it acknowledges the request.
            probe = await self.wait_boot(machine, system, switch)
        # Use the target profile even if bootstrap credentials worked on the other OS.
        target = self.client(machine, system)
        requirements = requirements_for(self.config)
        target_probe = await self.retry(lambda: target.probe(requirements))
        if not self.is_target(target_probe, system) or target_probe["boot_id"] != probe["boot_id"]:
            raise RemoteError("system boot changed during preparation")
        check_probe(target_probe, requirements=requirements)
        # The detached worker waits after construction/preparation, before each test.
        return target, target_probe

    async def wait_boot(self, machine: Machine, system: System, switch: dict) -> dict:
        while time.time() < switch["deadline"]:
            try:
                _, probe = await self.find_current(machine, system)
                if probe["boot_id"] != switch["previous_boot_id"]:
                    if not self.is_target(probe, system):
                        raise RemoteError("boot ID changed but target UEFI entry did not match")
                    return probe
            except (ConnectionLost, AuthenticationFailed):
                pass
            await asyncio.sleep(
                min(self.config.policy.poll_interval, max(0, switch["deadline"] - time.time()))
            )
        raise Paused("boot timeout; reconcile the existing reboot intent before resuming")

    def destination(self, job: dict) -> Path:
        return (
            self.config.policy.output_dir
            / self.run_id
            / job["machine"]
            / job["system"]
            / job["benchmark"]
            / f"round-{job['repetition']:03d}"
        )

    async def collect(self, client, job: dict, remote: dict):
        outcome = remote["state"]["status"]
        if outcome not in TERMINAL:
            raise RemoteError("invalid terminal remote state")
        self.store.update(job["id"], "collecting", remote_outcome=outcome)
        directory = job["details"]["remote_dir"]
        self.event(job, f"collecting artifacts ({outcome})")
        common = remote["common"]
        if common["inventory"] is None:
            raise RemoteError("shared build artifacts are not sealed")
        await self.retry(
            lambda: client.download(
                common["directory"], common["inventory"], self.destination(job).parent
            )
        )
        await self.retry(
            lambda: client.download(directory, remote["inventory"], self.destination(job))
        )
        # Acknowledge only after verification and durable local directory commit.
        await self.retry(lambda: client.acknowledge(job["id"], directory))
        self.store.update(
            job["id"],
            outcome,
            archived=True,
            output=str(self.destination(job)),
            reason=remote["state"].get("reason"),
        )
        self.event(job, f"archived: {outcome}")

    async def drive(self, machine: Machine, system: System, job: dict, *, fetch_only=False):
        benchmark = next(b for b in self.config.benchmarks if b.id == job["benchmark"])
        details = job["details"]
        if details.get("archived"):
            return
        directory = details.get("remote_dir")
        if directory:
            client = self.client(machine, system)
            probe = await self.retry(client.probe)
            if not self.is_target(probe, system):
                raise Paused("job OS is not currently booted; no automatic switch during recovery")
        elif fetch_only:
            return
        elif self.cancelled():
            self.store.update(job["id"], "cancelled", reason="cancelled before remote staging")
            return
        else:
            client, probe = await self.ensure_system(machine, system, job)
            root = client.root(probe)
            directory = str(PurePosixPath(root) / "jobs" / job["id"])
            adapter = get_adapter(benchmark.type)
            manifest = {
                "schema_version": 2,
                "job_id": job["id"],
                "build_id": details["build_id"],
                "run_id": self.run_id,
                "machine": machine.id,
                "system": system.id,
                "benchmark": benchmark.id,
                "repetition": job["repetition"],
                "warmup": benchmark.warmup and job["repetition"] == 1,
                "boot_id": probe["boot_id"],
                "efi": probe["efi"],
                "source_path": str(
                    PurePosixPath(root) / "sources" / (benchmark.source_sha256 + ".tar")
                ),
                "source_sha256": benchmark.source_sha256,
                "system_env": system.env,
                "benchmark_env": benchmark.env,
                "execution": adapter.execution(benchmark),
                "tool": adapter.metadata(benchmark),
                "settle_seconds": self.config.policy.settle_seconds,
            }
            self.store.update(
                job["id"],
                "preparing",
                remote_dir=directory,
                manifest=manifest,
                remote_boot_id=probe["boot_id"],
            )
            details = self.store.job(job["id"])["details"]
        status = await self.retry(lambda: client.status(job["id"], directory))
        if self.cancelled() and not fetch_only:
            if status["phase"] == "absent":
                self.store.update(job["id"], "cancelled", reason="cancelled before remote staging")
                return
            self.store.update(job["id"], "cancel_pending")
            status = await self.retry(lambda: client.cancel(job["id"], directory))
        if fetch_only and status["phase"] != "terminal":
            return
        if status["phase"] in ("absent", "staged"):
            if details.get("submission_intent"):
                raise UncertainSubmission(
                    "local submission intent exists but remote execution evidence "
                    "is missing; automatic replay is disabled"
                )
            if probe["boot_id"] != details["manifest"]["boot_id"]:
                raise RemoteError("boot ID changed before submission; job will not be replayed")
            self.store.update(job["id"], "preparing")
            await self.retry(
                lambda: client.stage(
                    job["id"], directory, details["manifest"], benchmark.source_archive
                )
            )
            if self.cancelled():
                status = await self.retry(lambda: client.cancel(job["id"], directory))
            else:
                self.store.update(job["id"], "submitting", submission_intent=True)
                self.event(job, "submitting detached benchmark job")
                try:
                    # Do not retry this RPC. An absent response is not an absent service.
                    await client.start(job["id"], directory)
                except ConnectionLost:
                    self.event(job, "submission response lost; reconciling the original job")
                status = await self.retry(lambda: client.status(job["id"], directory))
        while status["phase"] != "terminal":
            if status["phase"] in ("uncertain", "absent", "staged"):
                raise UncertainSubmission(
                    "submission outcome is uncertain; automatic replay is disabled"
                )
            if self.cancelled():
                self.store.update(job["id"], "cancel_pending")
                status = await self.retry(lambda: client.cancel(job["id"], directory))
            else:
                phase = status.get("state", {}).get("status", "preparing")
                if phase not in {"preparing", "building", "stabilizing", "warming_up", "running"}:
                    phase = "preparing"
                previous = self.store.job(job["id"])["status"]
                ready_at = status.get("state", {}).get("ready_at")
                self.store.update(job["id"], phase, ready_at=ready_at)
                if phase != previous:
                    suffix = (
                        f" ({max(0, ready_at - time.time()):.0f}s remaining)"
                        if phase == "stabilizing" and ready_at is not None
                        else ""
                    )
                    self.event(job, ("预热中" if phase == "warming_up" else phase) + suffix)
                await asyncio.sleep(self.config.policy.poll_interval)
                status = await self.retry(lambda: client.status(job["id"], directory))
        job = self.store.job(job["id"])
        await self.collect(client, job, status)

    async def machine(self, machine: Machine, semaphore: asyncio.Semaphore, fetch_only: bool):
        async with semaphore:
            with self.store.machine_lock(machine.id):
                stopped = False
                for job in self.store.jobs(self.run_id, machine.id):
                    if job["status"] == "succeeded" or job["details"].get("archived"):
                        if job["status"] != "succeeded":
                            stopped = True
                        continue
                    if stopped and not fetch_only and not self.cancelled():
                        if job["status"] == "pending":
                            self.store.update(
                                job["id"], "blocked", reason="earlier job on this machine failed"
                            )
                        continue
                    if job["status"] in TERMINAL:
                        stopped = True
                        continue
                    system = next(s for s in machine.systems if s.id == job["system"])
                    try:
                        await self.drive(machine, system, job, fetch_only=fetch_only)
                    except (
                        Paused,
                        ConnectionLost,
                        AuthenticationFailed,
                        UncertainSubmission,
                    ) as exc:
                        status = "cancel_pending" if self.cancelled() else "paused"
                        self.store.update(job["id"], status, reason=str(exc))
                        self.event(job, str(exc))
                        # A temporarily paused job must not advance to the next OS.
                        if not self.cancelled():
                            break
                    except (AutoBenchError, OSError, ValueError) as exc:
                        # Preserve jobs with a remote reference for recovery/collection.
                        has_remote = bool(self.store.job(job["id"])["details"].get("remote_dir"))
                        self.store.update(
                            job["id"], "paused" if has_remote else "failed", reason=str(exc)
                        )
                        self.event(job, str(exc))
                        stopped = True
                        if has_remote:
                            break
                    final = self.store.job(job["id"])
                    if final["status"] in TERMINAL - {"succeeded"}:
                        stopped = True

    async def execute(self, *, fetch_only=False) -> str:
        root = self.config.policy.output_dir / self.run_id
        with self.store.run_lock(self.run_id):
            self.store.set_run_status(self.run_id, "running")
            self.store.snapshot(self.run_id, root)
            semaphore = asyncio.Semaphore(self.config.policy.parallel_machines)
            try:
                results = await asyncio.gather(
                    *(self.machine(m, semaphore, fetch_only) for m in self.config.machines),
                    return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, BaseException):
                        self.event(None, f"machine scheduler stopped: {result}")
                return self.store.summarize(self.run_id)
            finally:
                if self.store.run(self.run_id)["status"] == "running":
                    self.store.summarize(self.run_id)
                self.store.snapshot(self.run_id, root)


async def inspect_machine(config: Config, machine: Machine) -> dict:
    """Read-only discovery/doctor: never boot other systems to inspect them."""
    requirements = requirements_for(config)
    client = RemoteClient(machine, None, config.policy)
    probe = await client.probe(requirements)
    errors = []
    try:
        check_probe(probe, requirements=requirements)
    except RemoteError as exc:
        errors.append(str(exc))
    systems = []
    for system in machine.systems:
        entry = probe.get("efi", {}).get("entries", {}).get(system.boot_entry)
        current = Scheduler.is_target(probe, system)
        if not entry or not entry["active"]:
            errors.append(f"missing/inactive EFI entry for {system.id}")
        systems.append(
            {
                "id": system.id,
                "current": current,
                "dependencies_checked": current,
                "boot_entry_checked": current,
                "configured_environment": {
                    b.id: merged_env({}, system.env, b.env) for b in config.benchmarks
                },
            }
        )
    return {
        "machine": machine.id,
        "verify_host_key": machine.ssh.verify_host_key,
        "probe": probe,
        "systems": systems,
        "errors": errors,
        "warnings": ["UnixBench may overwrite LANG internally; launch env is archived verbatim."],
    }
