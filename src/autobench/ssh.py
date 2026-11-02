from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from uuid import uuid4

import asyncssh
from python_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError, ProxyType
from python_socks.async_.asyncio import Proxy

from autobench import remote_runner
from autobench.config import Machine, Policy, SSHConfig, System, file_sha256
from autobench.errors import AuthenticationFailed, ChecksumError, ConnectionLost, RemoteError
from autobench.storage import private_dir


class SSHClient:
    """Authenticated, verified SSH RPCs independent of a benchmark configuration."""

    def __init__(self, ssh: SSHConfig, policy: Policy, label: str = "connection"):
        self.policy = policy
        self.ssh = ssh
        self.label = label
        self.source = Path(remote_runner.__file__).read_text()

    @asynccontextmanager
    async def connection(self):
        if not self.ssh.known_hosts.is_file():
            raise AuthenticationFailed(f"known_hosts file does not exist: {self.ssh.known_hosts}")
        kwargs = {
            "host": self.ssh.host,
            "port": self.ssh.port,
            "username": self.ssh.username,
            "password": self.ssh.password,
            "known_hosts": str(self.ssh.known_hosts),
            "config": None,
            "client_keys": [str(p) for p in self.ssh.client_keys],
            "agent_path": () if self.ssh.agent else None,
            "connect_timeout": self.policy.connect_timeout,
            "login_timeout": self.policy.connect_timeout,
            "keepalive_interval": 10,
            "keepalive_count_max": 3,
        }
        if self.ssh.host_key_alias:
            kwargs["host_key_alias"] = self.ssh.host_key_alias
        sock = None
        stage = "SSH connection"
        try:
            async with asyncio.timeout(self.policy.connect_timeout):
                if self.ssh.proxy is not None:
                    stage = "SOCKS5 proxy connection"
                    proxy = self.ssh.proxy
                    # The socket peer is the proxy, never a trusted identity for the target.
                    kwargs["known_hosts"] = asyncssh.read_known_hosts(
                        str(self.ssh.known_hosts)
                    ).match(
                        self.ssh.host_key_alias or self.ssh.host,
                        "",
                        self.ssh.port if self.ssh.port != 22 else None,
                    )
                    sock = await Proxy(
                        proxy_type=ProxyType.SOCKS5,
                        host=proxy.host,
                        port=proxy.port,
                        username=proxy.username,
                        password=proxy.password,
                        rdns=True,
                    ).connect(
                        dest_host=self.ssh.host,
                        dest_port=self.ssh.port,
                        timeout=self.policy.connect_timeout,
                    )
                    kwargs["sock"] = sock
                    stage = "SSH connection through SOCKS5 proxy"
                connection = await asyncssh.connect(**kwargs)
            async with connection:
                yield connection
        except (
            asyncssh.PermissionDenied,
            asyncssh.HostKeyNotVerifiable,
            asyncssh.KeyImportError,
        ) as exc:
            raise AuthenticationFailed(
                f"SSH authentication/host-key check failed for {self.label}"
            ) from exc
        except asyncssh.SFTPError as exc:
            if isinstance(
                exc, (asyncssh.SFTPConnectionLost, asyncssh.SFTPNoConnection, asyncssh.SFTPEOF)
            ):
                raise ConnectionLost(f"SFTP connection lost for {self.label}") from exc
            raise RemoteError(f"SFTP operation failed for {self.label}: {exc}") from exc
        except (ProxyError, ProxyConnectionError, ProxyTimeoutError) as exc:
            raise ConnectionLost(f"SOCKS5 proxy connection failed for {self.label}") from exc
        except (asyncssh.Error, OSError, asyncio.TimeoutError) as exc:
            raise ConnectionLost(f"{stage} unavailable for {self.label}") from exc
        finally:
            if sock is not None:
                sock.close()

    async def call(self, action: str, payload: dict | None = None, timeout: float = 60) -> dict:
        # shlex quotes the complete Python program and JSON as data, including dollars/backticks.
        argv = ["python3", "-c", self.source, action, json.dumps(payload or {})]
        async with self.connection() as connection:
            result = await connection.run(shlex.join(argv), check=False, timeout=timeout)
        try:
            reply = json.loads(result.stdout)
        except (ValueError, TypeError) as exc:
            raise RemoteError(
                "remote helper did not return JSON; check Python and login shell output"
            ) from exc
        if not reply.get("ok"):
            raise RemoteError(reply.get("error", "remote helper failed"))
        return reply["value"]

    async def probe(self, requirements: list[str] | None = None) -> dict:
        return await self.call("probe", {"requirements": requirements or []})


