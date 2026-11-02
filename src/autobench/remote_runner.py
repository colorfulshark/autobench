"""Standalone remote helper. Keep compatible with Python 3.8 and stdlib only.

Control operations print a single JSON response. The worker is a detached systemd
service; durable job files, rather than the SSH channel, are the source of truth.
"""

import datetime
import fcntl
import hashlib
import json
import os
import platform
import re
import resource
import shutil
import signal
import subprocess
import sys
import tarfile
import uuid
from pathlib import Path, PurePosixPath

TERMINAL = {"succeeded", "failed", "interrupted", "cancelled"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(temp), str(path))
    directory = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def managed(argv):
    return argv if os.geteuid() == 0 else ["sudo", "-n"] + argv


def command(argv, privileged=False, timeout=15):
    result = subprocess.run(
        managed(argv) if privileged else argv,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError("command failed: %s: %s" % (argv[0], result.stderr.strip()))
    return result.stdout


def efi():
    text = command(["efibootmgr"], privileged=True)
    result = {"raw": text, "entries": {}}
    for line in text.splitlines():
        match = re.match(r"Boot(Current|Next):\s*([0-9a-fA-F]{4})", line)
        if match:
            result[match.group(1).lower()] = match.group(2).upper()
        match = re.match(r"Boot([0-9a-fA-F]{4})(\*?)\s+(.*)", line)
        if match:
            result["entries"][match.group(1).upper()] = {
                "active": bool(match.group(2)),
                "label": match.group(3),
            }
    return result


def probe(payload):
    result = {
        "schema_version": 1,
        "python": list(sys.version_info[:3]),
        "python_executable": sys.executable,
        "uid": os.getuid(),
        "gid": os.getgid(),
        "home": str(Path.home()),
        "machine_id": Path("/etc/machine-id").read_text().strip(),
        "boot_id": boot_id(),
        "hostname": platform.node(),
        "commands": {name: shutil.which(name) for name in payload.get("requirements", [])},
    }
    try:
        result["efi"] = efi()
    except Exception as exc:
        result["efi_error"] = str(exc)
    try:
        result["systemd_version"] = int(command(["systemctl", "--version"]).split()[1])
        command(["systemctl", "show", "--property=Version"], privileged=True)
        result["systemd_available"] = True
    except Exception as exc:
        result["systemd_error"] = str(exc)
    result["disk_free"] = shutil.disk_usage(str(Path.home())).free
    return result


def job_path(payload):
    path = Path(payload["job_dir"])
    if not path.is_absolute() or path.name != payload["job_id"]:
        raise ValueError("invalid absolute job path")
    if not re.fullmatch(r"[0-9a-f]{32}", payload["job_id"]):
        raise ValueError("invalid job id")
    return path


def lock(path, nonblocking=True):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, "a+")


def stage(payload):
    directory = job_path(payload)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock(directory / "dispatch.lock"):
        path = directory / "manifest.json"
        existing = read_json(path)
        if existing is not None and existing != payload["manifest"]:
            raise RuntimeError("immutable job manifest mismatch")
        if existing is None:
            atomic_json(path, payload["manifest"])
        (directory / "artifacts").mkdir(mode=0o700, exist_ok=True)
    source = Path(payload["manifest"]["source_path"])
    return {
        "staged": True,
        "source_valid": source.is_file() and digest(source) == payload["manifest"]["source_sha256"],
    }


