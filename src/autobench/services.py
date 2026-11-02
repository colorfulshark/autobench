"""Operations shared by the terminal and local web controllers."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import asyncssh

from autobench.config import Config, Policy, SSHConfig
from autobench.errors import (
    AuthenticationFailed,
    AutoBenchError,
    ConfigurationFieldError,
    ConnectionLost,
)
from autobench.scheduler import Scheduler, inspect_machine
from autobench.ssh import SSHClient
from autobench.storage import Store


async def probe_connection(ssh: SSHConfig, connect_timeout: float = 15) -> dict:
    """Read the current OS without creating state, staging jobs or changing UEFI."""

    def failed(code, message, field):
        return {
            "probe": None,
            "checks": [
                {
                    "id": "connection",
                    "status": "failed",
                    "code": code,
                    "message": message,
                    "path": ["ssh", field],
                }
            ],
        }

    if not ssh.known_hosts.is_file():
        return failed(
            "known_hosts_missing",
            "主机密钥文件不存在，请在高级设置中选择已核验的 known_hosts 文件。",
            "known_hosts",
        )
    if any(not path.is_file() for path in ssh.client_keys):
        return failed("key_missing", "私钥文件不存在，请选择服务器上的私钥文件。", "client_keys")
    requirements = ["python3", "systemd-run", "systemctl", "efibootmgr"]
    try:
        probe = await SSHClient(ssh, Policy(connect_timeout=connect_timeout)).probe(requirements)
    except AuthenticationFailed as exc:
        if isinstance(exc.__cause__, asyncssh.HostKeyNotVerifiable):
            return failed(
                "host_key",
                "主机密钥校验失败，请核对指纹并更新已信任的主机密钥文件。",
                "known_hosts",
            )
        if isinstance(exc.__cause__, asyncssh.KeyImportError):
            return failed(
                "private_key", "无法读取私钥；加密私钥可先加载到 SSH Agent。", "client_keys"
            )
        return failed("authentication", "SSH 认证失败，请检查用户名和所选认证方式。", "password")
    except ConnectionLost:
        return failed("connection", "连接失败，请检查主机地址、端口、网络和代理设置。", "host")
    except AutoBenchError:
        return failed(
            "remote_helper",
            "无法读取远端信息，请确认远端已安装 Python 3 并检查登录 Shell。",
            "host",
        )

    def check(id, passed, message):
        return {"id": id, "status": "passed" if passed else "failed", "message": message}

    identity = probe.get("machine_id", "")
    identity_ok = len(identity) == 32 and all(c in "0123456789abcdefABCDEF" for c in identity)
    checks = [
        check("connection", True, "SSH 连接及主机密钥校验通过"),
        check(
            "identity",
            identity_ok,
            "当前系统身份已读取" if identity_ok else "当前系统 Machine ID 不合法",
        ),
        check(
            "uefi",
            bool(probe.get("efi", {}).get("current")),
            "已读取 UEFI 启动项"
            if probe.get("efi", {}).get("current")
            else "无法读取 UEFI，请检查 efibootmgr 与非交互 sudo 权限",
        ),
        check(
            "runtime",
            bool(probe.get("systemd_available"))
            and probe.get("systemd_version", 0) >= 240
            and tuple(probe.get("python", [])) >= (3, 8),
            "运行环境要求 Python ≥3.8、systemd ≥240 和非交互管理权限",
        ),
        check(
            "dependencies",
            all(probe.get("commands", {}).get(name) for name in requirements),
            "基础命令：Python、systemd-run、systemctl、efibootmgr",
        ),
        check(
            "disk", probe.get("disk_free", 0) >= 256 * 1024 * 1024, "远端可用空间需不少于 256 MiB"
        ),
    ]
    return {"probe": probe, "checks": checks}


def validate_config(config: Config) -> Config:
    for mi, machine in enumerate(config.machines):
        for si, system in enumerate([None, *machine.systems]):
            ssh = machine.connection(system)
            paths = [
                ("known_hosts", None, ssh.known_hosts),
                *[("client_keys", i, p) for i, p in enumerate(ssh.client_keys)],
            ]
            for field, index, path in paths:
                if not path.is_file():
                    location = ["machines", mi]
                    if system and field in system.ssh.model_fields_set:
                        location += ["systems", si - 1]
                    location += ["ssh", field]
                    if index is not None:
                        location.append(index)
                    raise ConfigurationFieldError(
                        f"local SSH file does not exist: {path}", location, "file_missing"
                    )
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
