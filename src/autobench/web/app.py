from __future__ import annotations

import asyncio
import json
import math
import tarfile
import tempfile
import threading
import time
import uuid
import zipfile
from collections.abc import Callable, Coroutine
from contextlib import asynccontextmanager, contextmanager
from ipaddress import ip_address
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import yaml
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.background import BackgroundTask

from autobench import __version__, services
from autobench.config import (
    Config,
    PositiveSeconds,
    SSHConfig,
    inspect_archive,
    load_config_text,
    resolve_ssh_paths,
)
from autobench.errors import AutoBenchError, ConfigurationFieldError
from autobench.storage import Store
from autobench.web.files import ConfigFiles, ConflictError, parse_document

STATIC = Path(__file__).with_name("static")
RunID = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]

TEMPLATE = """version: 1
policy:
  state_dir: ~/.local/state/autobench
  output_dir: ./results
machines:
  - id: server01
    ssh:
      host: 192.0.2.10
      username: bench
      known_hosts: ~/.ssh/known_hosts
      client_keys: []
      agent: true
    systems:
      - id: linux
        boot_entry: "0001"
benchmarks:
  - id: unixbench
    type: unixbench
    source_archive: ./sources/unixbench.tar.gz
"""


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class DocumentBody(Body):
    text: str


class RenderBody(Body):
    document: dict[str, Any]


class PathBody(Body):
    path: str = Field(min_length=1)


class SavedBody(PathBody):
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class SaveBody(PathBody):
    text: str
    revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class InspectionBody(SavedBody):
    kind: Literal["discover", "doctor"] = "discover"
    machine: str | None = None


class ControlBody(Body):
    state_dir: str


class ProbeBody(Body):
    ssh: SSHConfig
    config_path: str | None = None
    connect_timeout: PositiveSeconds = 15


def field_errors(exc: Exception) -> list[dict]:
    if isinstance(exc, ConfigurationFieldError):
        return [{"path": exc.path, "code": exc.code, "message": str(exc)}]
    if isinstance(exc, (ValidationError, RequestValidationError)):
        return [
            {
                "path": [part for part in item["loc"] if part != "body"],
                "code": item["type"],
                "message": item["msg"],
            }
            for item in exc.errors()
        ]
    mark = getattr(exc, "problem_mark", None)
    if mark:
        return [
            {
                "path": [],
                "code": "yaml_syntax",
                "message": "YAML 格式错误",
                "line": mark.line + 1,
                "column": mark.column + 1,
            }
        ]
    return [{"path": [], "code": "configuration", "message": error_message(exc)}]


def error_message(exc: Exception) -> str:
    if isinstance(exc, (ValidationError, RequestValidationError)):
        return "\n".join(
            f"{'.'.join(map(str, item['loc'])) or 'config'}: {item['msg']}" for item in exc.errors()
        )
    if isinstance(exc, yaml.YAMLError):
        mark = getattr(exc, "problem_mark", None)
        return (
            f"YAML 格式错误：第 {mark.line + 1} 行，第 {mark.column + 1} 列"
            if mark
            else "YAML 格式错误"
        )
    if isinstance(exc, (OSError, ValueError, AutoBenchError, tarfile.TarError)):
        return str(exc)
    return "操作失败，请检查配置、本地文件和目标机器状态"