class RemoteClient(SSHClient):
    def __init__(self, machine: Machine, system: System | None, policy: Policy):
        self.machine = machine
        self.system = system
        super().__init__(machine.connection(system), policy, machine.id)

    def root(self, probe: dict) -> str:
        configured = self.machine.remote_dir
        if configured == "~" or configured.startswith("~/"):
            configured = str(PurePosixPath(probe["home"]) / configured[2:])
        root = PurePosixPath(configured)
        if not root.is_absolute():
            root = PurePosixPath(probe["home"]) / root
        if ".." in root.parts or str(root) == "/":
            raise RemoteError("remote_dir must be a dedicated directory without '..'")
        return str(root)

    @staticmethod
    def payload(job_id: str, directory: str) -> dict:
        return {"job_id": job_id, "job_dir": directory}

    async def upload(self, local: Path, remote: str):
        """Atomic content-addressed upload. Incomplete transfers are never executed."""
        async with self.connection() as connection:
            async with connection.start_sftp_client() as sftp:
                parent = str(PurePosixPath(remote).parent)
                await sftp.makedirs(parent, exist_ok=True)
                await sftp.chmod(parent, 0o700)
                temp = remote + ".upload-" + uuid4().hex
                try:
                    await sftp.put(str(local), temp)
                    await sftp.chmod(temp, 0o600)
                    await sftp.posix_rename(temp, remote)
                except BaseException:
                    try:
                        await sftp.remove(temp)
                    except (asyncssh.SFTPError, OSError):
                        pass
                    raise

    async def stage(self, job_id: str, directory: str, manifest: dict, source: Path):
        # The local source may have changed after the run snapshot was created.
        if await asyncio.to_thread(file_sha256, source) != manifest["source_sha256"]:
            raise RemoteError("local source no longer matches the frozen source checksum")
        payload = self.payload(job_id, directory)
        staged = await self.call("stage", {**payload, "manifest": manifest})
        if not staged["source_valid"]:
            await self.upload(source, manifest["source_path"])
        # No worker can start until this final upload has completed atomically.
        await self.upload(Path(remote_runner.__file__), str(PurePosixPath(directory) / "runner.py"))

    async def start(self, job_id: str, directory: str):
        return await self.call("start", self.payload(job_id, directory))

    async def status(self, job_id: str, directory: str):
        return await self.call("status", self.payload(job_id, directory))

    async def cancel(self, job_id: str, directory: str):
        return await self.call("cancel", self.payload(job_id, directory), timeout=60)

    async def acknowledge(self, job_id: str, directory: str):
        return await self.call("ack", self.payload(job_id, directory))

    async def switch(self, root: str, previous_boot_id: str, boot_entry: str, token: str):
        return await self.call(
            "switch",
            {
                "root": root,
                "previous_boot_id": previous_boot_id,
                "boot_entry": boot_entry,
                "token": token,
            },
        )

    async def download(self, directory: str, inventory: dict, destination: Path):
        """Resume per-file offsets, verify every file, then commit the directory."""
        files = validate_inventory(inventory)
        private_dir(destination.parent)
        if destination.exists():
            await asyncio.to_thread(verify_download, destination, files)
            return
        partial = private_dir(destination.with_name(destination.name + ".partial"))
        async with self.connection() as connection:
            async with connection.start_sftp_client() as sftp:
                for item in files:
                    target = partial / item["path"]
                    if (
                        not target.resolve().is_relative_to(partial.resolve())
                        or target.is_symlink()
                    ):
                        raise RemoteError("unsafe local artifact path")
                    private_dir(target.parent)
                    size = target.stat().st_size if target.exists() else 0
                    if size > item["size"]:
                        target.unlink()
                        size = 0
                    remote_path = str(PurePosixPath(directory) / "artifacts" / item["path"])
                    async with sftp.open(remote_path, "rb") as incoming:
                        await incoming.seek(size)
                        with target.open("ab") as outgoing:
                            target.chmod(0o600)
                            while size < item["size"]:
                                block = await incoming.read(min(1024 * 1024, item["size"] - size))
                                if not block:
                                    raise ConnectionLost(
                                        "remote artifact ended before its declared size"
                                    )
                                outgoing.write(block)
                                size += len(block)
                            outgoing.flush()
                            os.fsync(outgoing.fileno())
                    if await asyncio.to_thread(file_sha256, target) != item["sha256"]:
                        target.unlink()  # Next attempt starts this file from zero.
                        raise ChecksumError(f"artifact checksum mismatch: {item['path']}")
        await asyncio.to_thread(verify_download, partial, files)
        manifest = partial / "archive-manifest.json"
        manifest.write_text(json.dumps(inventory, ensure_ascii=False, indent=2))
        manifest.chmod(0o600)
        with manifest.open("rb") as stream:
            os.fsync(stream.fileno())
        for directory_path, _, _ in os.walk(partial, topdown=False):
            fd = os.open(directory_path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        partial.replace(destination)
        fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def validate_inventory(inventory: dict) -> list[dict]:
    if inventory.get("schema_version") != 1 or not isinstance(inventory.get("files"), list):
        raise RemoteError("unsupported artifact inventory")
    seen = set()
    for item in inventory["files"]:
        path = PurePosixPath(item["path"])
        if (
            not path.parts
            or path.is_absolute()
            or ".." in path.parts
            or str(path) in seen
            or str(path) == "archive-manifest.json"
            or not isinstance(item["size"], int)
            or item["size"] < 0
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        ):
            raise RemoteError("unsafe artifact inventory")
        seen.add(str(path))
    return inventory["files"]


def verify_download(root: Path, files: list[dict]):
    for item in files:
        path = root / item["path"]
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())
            or not path.is_file()
            or path.stat().st_size != item["size"]
            or file_sha256(path) != item["sha256"]
        ):
            raise ChecksumError(f"local artifact verification failed: {item['path']}")
