"""Local history mutations and self-contained snapshots of completed runs."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import time
import uuid
from collections.abc import Callable
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from autobench.artifacts import UnsafeArtifactPath
from autobench.config import Config, merged_env
from autobench.errors import AutoBenchError
from autobench.storage import HISTORY_TERMINAL, TERMINAL, FileLock, Store


class HistoryConflict(AutoBenchError):
    pass


@contextmanager
def _lock(lock: FileLock):
    with ExitStack() as stack:
        try:
            stack.enter_context(lock)
        except AutoBenchError as exc:
            raise HistoryConflict("此运行或历史目录正在使用，请稍后重试") from exc
        yield


@contextmanager
def _open_directory(path: Path):
    """Anchor mutations and copies to a directory reached without symlink traversal."""
    path = path.absolute()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            if part == "..":
                raise UnsafeArtifactPath("无效的结果目录")
            try:
                child = os.open(part, flags, dir_fd=fd)
            except NotADirectoryError as exc:
                raise UnsafeArtifactPath("结果目录不允许经过符号链接或非目录文件") from exc
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def _directory_exists(parent: int, name: str) -> bool:
    try:
        mode = os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(mode):
        raise UnsafeArtifactPath("结果目录不允许使用符号链接或非目录文件")
    return True


def _remove(parent: int, name: str):
    if _directory_exists(parent, name):
        shutil.rmtree(name, dir_fd=parent)


def _changes(store: Store) -> FileLock:
    return FileLock(store.directory / "locks" / "history.lock")


def _recover_change(store: Store, change: dict):
    root = Path(change["root"])
    stage = change["stage"]
    exists = store.db.execute("SELECT 1 FROM runs WHERE id=?", (change["run_id"],)).fetchone()
    with suppress(FileNotFoundError), _open_directory(root.parent) as parent:
        if change["action"] == "delete" and exists:
            if _directory_exists(parent, stage):
                if _directory_exists(parent, root.name):
                    raise HistoryConflict("结果目录已被占用，无法恢复待删除的文件")
                os.rename(stage, root.name, src_dir_fd=parent, dst_dir_fd=parent)
        else:
            _remove(parent, stage)
            if change["action"] == "merge" and not exists:
                if _directory_exists(parent, root.name):
                    with _open_directory(root) as published:
                        try:
                            marker = os.stat(stage, dir_fd=published, follow_symlinks=False)
                        except FileNotFoundError:
                            marker = None
                    if marker and stat.S_ISREG(marker.st_mode):
                        _remove(parent, root.name)
        os.fsync(parent)
    _clear_change(store, change["run_id"])


def _recover_pending(store: Store):
    for row in store.db.execute("SELECT * FROM run_file_changes").fetchall():
        # A failed or externally moved directory must not block unrelated records.
        with suppress(OSError, UnsafeArtifactPath, HistoryConflict):
            with _lock(store.run_lock(row["run_id"])):
                _recover_change(store, dict(row))


def recover(store: Store):
    if store.db.execute("SELECT 1 FROM run_file_changes LIMIT 1").fetchone():
        with suppress(HistoryConflict), _lock(_changes(store)):
            _recover_pending(store)


def _clear_change(store: Store, run_id: str):
    with store.db:
        store.db.execute("DELETE FROM run_file_changes WHERE run_id=?", (run_id,))


def _plan_change(store: Store, run_id: str, action: str, root: Path) -> dict:
    change = {
        "run_id": run_id,
        "action": action,
        "root": str(root),
        "stage": f".autobench-{action}-{uuid.uuid4().hex}",
    }
    store.db.execute(
        "INSERT INTO run_file_changes VALUES (:run_id, :action, :root, :stage)", change
    )
    return change


def allowed(store: Store, run_id: str, status: str) -> bool:
    if status != "merged" and status not in HISTORY_TERMINAL:
        return False
    if store.db.execute("SELECT 1 FROM run_file_changes WHERE run_id=?", (run_id,)).fetchone():
        return False
    return not store.db.execute(
        "SELECT 1 FROM jobs WHERE run_id=? AND ("
        f"status NOT IN ({','.join('?' for _ in TERMINAL)}, 'blocked') "
        "OR (json_extract(details, '$.remote_dir') "
        "IS NOT NULL AND NOT COALESCE(json_extract(details, '$.archived'), 0))) LIMIT 1",
        (run_id, *sorted(TERMINAL)),
    ).fetchone()


def _require_closed(store: Store, run_id: str) -> dict:
    try:
        run = store.run(run_id)
    except AutoBenchError as exc:
        raise FileNotFoundError(f"运行记录不存在：{run_id}") from exc
    if not allowed(store, run_id, run["status"]):
        raise HistoryConflict(f"运行尚未封存或仍有待处理的任务：{run_id}")
    return run


def _ensure_dedicated_root(store: Store, run_id: str, root: Path):
    if store.directory.is_relative_to(root):
        raise UnsafeArtifactPath("结果目录与状态目录重叠")
    for row in store.runs():
        if row["id"] == run_id:
            continue
        try:
            other = store.result_directory(row["id"])
        except AutoBenchError:
            if store.db.execute("SELECT 1 FROM runs WHERE id=?", (row["id"],)).fetchone():
                raise
            continue
        if other.is_relative_to(root) or root.is_relative_to(other):
            raise UnsafeArtifactPath("结果目录与其他运行的结果目录重叠")


def delete(store: Store, run_id: str) -> dict:
    with _lock(_changes(store)):
        _recover_pending(store)
        with _lock(store.run_lock(run_id)):
            _require_closed(store, run_id)
            root = store.result_directory(run_id)
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                _ensure_dedicated_root(store, run_id, root)
                change = _plan_change(store, run_id, "delete", root)
            try:
                with suppress(FileNotFoundError), _open_directory(root.parent) as parent:
                    if _directory_exists(parent, root.name):
                        os.rename(root.name, change["stage"], src_dir_fd=parent, dst_dir_fd=parent)
                        os.fsync(parent)
                with store.db:
                    store.db.execute("DELETE FROM events WHERE run_id=?", (run_id,))
                    store.db.execute("DELETE FROM jobs WHERE run_id=?", (run_id,))
                    store.db.execute("DELETE FROM runs WHERE id=?", (run_id,))
                    # Stale clients and merged provenance must never refer to a later run.
                    store.db.execute("INSERT INTO retired_run_ids VALUES (?)", (run_id,))
            except BaseException:
                with suppress(OSError, UnsafeArtifactPath, HistoryConflict):
                    _recover_change(store, change)
                raise
            try:
                _recover_change(store, change)
            except (OSError, UnsafeArtifactPath):
                return {"run_id": run_id, "cleanup_pending": True}
            return {"run_id": run_id, "cleanup_pending": False}


@dataclass
class Source:
    metadata: dict
    root: Path
    jobs: list[dict]
    events: list[dict]


def _group_fingerprints(config: Config, source_key: str) -> dict[tuple[str, str, str], str]:
    fingerprints = {}
    for machine in config.machines:
        for system in machine.systems:
            ssh = machine.connection(system)
            for benchmark in config.benchmarks:
                group = (machine.id, system.id, benchmark.id)
                identity = [
                    group,
                    (ssh.host, ssh.port, ssh.username, ssh.host_key_alias),
                    (ssh.proxy.host, ssh.proxy.port) if ssh.proxy else None,
                    system.boot_entry,
                    system.grub_entry,
                    merged_env({}, system.env, benchmark.env),
                    benchmark.source_sha256 or source_key,
                    benchmark.source_subdir,
                    benchmark.type,
                    benchmark.make_args,
                    benchmark.args,
                    benchmark.tests,
                    benchmark.warmup,
                    config.policy.settle_seconds,
                ]
                encoded = json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()
                fingerprints[group] = hashlib.sha256(encoded).hexdigest()
    return fingerprints


def _original_source(
    store: Store,
    run: dict,
    clean: Callable[[str], str],
) -> Source:
    run_id = run["id"]
    jobs = store.jobs(run_id)
    if not jobs:
        raise HistoryConflict(f"来源记录没有任务：{run_id}")
    key = jobs[0]["id"]
    config = store.config(run_id)
    fingerprints = _group_fingerprints(config, key)
    for job in jobs:
        identity = (job["machine"], job["system"], job["benchmark"])
        reason = job["details"].get("reason")
        job["details"] = {
            "group_fingerprint": fingerprints[identity],
            "reason": clean(reason) if reason else None,
        }
    events = store.events(run_id)
    for event in events:
        event["message"] = clean(event["message"])
    return Source(
        {"key": key, "run_id": run_id, "created": run["created"], "status": run["status"]},
        store.result_directory(run_id),
        jobs,
        events,
    )


def _snapshot_sources(store: Store, run: dict) -> list[Source]:
    jobs_by_source: dict[str, list[dict]] = {}
    events_by_source: dict[str, list[dict]] = {}
    root = store.result_directory(run["id"])
    for job in store.jobs(run["id"]):
        jobs_by_source.setdefault(job["details"]["source_key"], []).append(job)
    for event in store.events(run["id"], with_source=True):
        events_by_source.setdefault(event["source_key"], []).append(event)
    return [
        Source(
            metadata,
            root / "sources" / metadata["folder"],
            jobs_by_source[metadata["key"]],
            events_by_source.get(metadata["key"], []),
        )
        for metadata in store.merge_sources(run["id"])
    ]


def _copy_artifacts(source: Path, target: Path, *, snapshot: bool):
    with ExitStack() as stack:
        try:
            source_fd = stack.enter_context(_open_directory(source))
        except FileNotFoundError:
            if snapshot:
                raise HistoryConflict("合并记录的本地快照缺失")
            return
        for directory, subdirs, files, parent in os.fwalk(".", dir_fd=source_fd, onerror=_raise_io):
            subdirs[:] = [name for name in subdirs if not name.startswith(".") and "\\" not in name]
            destination = target / directory
            destination.mkdir(mode=0o700, parents=True, exist_ok=True)
            for name in files:
                if name.startswith(".") or "\\" in name:
                    continue
                if directory == "." and name in {"config.json", "events.json"}:
                    continue
                mode = os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode
                if not stat.S_ISREG(mode):
                    continue
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                with os.fdopen(os.open(name, flags, dir_fd=parent), "rb") as original:
                    if not stat.S_ISREG(os.fstat(original.fileno()).st_mode):
                        raise UnsafeArtifactPath("产物类型发生变化")
                    with open(destination / name, "xb", opener=_private_opener) as copied:
                        shutil.copyfileobj(original, copied)
                        copied.flush()
                        os.fsync(copied.fileno())


def _raise_io(error):
    raise error


def _private_opener(name, flags):
    return os.open(name, flags, 0o600)


def _write_json(path: Path, value):
    with open(path, "x", encoding="utf-8", opener=_private_opener) as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())


def _public_source(metadata: dict) -> dict:
    return {key: metadata[key] for key in ("run_id", "status", "created", "folder")}


def public_sources(store: Store, run_id: str) -> list[dict]:
    return [_public_source(source) for source in store.merge_sources(run_id)]


def _copy_sources(stage: Path, sources: list[Source]):
    used = set()
    for source in sources:
        run_id = source.metadata["run_id"]
        folder = run_id
        if folder in used:
            folder = f"{run_id}-{source.metadata['key']}"
        used.add(folder)
        snapshot = "folder" in source.metadata
        source.metadata["folder"] = folder
        target = stage / "sources" / folder
        target.mkdir(mode=0o700, parents=True)
        _copy_artifacts(source.root, target, snapshot=snapshot)
        _write_json(
            target / "events.json",
            [{key: event[key] for key in ("time", "message")} for event in source.events],
        )
    _write_json(stage / "sources.json", [_public_source(source.metadata) for source in sources])
    for _, _, _, directory in os.fwalk(stage, topdown=False):
        os.fsync(directory)


def _insert_snapshot(store: Store, run_id: str, now: float, sources: list[Source]):
    store.db.execute("INSERT INTO runs VALUES (?, '{}', 'merged', 0, ?, ?)", (run_id, now, now))
    store.db.execute(
        "INSERT INTO run_merges VALUES (?, ?)",
        (run_id, json.dumps([source.metadata for source in sources])),
    )
    ordinal = 0
    events = []
    for source in sources:
        ids = {}
        origin = source.metadata
        for job in source.jobs:
            job_id = uuid.uuid4().hex
            ids[job["id"]] = job_id
            fields = (job["machine"], job["system"], job["benchmark"], job["repetition"])
            output = "/".join(("sources", origin["folder"], *fields[:3], f"round-{fields[3]:03d}"))
            details = {
                **job["details"],
                "source_key": origin["key"],
                "source_run_id": origin["run_id"],
                "result_relative": output,
            }
            store.db.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    run_id,
                    *fields,
                    ordinal,
                    job["status"],
                    json.dumps(details),
                    job["updated"],
                ),
            )
            ordinal += 1
        for event in source.events:
            events.append(
                (
                    event["time"],
                    event["sequence"],
                    origin["key"],
                    ids.get(event["job_id"]),
                    event["message"],
                )
            )
    for when, _, source_key, job_id, message in sorted(events):
        cursor = store.db.execute(
            "INSERT INTO events(run_id,job_id,time,message) VALUES(?,?,?,?)",
            (run_id, job_id, when, message),
        )
        store.db.execute(
            "INSERT INTO merged_event_sources VALUES (?, ?)", (cursor.lastrowid, source_key)
        )


def merge(store: Store, run_ids: list[str], clean: Callable[[str], str]) -> str:
    with _lock(_changes(store)), ExitStack() as stack:
        _recover_pending(store)
        for run_id in sorted(run_ids):
            stack.enter_context(_lock(store.run_lock(run_id)))
        rows = [_require_closed(store, run_id) for run_id in run_ids]
        originals = {}
        for row in sorted(
            rows,
            key=lambda row: (row["status"] != "merged", row["created"], row["id"]),
            reverse=True,
        ):
            snapshots = (
                _snapshot_sources(store, row)
                if row["status"] == "merged"
                else [_original_source(store, row, clean)]
            )
            for source in snapshots:
                originals.setdefault(source.metadata["key"], source)
        sources = sorted(
            originals.values(),
            key=lambda s: (s.metadata["created"], s.metadata["run_id"], s.metadata["key"]),
        )
        if len(sources) < 2:
            raise HistoryConflict("请选择至少两条不同的原始运行记录")
        with _open_directory(store.directory) as state:
            with suppress(FileExistsError):
                os.mkdir("merged-results", mode=0o700, dir_fd=state)
            os.fsync(state)
        output = store.directory / "merged-results"
        with _open_directory(output) as parent:
            now = time.time()
            with store.db:
                store.db.execute("BEGIN IMMEDIATE")
                for run_id in store.available_run_ids(now):
                    if not os.path.lexists(output / run_id):
                        break
                change = _plan_change(store, run_id, "merge", output / run_id)
            stage = change["stage"]
            try:
                os.mkdir(stage, mode=0o700, dir_fd=parent)
                _write_json(output / stage / stage, run_id)
                _copy_sources(output / stage, sources)
                with store.db:
                    store.db.execute("BEGIN IMMEDIATE")
                    if os.path.lexists(output / run_id):
                        raise HistoryConflict("合并结果目录已被占用")
                    os.rename(stage, run_id, src_dir_fd=parent, dst_dir_fd=parent)
                    os.fsync(parent)
                    _insert_snapshot(store, run_id, now, sources)
                    store.db.execute("DELETE FROM run_file_changes WHERE run_id=?", (run_id,))
            except BaseException:
                with suppress(OSError, UnsafeArtifactPath, HistoryConflict):
                    _recover_change(store, change)
                raise
            return run_id