class Operations:
    def __init__(self, clean: Callable):
        self.records: dict[str, dict] = {}
        self.tasks: set[asyncio.Task] = set()
        self.active: set[tuple[str, str]] = set()
        self.clean = clean
        self.stopping = False

    def start(
        self,
        kind: str,
        factory: Callable[[], Coroutine],
        *,
        run_id: str | None = None,
        key: tuple[str, str] | None = None,
    ) -> dict:
        if self.stopping:
            raise HTTPException(503, "服务正在退出")
        if key in self.active:
            raise HTTPException(409, "此运行已有正在执行的控制操作")
        operation_id = uuid.uuid4().hex
        record = {
            "id": operation_id,
            "kind": kind,
            "run_id": run_id,
            "status": "running",
            "created": time.time(),
            "result": None,
            "error": None,
        }
        # Keep transient operations bounded; durable run history lives in SQLite.
        completed = [k for k, v in self.records.items() if v["status"] != "running"]
        for old in completed[: max(0, len(self.records) - 199)]:
            del self.records[old]
        self.records[operation_id] = record
        if key:
            self.active.add(key)

        async def work():
            try:
                result = await factory()
                if kind in {"discover", "doctor"}:
                    for item in result:
                        item["errors"] = self.clean(item.get("errors", []))
                if kind == "connection_probe":
                    for item in result["checks"]:
                        item["message"] = self.clean(item["message"])
                record["result"] = result
                record["status"] = "completed"
            except asyncio.CancelledError:
                record["status"] = "stopped"
                raise
            except Exception as exc:
                record["status"] = "failed"
                record["error"] = self.clean(error_message(exc))
            finally:
                if key:
                    self.active.discard(key)

        task = asyncio.create_task(work())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return dict(record)

    async def stop(self):
        self.stopping = True
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def create_app(
    *,
    config: Path | None = None,
    state_dir: Path = Path("~/.local/state/autobench"),
    base_dir: Path | None = None,
    host: str = "127.0.0.1",
) -> FastAPI:
    listen_address = ip_address(host)
    local_addresses = {ip_address("127.0.0.1"), ip_address("::1")}
    files = ConfigFiles(base_dir or Path.cwd())
    default_state = str(files.path(state_dir))
    secrets: set[str] = set()
    secrets_lock = threading.Lock()

    def remember(value):
        found = set()

        def visit(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "password" and isinstance(item, str) and item:
                        found.add(item)
                    else:
                        visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(value)
        with secrets_lock:
            secrets.update(found)

    def clean(value):
        with secrets_lock:
            captured = sorted(secrets, key=len, reverse=True)

        def redact(value):
            if isinstance(value, str):
                for secret in captured:
                    value = value.replace(secret, "********")
                return value
            if isinstance(value, dict):
                return {k: redact(v) for k, v in value.items()}
            if isinstance(value, list):
                return [redact(item) for item in value]
            return value

        return redact(value)

    operations = Operations(clean)

    @asynccontextmanager
    async def lifespan(app):
        yield
        # Cancelling the controller does not request cancellation of remote jobs.
        await operations.stop()

    app = FastAPI(title="AutoBench Web", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.operations = operations

    @app.middleware("http")
    async def check_access(request: Request, call_next):
        host = request.headers.get("host", "")
        try:
            authority = urlsplit("//" + host)
            request_address = (
                ip_address(authority.hostname) if authority.hostname != "localhost" else None
            )
            allowed = (
                (
                    authority.hostname == "localhost"
                    or request_address in local_addresses
                    or request_address == listen_address
                    or listen_address.is_unspecified
                )
                and authority.username is None
                and authority.password is None
                and authority.netloc == host
                and not (authority.path or authority.query or authority.fragment)
                and not host.endswith(":")
                and (authority.port is None or 1 <= authority.port <= 65535)
            )
        except ValueError:
            allowed = False
        if not allowed:
            return JSONResponse({"detail": "不允许使用此 Host 访问"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{host}":
            return JSONResponse({"detail": "不允许跨域访问"}, status_code=403)
        if request.method not in {"GET", "HEAD"}:
            content_type = request.headers.get("content-type", "").split(";")[0]
            if content_type != "application/json":
                return JSONResponse({"detail": "请求必须使用 JSON"}, status_code=415)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
            "object-src 'none'; base-uri 'none'"
        )
        return response

    async def failure(request, exc):
        if isinstance(exc, ConflictError):
            status = 409
        elif isinstance(exc, FileNotFoundError):
            status = 404
        elif isinstance(exc, PermissionError):
            status = 403
        else:
            status = 422
        issues = field_errors(exc)
        for issue in issues:
            issue["message"] = clean(issue["message"])
        return JSONResponse(
            {"detail": clean(error_message(exc)), "errors": issues}, status_code=status
        )

    for error in (
        ValueError,
        OSError,
        yaml.YAMLError,
        tarfile.TarError,
        AutoBenchError,
        RequestValidationError,
    ):
        app.add_exception_handler(error, failure)

    @contextmanager
    def store_at(directory: str | None):
        path = files.path(directory or default_state)
        if not (path / "state.sqlite3").is_file():
            raise HTTPException(404, "此状态目录中没有运行数据库")
        store = Store(path)
        try:
            yield store
        finally:
            store.close()

    def saved(body: SavedBody, *, check_sources: bool = True) -> Config:
        document = files.load(body.path, body.revision)
        remember(document["document"])
        parsed = load_config_text(document["text"], Path(document["path"]), check_sources=False)
        if check_sources:
            for index, benchmark in enumerate(parsed.benchmarks):
                try:
                    inspect_archive(benchmark)
                except (OSError, ValueError, tarfile.TarError) as exc:
                    field = (
                        "source_subdir"
                        if "source_subdir" in str(exc)
                        else "source_sha256"
                        if "checksum" in str(exc)
                        else "source_archive"
                    )
                    raise ConfigurationFieldError(
                        str(exc), ["benchmarks", index, field], "source"
                    ) from exc
        return parsed

    @app.get("/api/info")
    def info():
        return {
            "version": __version__,
            "listen_host": str(listen_address),
            "config": str(files.path(config)) if config is not None else None,
            "state_dir": default_state,
            "base_dir": str(files.base_dir),
            "schema": Config.model_json_schema(),
            "template": TEMPLATE,
        }

    @app.post("/api/config/load")
    def load(body: PathBody):
        value = files.load(body.path)
        remember(value["document"])
        return value

    @app.get("/api/files")
    def browse_files(path: str | None = None):
        directory = files.path(path or files.base_dir)
        if directory.is_file():
            directory = directory.parent
        if not directory.is_dir():
            raise FileNotFoundError("目录不存在，请输入已有服务器目录")
        entries = []
        for entry in directory.iterdir():
            try:
                if entry.is_dir() or entry.is_file():
                    entries.append(
                        {
                            "name": entry.name,
                            "path": str(entry),
                            "type": "directory" if entry.is_dir() else "file",
                        }
                    )
            except OSError:
                continue
        entries.sort(key=lambda entry: (entry["type"] != "directory", entry["name"].casefold()))
        return {"path": str(directory), "parent": str(directory.parent), "entries": entries}

    @app.post("/api/connections/probe", status_code=202)
    async def connection_probe(body: ProbeBody):
        remember(body.ssh.model_dump(mode="json"))
        base = files.path(body.config_path).parent if body.config_path else files.base_dir
        resolve_ssh_paths(body.ssh, base)
        return operations.start(
            "connection_probe", lambda: services.probe_connection(body.ssh, body.connect_timeout)
        )

    @app.post("/api/config/parse")
    def parse(body: DocumentBody):
        return {"document": parse_document(body.text)}

    @app.post("/api/config/render")
    def render(body: RenderBody):
        text = yaml.safe_dump(body.document, allow_unicode=True, sort_keys=False)
        return {"text": text, "document": parse_document(text)}

    @app.post("/api/config/save")
    def save(body: SaveBody):
        value = files.save(body.path, body.text, body.revision)
        remember(value["document"])
        return value

    @app.post("/api/config/validate")
    def validate(body: SavedBody):
        parsed = services.validate_config(saved(body))
        return {
            "valid": True,
            "machines": len(parsed.machines),
            "state_dir": str(parsed.policy.state_dir),
            "benchmarks": [
                {"id": b.id, "source_sha256": b.source_sha256, "source_subdir": b.source_subdir}
                for b in parsed.benchmarks
            ],
            "warnings": ["UnixBench 可能覆盖 LANG；UB_RESULTDIR 由 AutoBench 管理。"],
        }

    @app.post("/api/inspections", status_code=202)
    async def inspect(body: InspectionBody):
        parsed = await asyncio.to_thread(saved, body, check_sources=False)
        if body.machine is not None and not any(m.id == body.machine for m in parsed.machines):
            raise HTTPException(422, "未知机器 ID")
        return operations.start(body.kind, lambda: services.inspections(parsed, body.machine))

    @app.get("/api/operations")
    async def list_operations():
        return list(operations.records.values())

    @app.get("/api/operations/{operation_id}")
    async def operation(operation_id: str):
        if operation_id not in operations.records:
            raise HTTPException(404, "操作不存在；服务重启后请从运行记录恢复")
        return operations.records[operation_id]

    @app.get("/api/runs")
    def runs(state_dir: str | None = None):
        directory = files.path(state_dir or default_state)
        if not (directory / "state.sqlite3").exists():
            return []
        with store_at(str(directory)) as store:
            return store.runs()

    @app.post("/api/runs", status_code=202)
    async def run(body: SavedBody):
        parsed = await asyncio.to_thread(saved, body)
        run_id = services.create_run(parsed)
        directory = str(parsed.policy.state_dir)
        task = operations.start(
            "run",
            lambda: services.execute_run(run_id, parsed.policy.state_dir, report=lambda _: None),
            run_id=run_id,
            key=(directory, run_id),
        )
        return {"run_id": run_id, "state_dir": directory, "operation": task}

    @app.get("/api/runs/{run_id}")
    def status(run_id: RunID, state_dir: str | None = None):
        with store_at(state_dir) as store:
            remember(json.loads(store.run(run_id)["config"]))
            result = services.run_status(store, run_id)
            result["state_dir"] = str(store.directory)
            result["controller_active"] = False
            try:
                with store.run_lock(run_id):
                    pass
            except AutoBenchError:
                result["controller_active"] = True
            for job in result["jobs"]:
                job["reason"] = clean(job["reason"])
            progress = {job["id"]: job for job in store.jobs(run_id)}
            for job in result["jobs"]:
                details = progress[job["id"]]["details"]
                ready_at = details.get("ready_at")
                job["phase"] = job["status"]
                if (
                    isinstance(ready_at, (int, float))
                    and math.isfinite(ready_at)
                    and ready_at > time.time()
                    and not details.get("remote_dir")
                    and job["status"] in {"pending", "preparing", "switching"}
                ):
                    job["phase"] = "stabilizing"
                    job["wait_seconds"] = max(0, math.ceil(ready_at - time.time()))
            return result

    @app.post("/api/runs/{run_id}/{action}", status_code=202)
    async def control(
        run_id: RunID, action: Literal["resume", "fetch", "cancel"], body: ControlBody
    ):
        with store_at(body.state_dir) as store:
            remember(json.loads(store.run(run_id)["config"]))
            directory = store.directory
            if action == "cancel":
                store.request_cancel(run_id)
                try:
                    with store.run_lock(run_id):
                        pass
                except AutoBenchError:
                    return {"status": "cancel_requested", "run_id": run_id}
                if (str(directory), run_id) in operations.active:
                    return {"status": "cancel_requested", "run_id": run_id}
            else:
                try:
                    with store.run_lock(run_id):
                        pass
                except AutoBenchError as exc:
                    raise HTTPException(409, "已有控制端正在执行此运行") from exc
        return operations.start(
            action,
            lambda: services.execute_run(
                run_id,
                directory,
                fetch_only=action == "fetch",
                cancel=action == "cancel",
                report=lambda _: None,
            ),
            run_id=run_id,
            key=(str(directory), run_id),
        )

    @app.get("/api/runs/{run_id}/events")
    def events(
        run_id: RunID,
        state_dir: str | None = None,
        after: Annotated[int, Query(ge=0)] = 0,
    ):
        with store_at(state_dir) as store:
            remember(json.loads(store.run(run_id)["config"]))
            result = store.events(run_id, after, limit=500)
            for event in result:
                event["message"] = clean(event["message"])
            return result

    def result_root(run_id: str, state_dir: str | None) -> Path:
        with store_at(state_dir) as store:
            parsed = store.config(run_id)
        base = parsed.policy.output_dir.absolute()
        if any(path.is_symlink() for path in (base, *base.parents)):
            raise HTTPException(403, "结果目录不允许经过符号链接")
        root = base / run_id
        if root.is_symlink():
            raise HTTPException(403, "结果目录不允许使用符号链接")
        if not root.is_dir():
            raise HTTPException(404, "结果尚未回收")
        return root

    def artifact(root: Path, name: str) -> Path:
        relative = PurePosixPath(name)
        if not name or relative.is_absolute() or ".." in relative.parts or "\\" in name:
            raise HTTPException(403, "无效的产物路径")
        path = root
        for part in relative.parts:
            path = path / part
            if path.is_symlink():
                raise HTTPException(403, "不允许访问符号链接产物")
        if not path.resolve().is_relative_to(root.resolve()):
            raise HTTPException(403, "产物路径越界")
        if not path.is_file():
            raise HTTPException(404, "产物不存在")
        return path

    @app.get("/api/runs/{run_id}/artifacts")
    def artifacts(run_id: RunID, state_dir: str | None = None):
        root = result_root(run_id, state_dir)
        result = []
        for path in sorted(root.rglob("*")):
            name = path.relative_to(root).as_posix()
            if any(part.startswith(".") for part in path.relative_to(root).parts):
                continue
            try:
                checked = artifact(root, name)
            except HTTPException:
                continue
            result.append({"path": name, "size": checked.stat().st_size})
        return result

    @app.get("/api/runs/{run_id}/artifact")
    def download_artifact(
        run_id: RunID,
        path: str,
        state_dir: str | None = None,
        preview: bool = False,
    ):
        selected = artifact(result_root(run_id, state_dir), path)
        if preview:
            if selected.suffix.lower() in {".html", ".htm", ".svg"}:
                raise HTTPException(415, "此类报告请下载查看")
            with selected.open("rb") as stream:
                value = stream.read(256 * 1024 + 1)
            truncated = len(value) > 256 * 1024
            text = value[: 256 * 1024].decode("utf-8", errors="replace")
            return {"text": text, "truncated": truncated}
        return FileResponse(selected, media_type="application/octet-stream", filename=selected.name)

    @app.get("/api/runs/{run_id}/archive")
    def download_archive(run_id: RunID, state_dir: str | None = None):
        root = result_root(run_id, state_dir)
        temporary = tempfile.NamedTemporaryFile(prefix="autobench-", suffix=".zip", delete=False)
        target = Path(temporary.name)
        temporary.close()
        try:
            with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(root.rglob("*")):
                    name = path.relative_to(root).as_posix()
                    if any(part.startswith(".") for part in path.relative_to(root).parts):
                        continue
                    try:
                        selected = artifact(root, name)
                    except HTTPException:
                        continue
                    archive.write(selected, f"{run_id}/{name}")
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return FileResponse(
            target,
            media_type="application/zip",
            filename=f"autobench-{run_id}.zip",
            background=BackgroundTask(target.unlink, missing_ok=True),
        )

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
