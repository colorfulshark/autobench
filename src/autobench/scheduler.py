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
from autobench.remote_runner import resolve_grub_entry
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
                return client, await self.probe_system(client, preferred)
            except (AuthenticationFailed, ConnectionLost) as exc:
                failures.append(exc)
        if failures and all(isinstance(exc, AuthenticationFailed) for exc in failures):
            raise AuthenticationFailed(f"no configured SSH profile authenticates to {machine.id}")
        raise ConnectionLost(f"no configured SSH profile reaches {machine.id}")

    @staticmethod
    async def probe_system(client, system: System, requirements=None):
        if system.grub_entry:
            return await client.probe(requirements, grub=True, grub_boot_entry=system.boot_entry)
        return await client.probe() if requirements is None else await client.probe(requirements)

    @staticmethod
    def is_target(probe: dict, system: System, grub=None) -> bool:
        if probe.get("efi", {}).get("current") != system.boot_entry:
            return False
        if not system.grub_entry:
            return True
        menu = probe.get("grub", {})
        if menu.get("boot_entry") != system.boot_entry:
            return False
        entries = menu.get("entries", [])
        entry = resolve_grub_entry(entries, system.grub_entry)
        if not entry or not entry.get("selectable") or not entry.get("matches_boot"):
            return False
        if grub is not None:
            return (
                grub["id"] == entry["id"]
                and grub["signature"] == entry["signature"]
                and grub["identity"] == menu["identity"]
            )
        return sum(bool(item.get("matches_boot")) for item in entries) == 1

    @staticmethod
    def grub_target(machine: Machine, system: System, probe: dict) -> dict:
        menu = probe.get("grub")
        if not menu or menu.get("boot_entry") != system.boot_entry:
            if probe.get("grub_error_code") == "esp_unmounted":
                raise RemoteError(
                    f"无法读取 Boot{system.boot_entry} 的 GRUB 菜单。请在目标系统中挂载对应的 EFI 系统分区后重试。"
                )
            reason = probe.get("grub_error", "the selected UEFI entry has no verified menu")
            raise RemoteError(f"cannot read the GRUB menu for Boot{system.boot_entry}: {reason}")
        entry = resolve_grub_entry(menu["entries"], system.grub_entry)
        if not entry or not entry["selectable"]:
            raise RemoteError(f"GRUB entry is missing or cannot be verified: {system.grub_entry}")
        for other in machine.systems:
            if other.id != system.id and other.boot_entry == system.boot_entry:
                if resolve_grub_entry(menu["entries"], other.grub_entry) is entry:
                    raise RemoteError(
                        f"systems {system.id} and {other.id} select the same GRUB entry"
                    )
        return {
            "id": entry["id"],
            "signature": entry["signature"],
            "identity": menu["identity"],
        }

    @classmethod
    def switched_target(cls, probe: dict, system: System, switch: dict) -> bool:
        if switch.get("efi_only"):
            firmware = probe.get("efi", {})
            return firmware.get("current") == system.boot_entry and not firmware.get("next")
        menu = probe.get("grub", {})
        return cls.is_target(probe, system, switch.get("grub")) and (
            not system.grub_entry
            or not (
                menu.get("next_entry") or menu.get("prev_saved_entry") or probe["efi"].get("next")
            )
        )

    async def request_switch(self, current, probe, system, job, *, grub=None, efi_only=False):
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
        if grub is not None:
            menu = probe["grub"]
            if not menu["one_shot"]:
                raise RemoteError("the shared GRUB environment does not support one-time boot")
            if menu["next_entry"] or menu["prev_saved_entry"]:
                raise RemoteError("existing GRUB boot selection conflicts with automatic switching")
            switch["grub"] = grub
        key = "switch"
        token = job["id"]
        destination = f"switching to Boot{system.boot_entry}"
        if efi_only:
            switch["efi_only"] = True
            key = "efi_bootstrap"
            token += "-efi"
            destination = (
                f"Boot{system.boot_entry} 的 EFI 系统分区尚未挂载，先切换到该入口读取 GRUB"
            )
        elif grub is not None:
            destination += f" / GRUB {grub['id']}"
        self.store.update(job["id"], "switching", reason=None, **{key: switch})
        self.event(job, destination)
        try:
            await current.switch(
                current.root(probe),
                probe["boot_id"],
                system.boot_entry,
                token,
                **({"grub": grub} if grub is not None else {}),
            )
        except (ConnectionLost, UncertainSubmission):
            self.event(job, "reboot acknowledgement lost; checking the original reboot")
        return switch

    async def ensure_system(self, machine: Machine, system: System, job: dict):
        current, probe = await self.retry(lambda: self.find_current(machine, system))
        check_probe(probe)
        details = self.store.job(job["id"])["details"]
        switch = details.get("switch")
        grub = None
        if not switch and system.grub_entry:
            bootstrap = details.get("efi_bootstrap")
            if (
                not bootstrap
                and probe["efi"].get("current") != system.boot_entry
                and probe.get("grub_error_code") == "esp_unmounted"
            ):
                bootstrap = await self.request_switch(current, probe, system, job, efi_only=True)
            if bootstrap:
                if probe["boot_id"] == bootstrap["previous_boot_id"]:
                    current, probe = await self.wait_boot(machine, system, bootstrap)
                elif not self.switched_target(probe, system, bootstrap):
                    raise RemoteError(f"重启后未确认已进入 UEFI 启动项 Boot{system.boot_entry}")
                check_probe(probe)
        if switch:
            grub = switch.get("grub")
            if probe["boot_id"] != switch["previous_boot_id"]:
                if not self.switched_target(probe, system, switch):
                    raise RemoteError("reboot reached an unexpected operating system or boot entry")
            else:
                # A durable intent may mean the SSH request reached the remote host.
                # Never send a second reboot; wait for or reconcile the original one.
                _, probe = await self.wait_boot(machine, system, switch)
        else:
            already_booted = self.is_target(probe, system)
            if system.grub_entry:
                grub = self.grub_target(machine, system, probe)
                if not already_booted and self.is_target(probe, system, grub):
                    evidence = {"boot_id": probe["boot_id"], "target": grub}
                    already_booted = any(
                        item["details"].get("grub_boot") == evidence
                        for item in self.store.jobs(self.run_id, machine.id)
                    )
        if not switch and not already_booted:
            switch = await self.request_switch(current, probe, system, job, grub=grub)
            _, probe = await self.wait_boot(machine, system, switch)
        # Use the target profile even if bootstrap credentials worked on the other OS.
        target = self.client(machine, system)
        requirements = requirements_for(self.config)
        target_probe = await self.retry(lambda: self.probe_system(target, system, requirements))
        if (
            not self.is_target(target_probe, system, grub)
            or target_probe["boot_id"] != probe["boot_id"]
        ):
            raise RemoteError("system boot changed during preparation")
        check_probe(target_probe, requirements=requirements)
        # The detached worker waits after construction/preparation, before each test.
        return target, target_probe, grub

    async def wait_boot(self, machine: Machine, system: System, switch: dict):
        while time.time() < switch["deadline"]:
            try:
                client, probe = await self.find_current(machine, system)
                if probe["boot_id"] != switch["previous_boot_id"]:
                    if not self.switched_target(probe, system, switch):
                        if switch.get("efi_only"):
                            raise RemoteError(
                                f"重启后未确认已进入 UEFI 启动项 Boot{system.boot_entry}"
                            )
                        raise RemoteError(
                            "boot ID changed but target UEFI or GRUB entry did not match"
                        )
                    return client, probe
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
        self.store.update(job["id"], "collecting", remote_outcome=outcome, reason=None)
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
            probe = await self.retry(lambda: self.probe_system(client, system))
            evidence = details.get("grub_boot")
            grub = evidence["target"] if evidence else None
            if not self.is_target(probe, system, grub):
                raise Paused("job OS is not currently booted; no automatic switch during recovery")
        elif fetch_only:
            return
        elif self.cancelled():
            self.store.update(job["id"], "cancelled", reason="cancelled before remote staging")
            return
        else:
            client, probe, grub = await self.ensure_system(machine, system, job)
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
            boot_details = {}
            if grub is not None:
                manifest["grub_entry"] = grub["id"]
                boot_details["grub_boot"] = {"boot_id": probe["boot_id"], "target": grub}
            self.store.update(
                job["id"],
                "preparing",
                remote_dir=directory,
                manifest=manifest,
                remote_boot_id=probe["boot_id"],
                reason=None,
                **boot_details,
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
            self.store.update(job["id"], "preparing", reason=None)
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
                self.store.update(job["id"], phase, ready_at=ready_at, reason=None)
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
    grub_systems = {system.boot_entry: system for system in machine.systems if system.grub_entry}
    if len(grub_systems) == 1:
        probe = await Scheduler.probe_system(
            client, next(iter(grub_systems.values())), requirements
        )
    else:
        probe = await client.probe(requirements, grub=True)
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
        checked = current
        if system.grub_entry and (
            len(grub_systems) == 1 or probe.get("efi", {}).get("current") == system.boot_entry
        ):
            try:
                Scheduler.grub_target(machine, system, probe)
                checked = True
            except RemoteError as exc:
                errors.append(str(exc))
        systems.append(
            {
                "id": system.id,
                "current": current,
                "dependencies_checked": current,
                "boot_entry_checked": checked,
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
