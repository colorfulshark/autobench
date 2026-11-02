from __future__ import annotations

import asyncio
import json
import tarfile
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import typer
import yaml
from pydantic import ValidationError

from autobench import __version__
from autobench.config import load_config
from autobench.errors import AutoBenchError
from autobench.scheduler import Scheduler, inspect_machine
from autobench.ssh import Credentials
from autobench.storage import Store

app = typer.Typer(
    no_args_is_help=True,
    help="Resumable Linux benchmarks over SSH and UEFI.",
    pretty_exceptions_enable=False,
)
StateDir = Annotated[
    Path, typer.Option(help="Local state directory used when this run was created.")
]
DEFAULT_STATE = Path("~/.local/state/autobench")


@contextmanager
def errors():
    try:
        yield
    except (
        AutoBenchError,
        ValidationError,
        OSError,
        ValueError,
        yaml.YAMLError,
        tarfile.TarError,
    ) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    except KeyboardInterrupt:
        typer.echo(
            "Controller stopped. Remote jobs continue; use resume with the printed run ID.",
            err=True,
        )
        raise typer.Exit(130) from None


@app.command()
def version():
    """Print the installed version."""
    typer.echo(__version__)


@app.command()
def validate(config: Path):
    """Validate YAML, source archive/checksum and local key/known_hosts paths."""
    with errors():
        parsed = load_config(config)
        for machine in parsed.machines:
            for system in [None, *machine.systems]:
                ssh = machine.connection(system)
                for path in [ssh.known_hosts, *ssh.client_keys]:
                    if not path.is_file():
                        raise ValueError(f"local SSH file does not exist: {path}")
        typer.echo(f"Valid: {len(parsed.machines)} machines, {len(parsed.benchmarks)} benchmarks")
        for benchmark in parsed.benchmarks:
            typer.echo(
                f"{benchmark.id}: SHA256 {benchmark.source_sha256}; source {benchmark.source_subdir}"
            )
        typer.echo("UnixBench may overwrite LANG internally. UB_RESULTDIR is managed by AutoBench.")


async def inspections(config: Path, selected: str | None):
    parsed = load_config(config, check_sources=False)
    machines = [m for m in parsed.machines if selected is None or m.id == selected]
    if not machines:
        raise ValueError(f"unknown machine: {selected}")
    credentials = Credentials()
    result = []
    # Discovery is intentionally read-only and only inspects the currently booted OS.
    for machine in machines:
        try:
            result.append(await inspect_machine(parsed, machine, credentials))
        except AutoBenchError as exc:
            result.append({"machine": machine.id, "errors": [str(exc)]})
    return result


@app.command()
def discover(config: Path, machine: Annotated[str | None, typer.Option()] = None):
    """Read current OS identity, UEFI entries and environment prerequisites without rebooting."""
    with errors():
        result = asyncio.run(inspections(config, machine))
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
        if any(item["errors"] for item in result):
            raise typer.Exit(1)


@app.command()
def doctor(config: Path, machine: Annotated[str | None, typer.Option()] = None):
    """Check current OS requirements and show per-system merged environment overrides."""
    discover(config, machine)


@app.command()
def run(config: Path):
    """Freeze a configuration, create a run, and execute its matrix."""
    with errors():
        parsed = load_config(config)
        store = Store(parsed.policy.state_dir)
        try:
            run_id = store.create(parsed)
            typer.echo(f"Run ID: {run_id}\nState: {store.directory}")
            outcome = asyncio.run(Scheduler(store, run_id, report=typer.echo).execute())
            typer.echo(f"Run {run_id}: {outcome}")
            if outcome != "succeeded":
                raise typer.Exit(1)
        finally:
            store.close()


def operate(run_id: str, state_dir: Path, *, fetch_only=False, cancel=False):
    with errors():
        store = Store(state_dir)
        try:
            if cancel:
                # Persist even if another controller owns the run lock. Its poll loop will observe it.
                store.request_cancel(run_id)
                try:
                    with store.run_lock(run_id):
                        pass
                except AutoBenchError:
                    typer.echo("Cancellation requested; the active controller will apply it.")
                    return
            outcome = asyncio.run(
                Scheduler(store, run_id, report=typer.echo).execute(fetch_only=fetch_only)
            )
            typer.echo(f"Run {run_id}: {outcome}")
            if outcome != "succeeded" and not (cancel and outcome == "cancelled"):
                raise typer.Exit(1)
        finally:
            store.close()


@app.command()
def resume(run_id: str, state_dir: StateDir = DEFAULT_STATE):
    """Reconcile existing jobs and continue using the frozen configuration."""
    operate(run_id, state_dir)


@app.command()
def fetch(run_id: str, state_dir: StateDir = DEFAULT_STATE):
    """Collect sealed results only; never submit a benchmark or switch an OS."""
    operate(run_id, state_dir, fetch_only=True)


@app.command()
def cancel(run_id: str, state_dir: StateDir = DEFAULT_STATE):
    """Persist cancellation, stop reachable job process trees, and recover artifacts."""
    operate(run_id, state_dir, cancel=True)


@app.command()
def status(run_id: str, state_dir: StateDir = DEFAULT_STATE):
    """Read local status without contacting or changing remote machines."""
    with errors():
        store = Store(state_dir)
        try:
            run = store.run(run_id)
            jobs = store.jobs(run_id)
            typer.echo(
                json.dumps(
                    {
                        "run_id": run_id,
                        "status": run["status"],
                        "cancel_requested": bool(run["cancel_requested"]),
                        "jobs": [
                            {
                                **{
                                    key: job[key]
                                    for key in (
                                        "id",
                                        "machine",
                                        "system",
                                        "benchmark",
                                        "repetition",
                                        "status",
                                    )
                                },
                                "reason": job["details"].get("reason"),
                                "output": job["details"].get("output"),
                            }
                            for job in jobs
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        finally:
            store.close()


if __name__ == "__main__":
    app()
