from __future__ import annotations

import asyncio
import json
import tarfile
from contextlib import contextmanager
from ipaddress import ip_address
from pathlib import Path
from typing import Annotated

import typer
import yaml
from pydantic import ValidationError

from autobench import __version__, services
from autobench.config import load_config
from autobench.errors import AutoBenchError
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
        services.validate_config(parsed)
        typer.echo(f"Valid: {len(parsed.machines)} machines, {len(parsed.benchmarks)} benchmarks")
        for benchmark in parsed.benchmarks:
            typer.echo(
                f"{benchmark.id}: SHA256 {benchmark.source_sha256}; source {benchmark.source_subdir}"
            )
        typer.echo(
            "UnixBench may overwrite LANG internally. "
            "UB_RESULTDIR, UB_BINDIR and MAKE are managed by AutoBench."
        )


async def inspections(config: Path, selected: str | None):
    parsed = load_config(config, check_sources=False)
    return await services.inspections(parsed, selected)


@app.command()
def discover(config: Path, machine: Annotated[str | None, typer.Option()] = None):
    """Read current UEFI entries and environment prerequisites without rebooting."""
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
        run_id = services.create_run(parsed)
        typer.echo(f"Run ID: {run_id}\nState: {parsed.policy.state_dir}")
        outcome = asyncio.run(
            services.execute_run(run_id, parsed.policy.state_dir, report=typer.echo)
        )
        typer.echo(f"Run {run_id}: {outcome}")
        if outcome != "succeeded":
            raise typer.Exit(1)


def operate(run_id: str, state_dir: Path, *, fetch_only=False, cancel=False):
    with errors():
        outcome = asyncio.run(
            services.execute_run(
                run_id, state_dir, fetch_only=fetch_only, cancel=cancel, report=typer.echo
            )
        )
        if outcome == "cancel_requested":
            return
        typer.echo(f"Run {run_id}: {outcome}")
        if outcome != "succeeded" and not (cancel and outcome == "cancelled"):
            raise typer.Exit(1)


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
            typer.echo(json.dumps(services.run_status(store, run_id), ensure_ascii=False, indent=2))
        finally:
            store.close()


@app.command()
def web(
    config: Annotated[Path | None, typer.Option(help="YAML file to open initially.")] = None,
    state_dir: StateDir = DEFAULT_STATE,
    host: Annotated[str, typer.Option(help="IPv4 or IPv6 address to listen on.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8765,
    open_browser: Annotated[bool, typer.Option(help="Open the local UI in a browser.")] = False,
):
    """Start the web UI for configuration, diagnostics and run management."""
    import uvicorn

    from autobench.web import create_app

    with errors():
        address = ip_address(host)
        listen_host = str(address)
        browser_host = (
            ("127.0.0.1" if address.version == 4 else "::1")
            if address.is_unspecified
            else listen_host
        )

        def url(value: str) -> str:
            authority = f"[{value}]" if ":" in value else value
            return f"http://{authority}:{port}"

        application = create_app(config=config, state_dir=state_dir, host=listen_host)
        typer.echo(f"AutoBench Web: {url(listen_host)}")
        if address.is_unspecified:
            typer.echo(f"Local URL: {url(browser_host)}")
        typer.echo("Remote jobs survive controller exit; use resume to reconnect.")
        if open_browser:
            import threading
            import webbrowser

            timer = threading.Timer(0.8, webbrowser.open, args=(url(browser_host),))
            timer.daemon = True
            timer.start()
        uvicorn.run(application, host=listen_host, port=port, access_log=False)


if __name__ == "__main__":
    app()
