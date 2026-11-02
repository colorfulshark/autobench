from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

from autobench.config import Config
from autobench.errors import AutoBenchError

TERMINAL = {"succeeded", "failed", "interrupted", "cancelled"}


def private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


class FileLock:
    def __init__(self, path: Path):
        self.path = path
        self.stream = None

    def __enter__(self):
        private_dir(self.path.parent)
        self.stream = self.path.open("a+")
        self.path.chmod(0o600)
        try:
            fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.stream.close()
            raise AutoBenchError(f"another controller holds lock {self.path.name}") from exc
        return self

    def __exit__(self, *args):
        self.stream.close()


class Store:
    def __init__(self, state_dir: Path):
        self.directory = private_dir(state_dir.expanduser().resolve())
        path = self.directory / "state.sqlite3"
        self.db = sqlite3.connect(path)
        path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, config TEXT NOT NULL, status TEXT NOT NULL,
                cancel_requested INTEGER NOT NULL DEFAULT 0, created REAL, updated REAL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                machine TEXT NOT NULL, system TEXT NOT NULL, benchmark TEXT NOT NULL,
                repetition INTEGER NOT NULL, ordinal INTEGER NOT NULL,
                status TEXT NOT NULL, details TEXT NOT NULL, updated REAL
            );
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                job_id TEXT, time REAL NOT NULL, message TEXT NOT NULL
            );
        """)

    def close(self):
        self.db.close()

    def create(self, config: Config) -> str:
        run_id = uuid.uuid4().hex
        now = time.time()
        with self.db:
            self.db.execute(
                "INSERT INTO runs VALUES (?, ?, 'pending', 0, ?, ?)",
                (run_id, config.model_dump_json(), now, now),
            )
            ordinal = 0
            for machine in config.machines:
                for system in machine.systems:
                    for benchmark in config.benchmarks:
                        for repetition in range(1, benchmark.repetitions + 1):
                            job_id = uuid.uuid5(
                                uuid.UUID(run_id),
                                f"{machine.id}/{system.id}/{benchmark.id}/{repetition}",
                            ).hex
                            self.db.execute(
                                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    job_id,
                                    run_id,
                                    machine.id,
                                    system.id,
                                    benchmark.id,
                                    repetition,
                                    ordinal,
                                    "pending",
                                    "{}",
                                    now,
                                ),
                            )
                            ordinal += 1
        return run_id

    def run(self, run_id: str) -> dict:
        row = self.db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise AutoBenchError(f"unknown run: {run_id}")
        return dict(row)

    def config(self, run_id: str) -> Config:
        return Config.model_validate_json(self.run(run_id)["config"])

    def jobs(self, run_id: str, machine: str | None = None) -> list[dict]:
        sql = "SELECT * FROM jobs WHERE run_id = ?"
        args = [run_id]
        if machine is not None:
            sql += " AND machine = ?"
            args.append(machine)
        return [self._job(row) for row in self.db.execute(sql + " ORDER BY ordinal", args)]

    def job(self, job_id: str) -> dict:
        row = self.db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise AutoBenchError(f"unknown job: {job_id}")
        return self._job(row)

    @staticmethod
    def _job(row):
        job = dict(row)
        job["details"] = json.loads(job["details"])
        return job

    def update(self, job_id: str, status: str | None = None, **details):
        job = self.job(job_id)
        job["details"].update(details)
        with self.db:
            self.db.execute(
                "UPDATE jobs SET status=?, details=?, updated=? WHERE id=?",
                (status or job["status"], json.dumps(job["details"]), time.time(), job_id),
            )

    def event(self, run_id: str, job_id: str | None, message: str):
        with self.db:
            self.db.execute(
                "INSERT INTO events(run_id,job_id,time,message) VALUES(?,?,?,?)",
                (run_id, job_id, time.time(), message),
            )

    def events(self, run_id: str) -> list[dict]:
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM events WHERE run_id=? ORDER BY sequence", (run_id,)
            )
        ]

    def request_cancel(self, run_id: str):
        self.run(run_id)
        with self.db:
            self.db.execute("UPDATE runs SET cancel_requested=1 WHERE id=?", (run_id,))

    def set_run_status(self, run_id: str, status: str):
        with self.db:
            self.db.execute(
                "UPDATE runs SET status=?, updated=? WHERE id=?", (status, time.time(), run_id)
            )

    def summarize(self, run_id: str) -> str:
        jobs = self.jobs(run_id)
        if all(j["status"] == "succeeded" for j in jobs):
            status = "succeeded"
        elif all(j["status"] in TERMINAL | {"blocked"} for j in jobs):
            status = "cancelled" if self.run(run_id)["cancel_requested"] else "failed"
        else:
            status = "paused"
        with self.db:
            self.db.execute(
                "UPDATE runs SET status=?, updated=? WHERE id=?", (status, time.time(), run_id)
            )
        return status

    def machine_lock(self, machine_id: str) -> FileLock:
        key = hashlib.sha256(machine_id.encode()).hexdigest()
        return FileLock(self.directory / "locks" / f"machine-{key}.lock")

    def run_lock(self, run_id: str) -> FileLock:
        return FileLock(self.directory / "locks" / f"run-{run_id}.lock")

    def snapshot(self, run_id: str, root: Path):
        private_dir(root)
        config = json.loads(self.run(run_id)["config"])
        # Keep the frozen database configuration intact for resume. Only the
        # exported copy is redacted, including explicitly set system overrides.
        for machine in config["machines"]:
            for ssh in [machine["ssh"], *(system["ssh"] for system in machine["systems"])]:
                for settings in (ssh, ssh.get("proxy") or {}):
                    if settings.get("password") is not None:
                        settings["password"] = "********"
        for name, value in (
            ("config.json", config),
            ("events.json", self.events(run_id)),
        ):
            temp = root / (name + ".tmp")
            with temp.open("w") as out:
                os.chmod(temp, 0o600)
                json.dump(value, out, ensure_ascii=False, indent=2)
                out.flush()
                os.fsync(out.fileno())
            temp.replace(root / name)
