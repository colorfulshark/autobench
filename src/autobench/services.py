"""Operations shared by the terminal and local web controllers."""

from __future__ import annotations

import re
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


async def _probe_ssh(ssh: SSHConfig, connect_timeout: float, requirements: list[str]) -> dict:
    if ssh.verify_host_key and not ssh.known_hosts.is_file():
        raise AutoBenchError("主机密钥文件不存在，请在高级设置中选择已核验的 known_hosts 文件。")
    if any(not path.is_file() for path in ssh.client_keys):
        raise AutoBenchError("私钥文件不存在，请选择服务器上的私钥文件。")
    try:
        return await SSHClient(ssh, Policy(connect_timeout=connect_timeout)).probe(requirements)
    except AuthenticationFailed as exc:
        if isinstance(exc.__cause__, asyncssh.HostKeyNotVerifiable):
            message = "主机密钥校验失败，请核对指纹并更新已信任的主机密钥文件。"
        elif isinstance(exc.__cause__, asyncssh.KeyImportError):
            message = "无法读取私钥；加密私钥可先加载到 SSH Agent。"
        else:
            message = "SSH 认证失败，请检查用户名和所选认证方式。"
        raise AutoBenchError(message) from exc
    except ConnectionLost as exc:
        raise AutoBenchError("连接失败，请检查主机地址、端口、网络和代理设置。") from exc
    except AutoBenchError as exc:
        raise AutoBenchError(
            "无法读取远端信息，请确认远端已安装 Python 3 并检查登录 Shell。"
        ) from exc


async def identify_boot_entries(ssh: SSHConfig, connect_timeout: float = 15) -> dict:
    """Read selectable UEFI entries without modifying boot state or configuration."""
    probe = await _probe_ssh(ssh, connect_timeout, ["efibootmgr"])
    entries = [
        {"id": entry.upper(), "label": item.get("label", ""), "active": bool(item.get("active"))}
        for entry, item in probe.get("efi", {}).get("entries", {}).items()
        if re.fullmatch(r"[a-fA-F0-9]{4}", entry)
    ]
    if not entries:
        message = "未识别到 UEFI 启动项，请检查 efibootmgr 与非交互 sudo 权限。"
        if probe.get("efi_error"):
            message += " " + probe["efi_error"]
        raise AutoBenchError(message)
    return {"entries": entries}


async def probe_connection(ssh: SSHConfig, connect_timeout: float = 15) -> dict:
    """Test connection and runtime requirements without creating state or changing UEFI."""
    requirements = ["python3", "systemd-run", "systemctl", "efibootmgr"]
    try:
        probe = await _probe_ssh(ssh, connect_timeout, requirements)
    except AutoBenchError as exc:
        return {"checks": [{"id": "connection", "status": "failed", "message": str(exc)}]}

    def check(id, passed, message):
        return {"id": id, "status": "passed" if passed else "failed", "message": message}

    checks = [
        check(
            "connection",
            True,
            "SSH 连接及主机密钥校验通过" if ssh.verify_host_key else "连接成功，已忽略主机密钥校验",
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
    return {"checks": checks}


def validate_config(config: Config) -> Config:
    for mi, machine in enumerate(config.machines):
        for si, system in enumerate([None, *machine.systems]):
            ssh = machine.connection(system)
            paths = [
                *([("known_hosts", None, ssh.known_hosts)] if ssh.verify_host_key else []),
                *[("client_keys", i, p) for i, p in enumerate(ssh.client_keys)],
            ]
            for field, index, path in paths:
                if not path.is_file():
                    location = ["machines", mi]
                    if system and (
                        field in system.ssh.model_fields_set
                        or (field == "known_hosts" and not machine.ssh.verify_host_key)
                    ):
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
