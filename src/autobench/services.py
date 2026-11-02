"""Operations shared by the terminal and local web controllers."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from autobench.config import Config
from autobench.errors import AutoBenchError
from autobench.scheduler import Scheduler, inspect_machine
from autobench.storage import Store


def validate_config(config: Config) -> Config:
    for machine in config.machines:
        for system in [None, *machine.systems]:
            ssh = machine.connection(system)
            for path in [ssh.known_hosts, *ssh.client_keys]:
                if not path.is_file():
                    raise ValueError(f"local SSH file does not exist: {path}")
    return config


async def inspections(config: Config, selected: str | None = None) -> list[dict]:
    machines = [m for m in config.machines if selected is None or m.id == selected]
    if not machines:
        raise ValueError(f"unknown machine: {selected}")
    result = []
    # Only inspect the currently booted OS, without changing its boot state.
    for machine in machines:
        try:
            result.append(await inspect_machine(config, machine))
        except AutoBenchError as exc:
            result.append({"machine": machine.id, "errors": [str(exc)]})
    return result


def create_run(config: Config) -> str:
    store = Store(config.policy.state_dir)
    try:
        return store.create(config)
    finally:
        store.close()


async def execute_run(
    run_id: str,
    state_dir: Path,
    *,
    fetch_only: bool = False,
    cancel: bool = False,
    report: Callable[[str], None] = print,
) -> str:
    store = Store(state_dir)
    try:
        if cancel:
            # The active controller observes this even while it owns the run lock.
            store.request_cancel(run_id)
            try:
                with store.run_lock(run_id):
                    pass
            except AutoBenchError:
                report("Cancellation requested; the active controller will apply it.")
                return "cancel_requested"
        return await Scheduler(store, run_id, report=report).execute(fetch_only=fetch_only)
    finally:
        store.close()


def run_status(store: Store, run_id: str) -> dict:
    run = store.run(run_id)
    return {
        "run_id": run_id,
        "status": run["status"],
        "cancel_requested": bool(run["cancel_requested"]),
        "jobs": [
            {
                **{
                    key: job[key]
                    for key in ("id", "machine", "system", "benchmark", "repetition", "status")
                },
                "reason": job["details"].get("reason"),
                "output": job["details"].get("output"),
            }
            for job in store.jobs(run_id)
        ],
    }
