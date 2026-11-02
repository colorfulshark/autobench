import asyncio
import hashlib
import json
import socket
from types import SimpleNamespace

import asyncssh
import pytest
from pydantic import ValidationError

from autobench.config import SOCKS5Proxy
from autobench.errors import AuthenticationFailed, ChecksumError, ConnectionLost, RemoteError
from autobench.ssh import RemoteClient, validate_inventory


@pytest.fixture
async def socks_server():
    state = SimpleNamespace(
        requests=[],
        passwords=[],
        tasks=set(),
        writers=set(),
        auth=False,
        malformed=False,
        disconnect=False,
        reject=False,
        stall=False,
        delay=0,
        accepted=asyncio.Event(),
        closed=asyncio.Event(),
    )

    async def relay(reader, writer):
        try:
            while block := await reader.read(65536):
                writer.write(block)
                await writer.drain()
        finally:
            writer.close()

    async def handle(reader, writer):
        task = asyncio.current_task()
        state.tasks.add(task)
        state.writers.add(writer)
        upstream = None
        try:
            version, count = await reader.readexactly(2)
            assert version == 5
            methods = await reader.readexactly(count)
            state.accepted.set()
            if state.disconnect:
                return
            if state.malformed:
                writer.write(b"\x04\x00")
                await writer.drain()
                return
            if state.stall:
                await reader.read()
                return
            await asyncio.sleep(state.delay)
            method = 2 if state.auth else 0
            writer.write(bytes([5, method if method in methods else 255]))
            await writer.drain()
            if method not in methods:
                return
            if state.auth:
                version, size = await reader.readexactly(2)
                assert version == 1
                username = await reader.readexactly(size)
                size = (await reader.readexactly(1))[0]
                password = await reader.readexactly(size)
                state.passwords.append((username, password))
                valid = (username, password) == (b"proxy-user", b"proxy-secret")
                writer.write(bytes([1, 0 if valid else 1]))
                await writer.drain()
                if not valid:
                    return
            version, command, reserved, kind = await reader.readexactly(4)
            assert (version, command, reserved) == (5, 1, 0)
            if kind == 3:
                size = (await reader.readexactly(1))[0]
                host = (await reader.readexactly(size)).decode("ascii")
            else:
                size, family = (4, socket.AF_INET) if kind == 1 else (16, socket.AF_INET6)
                host = socket.inet_ntop(family, await reader.readexactly(size))
            port = int.from_bytes(await reader.readexactly(2), "big")
            state.requests.append((host, port))
            if state.reject:
                writer.write(b"\x05\x05\x00\x01" + b"\x00" * 6)
                await writer.drain()
                return
            target = "127.0.0.1" if host == "target.internal.invalid" else host
            remote_reader, upstream = await asyncio.open_connection(target, port)
            writer.write(b"\x05\x00\x00\x01" + b"\x00" * 6)
            await writer.drain()
            async with asyncio.TaskGroup() as group:
                group.create_task(relay(reader, upstream))
                group.create_task(relay(remote_reader, writer))
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            for stream in (writer, upstream):
                if stream is not None:
                    stream.close()
                    try:
                        await stream.wait_closed()
                    except OSError:
                        pass
            state.writers.discard(writer)
            state.tasks.discard(task)
            state.closed.set()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    state.port = server.sockets[0].getsockname()[1]
    try:
        yield state
    finally:
        server.close()
        await server.wait_closed()
        for writer in state.writers:
            writer.close()
        tasks = list(state.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.fixture(params=["direct", "socks5", "socks5-auth"])
async def ssh_server(tmp_path, config, socks_server, request):
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    fault = {"drop": False, "offsets": [], "passwords": [], "public_keys": 0}

    class Server(asyncssh.SSHServer):
        def begin_auth(self, username):
            return True

        def password_auth_supported(self):
            return True

        def public_key_auth_supported(self):
            return True

        def validate_password(self, username, password):
            fault["passwords"].append(password)
            return username == "bench" and password == "correct-password"

        def validate_public_key(self, username, key):
            fault["public_keys"] += 1
            return key == client_key.convert_to_public()

    class SFTP(asyncssh.SFTPServer):
        def read(self, file_obj, offset, size):
            fault["offsets"].append(offset)
            if fault["drop"] and offset >= 128 * 1024:
                fault["drop"] = False
                self.connection.abort()
                raise asyncssh.SFTPFailure("injected disconnect")
            return super().read(file_obj, offset, size)

    async def process(process):
        child = await asyncio.create_subprocess_shell(
            process.command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await child.communicate()
        process.stdout.write(stdout.decode())
        process.stderr.write(stderr.decode())
        process.exit(child.returncode)

    listener = await asyncssh.create_server(
        Server,
        "127.0.0.1",
        0,
        server_host_keys=[host_key],
        process_factory=process,
        sftp_factory=SFTP,
    )
    port = listener.get_port()
    known = tmp_path / "server_known_hosts"
    known.write_text(f"[127.0.0.1]:{port} " + host_key.export_public_key().decode())
    private = tmp_path / "client_key"
    private.write_bytes(client_key.export_private_key())
    private.chmod(0o600)
    machine = config.machines[0].model_copy(deep=True)
    machine.ssh.host, machine.ssh.port = "127.0.0.1", port
    machine.ssh.known_hosts, machine.ssh.agent = known, False
    machine.ssh.client_keys = [private]
    if request.param != "direct":
        machine.ssh.proxy = SOCKS5Proxy(host="127.0.0.1", port=socks_server.port)
        if request.param == "socks5-auth":
            socks_server.auth = True
            machine.ssh.proxy.username = "proxy-user"
            machine.ssh.proxy.password = "proxy-secret"
    client = RemoteClient(machine, None, config.policy)
    try:
        yield client, fault
    finally:
        listener.close()
        await listener.wait_closed()


async def test_public_key_auth_and_real_remote_helper(ssh_server):
    client, fault = ssh_server
    probe = await client.probe()
    assert probe["boot_id"] and probe["python"] >= [3, 8]
    assert fault["public_keys"] > 0 and fault["passwords"] == []


async def test_password_authentication(ssh_server):
    client, fault = ssh_server
    client.ssh.client_keys = []
    client.ssh.password = "correct-password"
    assert (await client.probe())["machine_id"]
    assert fault["passwords"] == ["correct-password"]


async def test_bad_password_and_host_key_rejected(ssh_server):
    client, fault = ssh_server
    client.ssh.client_keys = []
    client.ssh.password = "wrong-password"
    with pytest.raises(AuthenticationFailed) as failure:
        await client.probe()
    assert "wrong-password" not in str(failure.value)
    assert "wrong-password" in fault["passwords"]
    replacement = asyncssh.generate_private_key("ssh-ed25519")
    client.ssh.known_hosts.write_text(
        f"[127.0.0.1]:{client.ssh.port} " + replacement.export_public_key().decode()
    )
    with pytest.raises(AuthenticationFailed):
        await client.probe()


async def test_remote_payload_is_shell_literal(ssh_server, tmp_path):
    client, _ = ssh_server
    marker = tmp_path / "must-not-exist"
    strange = tmp_path / ("dir-$(touch " + str(marker).replace("/", "_") + ")-`id`-$HOME")
    job_id = "b" * 32
    directory = strange / "jobs" / job_id
    source = tmp_path / "source"
    source.write_bytes(b"source")
    manifest = {
        "value": "$(touch %s)\n'\"$HOME`id`" % marker,
        "source_path": str(source),
        "source_sha256": hashlib.sha256(b"source").hexdigest(),
    }
    await client.call("stage", {"job_id": job_id, "job_dir": str(directory), "manifest": manifest})
    assert json.loads((directory / "manifest.json").read_text()) == manifest
    assert not marker.exists()


def inventory_for(path):
    data = path.read_bytes()
    return {
        "schema_version": 1,
        "files": [
            {"path": path.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        ],
    }


async def test_real_sftp_disconnect_resume_and_atomic_commit(ssh_server, tmp_path):
    client, fault = ssh_server
    remote = tmp_path / "remote-job"
    (remote / "artifacts").mkdir(parents=True)
    data = b"0123456789abcdef" * 150000
    source = remote / "artifacts/data.bin"
    source.write_bytes(data)
    destination = tmp_path / "downloaded"
    partial = tmp_path / "downloaded.partial"
    partial.mkdir()
    (partial / "data.bin").write_bytes(data[:65536])
    fault["drop"] = True
    inventory = inventory_for(source)
    with pytest.raises(ConnectionLost):
        await client.download(str(remote), inventory, destination)
    assert not destination.exists()
    await client.download(str(remote), inventory, destination)
    assert (destination / "data.bin").read_bytes() == data
    assert 65536 in fault["offsets"]
    assert (destination / "archive-manifest.json").exists()
    assert not partial.exists()


async def test_corrupt_partial_restarts_only_bad_file(ssh_server, tmp_path):
    client, _ = ssh_server
    remote = tmp_path / "remote"
    (remote / "artifacts").mkdir(parents=True)
    source = remote / "artifacts/result.txt"
    source.write_bytes(b"actual-result")
    partial = tmp_path / "result.partial"
    partial.mkdir()
    (partial / "result.txt").write_bytes(b"wrong")
    destination = tmp_path / "result"
    inventory = inventory_for(source)
    with pytest.raises(ChecksumError):
        await client.download(str(remote), inventory, destination)
    assert not (partial / "result.txt").exists()
    await client.download(str(remote), inventory, destination)
    assert (destination / "result.txt").read_bytes() == b"actual-result"


async def test_atomic_source_upload(ssh_server, tmp_path):
    client, _ = ssh_server
    source = tmp_path / "upload-source"
    source.write_bytes(b"archive-content")
    destination = tmp_path / "upload-target" / "archive.tar"
    await client.upload(source, str(destination))
    assert destination.read_bytes() == source.read_bytes()
    assert not list(destination.parent.glob("*.upload-*"))


@pytest.mark.parametrize(
    "name", ["../escape", "/etc/passwd", "a/../../escape", "archive-manifest.json"]
)
def test_inventory_traversal_rejected(name):
    with pytest.raises(RemoteError):
        validate_inventory(
            {"schema_version": 1, "files": [{"path": name, "size": 0, "sha256": "0" * 64}]}
        )


@pytest.fixture
async def proxied_client(ssh_server, socks_server):
    client, fault = ssh_server
    socks_server.auth = False
    client.ssh.proxy = SOCKS5Proxy(host="127.0.0.1", port=socks_server.port)
    client.ssh.host = "target.internal.invalid"
    client.ssh.known_hosts.write_text(
        client.ssh.known_hosts.read_text().replace("127.0.0.1", client.ssh.host)
    )
    return client, fault


async def test_proxy_resolves_target_and_checks_target_key(proxied_client, socks_server):
    client, _ = proxied_client
    assert (await client.probe())["machine_id"]
    assert socks_server.requests == [(client.ssh.host, client.ssh.port)]
    replacement = asyncssh.generate_private_key("ssh-ed25519")
    client.ssh.known_hosts.write_text(
        f"[{client.ssh.host}]:{client.ssh.port} " + replacement.export_public_key().decode()
    )
    with pytest.raises(AuthenticationFailed):
        await client.probe()


async def test_proxy_checks_host_key_alias(proxied_client):
    client, _ = proxied_client
    client.ssh.host_key_alias = "bench-alias"
    client.ssh.known_hosts.write_text(
        client.ssh.known_hosts.read_text().replace(client.ssh.host, "bench-alias")
    )
    assert (await client.probe())["machine_id"]


async def test_proxy_address_is_not_target_identity(proxied_client):
    client, _ = proxied_client
    # This valid server key is only trusted for the proxy IP, not the target hostname.
    client.ssh.known_hosts.write_text(
        client.ssh.known_hosts.read_text().replace(client.ssh.host, "127.0.0.1")
    )
    with pytest.raises(AuthenticationFailed):
        await client.probe()


async def test_proxy_rejects_without_direct_fallback(ssh_server, socks_server):
    client, _ = ssh_server
    client.ssh.proxy = SOCKS5Proxy(host="127.0.0.1", port=socks_server.port)
    socks_server.auth = False
    socks_server.reject = True
    with pytest.raises(ConnectionLost, match="SOCKS5 proxy"):
        await client.probe()
    assert socks_server.requests == [(client.ssh.host, client.ssh.port)]


async def test_proxy_bad_password_is_sanitized(proxied_client, socks_server):
    client, _ = proxied_client
    socks_server.auth = True
    client.ssh.proxy.username = "proxy-user"
    client.ssh.proxy.password = "wrong-proxy-password"
    with pytest.raises(ConnectionLost, match="SOCKS5 proxy") as failure:
        await client.probe()
    assert "wrong-proxy-password" not in str(failure.value)
    assert "wrong-proxy-password" not in repr(client.ssh)
    assert socks_server.requests == []


@pytest.mark.parametrize("password", [None, "", "x" * 256, "秘密"])
async def test_proxy_invalid_password_fails_before_connecting(
    proxied_client, socks_server, password
):
    client, _ = proxied_client
    with pytest.raises(ValidationError, match="SOCKS5 proxy"):
        client.ssh.proxy = SOCKS5Proxy(
            host="127.0.0.1", port=socks_server.port, username="proxy-user", password=password
        )
    assert not socks_server.accepted.is_set()


async def test_proxy_password_can_be_updated(proxied_client, socks_server):
    client, _ = proxied_client
    socks_server.auth = True
    client.ssh.proxy.username = "proxy-user"
    client.ssh.proxy.password = "wrong-proxy-password"
    with pytest.raises(ConnectionLost):
        await client.probe()
    client.ssh.proxy.password = "proxy-secret"
    assert (await client.probe())["machine_id"]
    assert socks_server.passwords[-1] == (b"proxy-user", b"proxy-secret")


@pytest.mark.parametrize("cancel", [False, True])
async def test_proxy_handshake_timeout_and_cancel_close_socket(
    proxied_client, socks_server, cancel
):
    client, _ = proxied_client
    socks_server.stall = True
    client.policy = client.policy.model_copy(update={"connect_timeout": 0.1})
    task = asyncio.create_task(client.probe())
    await asyncio.wait_for(socks_server.accepted.wait(), 2)
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(ConnectionLost, match="SOCKS5 proxy"):
            await task
    await asyncio.wait_for(socks_server.closed.wait(), 2)
    assert not socks_server.writers


@pytest.mark.parametrize("cancel", [False, True])
async def test_proxy_and_ssh_share_timeout_and_close_socket(
    proxied_client, socks_server, monkeypatch, cancel
):
    client, _ = proxied_client
    socks_server.delay = 0.2
    client.policy = client.policy.model_copy(update={"connect_timeout": 0.4})
    started = asyncio.Event()
    sockets = []

    async def stalled_connect(**kwargs):
        sockets.append(kwargs["sock"])
        started.set()
        await asyncio.sleep(0.3)
        pytest.fail("SSH handshake exceeded the shared connection budget")

    monkeypatch.setattr(asyncssh, "connect", stalled_connect)
    task = asyncio.create_task(client.probe())
    await asyncio.wait_for(started.wait(), 2)
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(ConnectionLost, match="SSH connection through SOCKS5 proxy"):
            await task
    assert sockets[0].fileno() == -1
    await asyncio.wait_for(socks_server.closed.wait(), 2)


async def test_connection_timeout_does_not_limit_operations(proxied_client):
    client, _ = proxied_client
    client.policy = client.policy.model_copy(update={"connect_timeout": 0.2})
    async with client.connection() as connection:
        await asyncio.sleep(0.25)
        result = await connection.run("printf still-connected", check=True)
        assert result.stdout == "still-connected"


async def test_proxy_unavailable_does_not_fall_back(ssh_server, socks_server):
    client, _ = ssh_server
    # A live direct endpoint must remain unused when its configured proxy is unavailable.
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    client.ssh.proxy = SOCKS5Proxy(host="127.0.0.1", port=port)
    with pytest.raises(ConnectionLost, match="SOCKS5 proxy"):
        await client.probe()


@pytest.mark.parametrize("failure", ["malformed", "disconnect"])
async def test_proxy_negotiation_failure_closes_socket(proxied_client, socks_server, failure):
    client, _ = proxied_client
    setattr(socks_server, failure, True)
    with pytest.raises(ConnectionLost, match="SOCKS5 proxy"):
        await client.probe()
    await asyncio.wait_for(socks_server.closed.wait(), 2)
    assert not socks_server.writers