def service(directory):
    unit = "autobench-" + directory.name + ".service"
    # Explicit properties also work after the transient unit was garbage collected.
    result = subprocess.run(
        [
            "systemctl",
            "show",
            unit,
            "--no-pager",
            "--property=LoadState,ActiveState,SubState,Result,ExecMainStatus",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if "LoadState" not in properties or "ActiveState" not in properties:
        raise RuntimeError("systemd service status is unavailable")
    return properties


def finalize(directory, state):
    """Seal an immutable inventory only after the process tree is no longer writing."""
    artifacts = directory / "artifacts"
    artifacts.mkdir(mode=0o700, exist_ok=True)
    atomic_json(directory / "state.json", state)
    atomic_json(
        artifacts / "task.json",
        {
            "schema_version": 1,
            "state": state,
            "manifest": read_json(directory / "manifest.json"),
        },
    )
    files = []
    for current, dirs, names in os.walk(str(artifacts), followlinks=False):
        for name in dirs:
            if (Path(current) / name).is_symlink():
                raise RuntimeError("artifact directory symlink is not allowed")
        for name in sorted(names):
            path = Path(current) / name
            if path.is_symlink() or not path.is_file():
                raise RuntimeError("artifact must be a regular file")
            files.append(
                {
                    "path": str(path.relative_to(artifacts)),
                    "size": path.stat().st_size,
                    "sha256": digest(path),
                }
            )
    atomic_json(directory / "artifacts.json", {"schema_version": 1, "files": files})


def status(payload):
    directory = job_path(payload)
    manifest = read_json(directory / "manifest.json")
    if manifest is None:
        return {"phase": "absent"}
    with lock(directory / "dispatch.lock"):
        state = read_json(directory / "state.json", {})
        unit = service(directory)
        active = unit.get("ActiveState") in ("active", "activating", "deactivating", "reloading")
        inventory = read_json(directory / "artifacts.json")
        # A completed worker may still be returning to systemd; wait for quiescence.
        if inventory and not active:
            return {"phase": "terminal", "state": state, "inventory": inventory}
        if manifest["boot_id"] != boot_id():
            if active:
                return {"phase": "uncertain", "message": "unexpected active unit after boot change"}
            state = {
                "status": "interrupted",
                "reason": "remote boot ID changed",
                "finished": now(),
                "boot_id": manifest["boot_id"],
            }
            finalize(directory, state)
        elif active:
            return {"phase": "active", "state": state, "service": unit}
        elif state.get("status") in TERMINAL:
            # Reconcile a worker killed during the final inventory write.
            finalize(directory, state)
        elif unit.get("LoadState") == "loaded" or unit.get("ActiveState") == "failed":
            state = {
                "status": "interrupted",
                "reason": "service exited without final state",
                "finished": now(),
                "service": unit,
                "boot_id": manifest["boot_id"],
            }
            finalize(directory, state)
        elif read_json(directory / "submitted.json"):
            return {"phase": "uncertain", "message": "submission recorded but unit/state is absent"}
        else:
            return {"phase": "staged"}
        return {
            "phase": "terminal",
            "state": state,
            "inventory": read_json(directory / "artifacts.json"),
        }


def start(payload):
    directory = job_path(payload)
    with lock(directory / "dispatch.lock"):
        manifest = read_json(directory / "manifest.json")
        if manifest is None:
            raise RuntimeError("job is not staged")
        if (directory / "submitted.json").exists() or (directory / "state.json").exists():
            return {"submitted": False, "existing": True}
        if service(directory).get("LoadState") != "not-found":
            return {"submitted": False, "existing": True}
        if boot_id() != manifest["boot_id"]:
            raise RuntimeError("boot ID changed before submission")
        # Reserve the OS until local collection is acknowledged, including between jobs.
        root = directory.parent.parent
        with lock(root / "reservation.lock"):
            owner = read_json(root / "reservation.json")
            if owner and owner["job_id"] != directory.name:
                raise RuntimeError("another job owns this OS: " + owner["job_id"])
            atomic_json(root / "reservation.json", {"job_id": directory.name})
        atomic_json(directory / "submitted.json", {"time": now(), "boot_id": boot_id()})
        argv = [
            "systemd-run",
            "--unit=autobench-" + directory.name,
            "--property=Type=exec",
            "--property=Restart=no",
            "--property=KillMode=control-group",
            "--property=TimeoutStopSec=30s",
            "--uid=" + str(os.getuid()),
            "--gid=" + str(os.getgid()),
            sys.executable,
            str(directory / "runner.py"),
            "worker",
            str(directory / "manifest.json"),
        ]
        # systemd expands dollars in ExecStart arguments even without a shell.
        argv[-4:] = [arg.replace("$", "$$") for arg in argv[-4:]]
        try:
            command(argv, privileged=True, timeout=30)
        except subprocess.TimeoutExpired:
            # It may have reached systemd. Preserve the marker and reconcile; never resubmit.
            return {"submitted": True, "uncertain": True}
        except Exception as exc:
            # Check the unit before classifying a failed systemd-run response.
            unit = service(directory)
            if unit.get("ActiveState") not in ("active", "activating", "deactivating"):
                finalize(
                    directory,
                    {
                        "status": "failed",
                        "reason": str(exc),
                        "finished": now(),
                        "boot_id": manifest["boot_id"],
                    },
                )
        return {"submitted": True}


def acknowledge(payload):
    directory = job_path(payload)
    if not (directory / "artifacts.json").exists():
        raise RuntimeError("cannot acknowledge an unsealed job")
    root = directory.parent.parent
    with lock(root / "reservation.lock"):
        owner = read_json(root / "reservation.json")
        if owner and owner["job_id"] == directory.name:
            (root / "reservation.json").unlink()
        atomic_json(directory / "collected.json", {"time": now()})
    return {"acknowledged": True}


def cancel(payload):
    directory = job_path(payload)
    if not (directory / "manifest.json").exists():
        return {"phase": "absent"}
    with lock(directory / "dispatch.lock"):
        unit = service(directory)
        if unit.get("LoadState") == "loaded":
            command(
                ["systemctl", "stop", "autobench-" + directory.name + ".service"],
                privileged=True,
                timeout=45,
            )
        state = read_json(directory / "state.json", {})
        if state.get("status") not in TERMINAL:
            finalize(directory, {"status": "cancelled", "finished": now(), "boot_id": boot_id()})
    return status(payload)


def switch(payload):
    root = Path(payload["root"])
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock(root / "reservation.lock"):
        if read_json(root / "reservation.json"):
            raise RuntimeError("uncollected job prevents a system switch")
        if boot_id() != payload["previous_boot_id"]:
            raise RuntimeError("boot ID changed before reboot request")
        target = payload["boot_entry"]
        if not re.fullmatch(r"[0-9A-F]{4}", target):
            raise ValueError("invalid boot entry")
        current = efi()
        if target not in current["entries"] or not current["entries"][target]["active"]:
            raise RuntimeError("target EFI entry is missing or inactive")
        if current.get("next"):
            raise RuntimeError("existing BootNext prevents reboot")
        marker = root / "switches" / (payload["token"] + ".json")
        if marker.exists():
            raise RuntimeError("reboot was already submitted; reconcile instead of resubmitting")
        atomic_json(marker, {"target": target, "boot_id": boot_id(), "time": now()})
        command(["efibootmgr", "-n", target], privileged=True)
        if efi().get("next") != target:
            raise RuntimeError("BootNext readback mismatch")
        command(["systemctl", "reboot", "--no-block"], privileged=True)
    return {"reboot_requested": True}


def extract_source(source, destination):
    """No links/devices and no traversal; works without Python 3.12 tar filters."""
    destination.mkdir(mode=0o700, exist_ok=True)
    with tarfile.open(str(source), "r:*") as archive:
        seen = set()
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isdir() or member.isfile()):
                raise ValueError("unsafe archive member: " + member.name)
            target = destination.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
            else:
                if str(path) in seen:
                    raise ValueError("duplicate archive file")
                seen.add(str(path))
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with archive.extractfile(member) as incoming, target.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                target.chmod(0o700 if member.mode & 0o111 else 0o600)


def collect_system():
    result = {"schema_version": 1, "time": now(), "boot_id": boot_id(), "errors": {}}
    for name in (
        "/etc/os-release",
        "/proc/cpuinfo",
        "/proc/meminfo",
        "/proc/cmdline",
        "/proc/loadavg",
        "/proc/mounts",
        "/etc/machine-id",
        "/proc/self/cgroup",
    ):
        try:
            with open(name) as stream:
                result[name] = stream.read(4 * 1024 * 1024)
        except Exception as exc:
            result["errors"][name] = str(exc)
    result["limits"] = {}
    if hasattr(os, "sched_getaffinity"):
        result["cpu_affinity"] = sorted(os.sched_getaffinity(0))
    for name in dir(resource):
        if name.startswith("RLIMIT_"):
            try:
                result["limits"][name] = list(resource.getrlimit(getattr(resource, name)))
            except (OSError, ValueError):
                pass
    result["cpu_policy"] = {}
    for pattern in (
        "devices/system/cpu/cpu*/cpufreq/scaling_governor",
        "devices/system/cpu/cpu*/cpufreq/scaling_min_freq",
        "devices/system/cpu/cpu*/cpufreq/scaling_max_freq",
    ):
        for path in Path("/sys").glob(pattern):
            try:
                result["cpu_policy"][str(path)] = path.read_text().strip()
            except OSError as exc:
                result["errors"][str(path)] = str(exc)
    for name, argv in {
        "uname": ["uname", "-a"],
        "cpu": ["lscpu"],
        "numa": ["numactl", "--hardware"],
        "disks": ["lsblk", "-J", "-O"],
        "mounts": ["findmnt", "-J"],
        "efi": ["efibootmgr"],
        "compiler": ["gcc", "--version"],
    }.items():
        try:
            # Optional collectors never prompt for sudo.
            result[name] = command(argv, timeout=5)
        except Exception as exc:
            result["errors"][name] = str(exc)
    return result


class WorkerCancelled(Exception):
    pass


def cancellation_signal(signum, frame):
    raise WorkerCancelled("worker received signal %s" % signum)


def run_process(argv, cwd, env, timeout, stdout, stderr, record):
    record.update({"argv": argv, "cwd": str(cwd), "started": now(), "timeout": timeout})
    process = None
    try:
        with stdout.open("wb") as out, stderr.open("wb") as err:
            process = subprocess.Popen(
                argv,
                cwd=str(cwd),
                env=env,
                stdout=out,
                stderr=err,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                record["exit_code"] = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                record["timed_out"] = True
                raise RuntimeError("command exceeded its timeout")
            if record["exit_code"] != 0:
                raise RuntimeError("command exited with code %s" % record["exit_code"])
    finally:
        if process is not None:
            # Always remove the whole process group, even if its leader already exited.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            record["exit_code"] = process.wait()
        record["finished"] = now()


def worker(manifest_path):
    os.umask(0o077)
    manifest_path = Path(manifest_path)
    directory = manifest_path.parent
    manifest = read_json(manifest_path)
    artifacts = directory / "artifacts"
    artifacts.mkdir(mode=0o700, exist_ok=True)
    state = {"status": "preparing", "started": now(), "boot_id": boot_id(), "commands": {}}
    held_lock = None
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, cancellation_signal)
    try:
        held_lock = lock(directory.parent.parent / "worker.lock")
        if state["boot_id"] != manifest["boot_id"]:
            raise RuntimeError("boot ID changed before worker startup")
        atomic_json(directory / "state.json", state)
        atomic_json(artifacts / "system-before.json", collect_system())
        source = Path(manifest["source_path"])
        if digest(source) != manifest["source_sha256"]:
            raise RuntimeError("source archive checksum mismatch")
        source_root = directory / "source"
        extract_source(source, source_root)
        execution = manifest["execution"]
        subdir = PurePosixPath(execution["source_subdir"])
        if subdir.is_absolute() or ".." in subdir.parts:
            raise ValueError("invalid source directory")
        cwd = source_root.joinpath(*subdir.parts)
        base = dict(os.environ)
        run_source = (cwd / "Run").read_text(errors="replace")
        version = re.search(r'\$version\s*=\s*[\'"]([^\'"]+)', run_source)
        atomic_json(
            artifacts / "source-metadata.json",
            {
                "sha256": manifest["source_sha256"],
                "source_subdir": str(subdir),
                "detected_version": version.group(1) if version else None,
            },
        )
        toolchain = {}
        for name, argv in {
            "compiler": execution["compiler_argv"] + ["--version"],
            "make": ["make", "--version"],
        }.items():
            try:
                toolchain[name] = {"argv": argv, "output": command(argv, timeout=5)}
            except Exception as exc:
                toolchain[name] = {"argv": argv, "error": str(exc)}
        atomic_json(artifacts / "toolchain.json", toolchain)
        atomic_json(artifacts / "build-environment.json", base)
        state["commands"]["build"] = {}
        run_process(
            execution["build_argv"],
            cwd,
            base,
            execution["build_timeout"],
            artifacts / "build.stdout",
            artifacts / "build.stderr",
            state["commands"]["build"],
        )
        result_dir = artifacts / execution["artifact_dir"]
        result_dir.mkdir(mode=0o700, exist_ok=True)
        env = dict(base)
        env.update(manifest["system_env"])
        env.update(manifest["benchmark_env"])
        env[execution["result_env"]] = str(result_dir)
        atomic_json(artifacts / "environment.json", env)
        atomic_json(
            artifacts / "environment-overrides.json",
            {
                "system": manifest["system_env"],
                "benchmark": manifest["benchmark_env"],
                "generated": {execution["result_env"]: str(result_dir)},
                "meaning": "launch environment; the benchmark may change variables internally",
            },
        )
        state["status"] = "running"
        atomic_json(directory / "state.json", state)
        state["commands"]["run"] = {}
        run_process(
            execution["run_argv"],
            cwd,
            env,
            execution["timeout"],
            artifacts / "run.stdout",
            artifacts / "run.stderr",
            state["commands"]["run"],
        )
        state["status"] = "succeeded"
    except WorkerCancelled as exc:
        state.update({"status": "cancelled", "reason": str(exc)})
    except Exception as exc:
        state.update({"status": "failed", "reason": str(exc)})
    finally:
        # Let systemd enforce its stop deadline rather than interrupting a manifest write.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            atomic_json(artifacts / "system-after.json", collect_system())
        except Exception as exc:
            state["collection_error"] = str(exc)
        state["finished"] = now()
        finalize(directory, state)
        if held_lock:
            held_lock.close()
    return 0 if state["status"] == "succeeded" else 1


def main():
    os.umask(0o077)
    action = sys.argv[1]
    if action == "worker":
        return worker(sys.argv[2])
    payload = json.loads(sys.argv[2])
    actions = {
        "probe": probe,
        "stage": stage,
        "start": start,
        "status": status,
        "ack": acknowledge,
        "cancel": cancel,
        "switch": switch,
    }
    try:
        value = actions[action](payload)
        print(json.dumps({"ok": True, "value": value}))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
