from __future__ import annotations

import hashlib
import re
import tarfile
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_serializer,
    model_validator,
)

ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
IDENTIFIER = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
Environment = dict[StrictStr, StrictStr]
PositiveSeconds = Annotated[float, Field(gt=0)]


def validate_env(value: Environment) -> Environment:
    for name, item in value.items():
        if not ENV_NAME.fullmatch(name) or "\x00" in item:
            raise ValueError(f"invalid environment entry: {name!r}")
        if name == "UB_RESULTDIR":
            raise ValueError("UB_RESULTDIR is reserved for AutoBench result isolation")
    return value


def merged_env(base: dict[str, str], system: Environment, benchmark: Environment) -> dict[str, str]:
    return {**base, **system, **benchmark}


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class SOCKS5Proxy(Model):
    host: str
    port: Annotated[int, Field(ge=1, le=65535)] = 1080
    username: str | None = None
    password: StrictStr | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def check_proxy(self):
        if not self.host.strip() or "\x00" in self.host:
            raise ValueError("SOCKS5 proxy host cannot be empty or contain NUL")
        if (self.username is None) != (self.password is None):
            raise ValueError("SOCKS5 proxy requires both username and password")
        if self.username is not None and (
            not self.username.isascii() or not 1 <= len(self.username) <= 255
        ):
            raise ValueError("SOCKS5 proxy username must contain 1–255 ASCII characters")
        if self.password is not None and (
            not self.password.isascii() or not 1 <= len(self.password) <= 255
        ):
            raise ValueError("SOCKS5 proxy password must contain 1–255 ASCII characters")
        return self


class SSHOverride(Model):
    host: str | None = None
    port: Annotated[int, Field(ge=1, le=65535)] | None = None
    username: str | None = None
    password: StrictStr | None = Field(default=None, repr=False)
    client_keys: list[Path] | None = None
    agent: bool | None = None
    known_hosts: Path | None = None
    host_key_alias: str | None = None
    proxy: SOCKS5Proxy | None = None

    @model_serializer(mode="wrap")
    def serialize_overrides(self, handler):
        # Preserve the distinction between omitted fields and explicit null clears
        # when a frozen configuration is round-tripped through JSON/SQLite.
        return {key: value for key, value in handler(self).items() if key in self.model_fields_set}

    @model_validator(mode="after")
    def nonnullable_overrides(self):
        for name in self.model_fields_set - {"password", "host_key_alias", "proxy"}:
            if getattr(self, name) is None:
                raise ValueError(f"SSH override {name} cannot be null")
        return self


class SSHConfig(Model):
    host: str
    port: Annotated[int, Field(ge=1, le=65535)] = 22
    username: str
    password: StrictStr | None = Field(default=None, repr=False)
    client_keys: list[Path] = Field(default_factory=list)
    agent: bool = True
    known_hosts: Path = Path("~/.ssh/known_hosts")
    host_key_alias: str | None = None
    proxy: SOCKS5Proxy | None = None

    @model_validator(mode="after")
    def check_auth(self):
        if not self.host or not self.username:
            raise ValueError("SSH host and username cannot be empty")
        return self


class System(Model):
    id: str
    boot_entry: StrictStr
    machine_id: StrictStr
    ssh: SSHOverride = Field(default_factory=SSHOverride)
    env: Environment = Field(default_factory=dict)

    _env = field_validator("env")(validate_env)

    @field_validator("boot_entry")
    @classmethod
    def boot_number(cls, value):
        if not re.fullmatch(r"[0-9a-fA-F]{4}", value):
            raise ValueError(
                'boot_entry must be a quoted four-digit hexadecimal string, e.g. "0001"'
            )
        return value.upper()

    @field_validator("machine_id")
    @classmethod
    def machine_identity(cls, value):
        if not re.fullmatch(r"[0-9a-fA-F]{32}", value):
            raise ValueError("machine_id must contain 32 hexadecimal characters")
        return value.lower()


class Machine(Model):
    id: str
    ssh: SSHConfig
    systems: Annotated[list[System], Field(min_length=1)]
    remote_dir: str = ".local/state/autobench"

    @model_validator(mode="after")
    def unique_systems(self):
        for field in ("id", "boot_entry", "machine_id"):
            values = [getattr(item, field) for item in self.systems]
            if len(set(values)) != len(values):
                raise ValueError(f"duplicate system {field} in machine {self.id}")
        if not self.remote_dir or "\x00" in self.remote_dir:
            raise ValueError("remote_dir cannot be empty or contain NUL")
        for system in self.systems:
            self.connection(system)  # Validate the merged authentication configuration.
        return self

    def connection(self, system: System | None = None) -> SSHConfig:
        overrides = system.ssh.model_dump(exclude_unset=True) if system else {}
        return SSHConfig.model_validate({**self.ssh.model_dump(), **overrides})


class Benchmark(Model):
    id: str
    type: Literal["unixbench"] = "unixbench"
    source_archive: Path
    source_subdir: str | None = None
    source_sha256: str | None = None
    make_args: list[StrictStr] = Field(default_factory=list)
    args: list[StrictStr] = Field(default_factory=list)
    env: Environment = Field(default_factory=dict)
    repetitions: Annotated[int, Field(ge=1)] = 1
    build_timeout: PositiveSeconds = 1800
    timeout: PositiveSeconds = 21600

    _env = field_validator("env")(validate_env)

    @field_validator("args", "make_args")
    @classmethod
    def safe_args(cls, value):
        if any("\x00" in item for item in value):
            raise ValueError("command arguments cannot contain NUL")
        return value

    @field_validator("source_sha256")
    @classmethod
    def digest(cls, value):
        if value is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", value):
            raise ValueError("source_sha256 must be a SHA-256 hexadecimal digest")
        return value.lower() if value else value


class Policy(Model):
    parallel_machines: Annotated[int, Field(ge=1)] = 4
    poll_interval: PositiveSeconds = 10
    settle_seconds: Annotated[float, Field(ge=0)] = 60
    reconnect_timeout: PositiveSeconds = 900
    boot_timeout: PositiveSeconds = 600
    connect_timeout: PositiveSeconds = 15
    state_dir: Path = Path("~/.local/state/autobench")
    output_dir: Path = Path("results")


class Config(Model):
    version: Literal[1] = 1
    machines: Annotated[list[Machine], Field(min_length=1)]
    benchmarks: Annotated[list[Benchmark], Field(min_length=1)]
    policy: Policy = Field(default_factory=Policy)

    @model_validator(mode="after")
    def identifiers(self):
        for items in (self.machines, self.benchmarks, *(m.systems for m in self.machines)):
            names = [item.id for item in items]
            if any(not IDENTIFIER.fullmatch(name) for name in names):
                raise ValueError("IDs must be 1–64 letters, digits, underscores or hyphens")
            if len(names) != len(set(names)):
                raise ValueError("IDs must be unique within their collection")
        return self


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inspect_archive(benchmark: Benchmark) -> None:
    """Freeze the digest and resolve one unambiguous UnixBench source directory."""
    digest = file_sha256(benchmark.source_archive)
    if benchmark.source_sha256 and benchmark.source_sha256 != digest:
        raise ValueError(f"source checksum mismatch: {benchmark.id}")
    with tarfile.open(benchmark.source_archive, "r:*") as archive:
        names = set()
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isdir() or member.isfile()):
                raise ValueError(f"unsafe archive member: {member.name}")
            if member.isfile():
                normalized = str(path)
                if normalized in names:
                    raise ValueError(f"duplicate archive member: {member.name}")
                names.add(normalized)
        candidates = sorted(
            str(PurePosixPath(n).parent)
            for n in names
            if PurePosixPath(n).name == "Run" and str(PurePosixPath(n).parent / "Makefile") in names
        )
    if benchmark.source_subdir is not None:
        subdir = str(PurePosixPath(benchmark.source_subdir))
        if subdir not in candidates:
            raise ValueError(f"source_subdir must contain Run and Makefile: {benchmark.id}")
    elif len(candidates) == 1:
        subdir = candidates[0]
    else:
        raise ValueError(f"set source_subdir to a unique UnixBench directory: {benchmark.id}")
    benchmark.source_subdir = subdir
    benchmark.source_sha256 = digest


def load_config(path: Path, *, check_sources: bool = True) -> Config:
    path = path.expanduser().resolve()
    config = Config.model_validate(yaml.safe_load(path.read_text()))

    def local(value: Path) -> Path:
        expanded = value.expanduser()
        return (
            (path.parent / expanded).resolve() if not expanded.is_absolute() else expanded.resolve()
        )

    config.policy.state_dir = local(config.policy.state_dir)
    config.policy.output_dir = local(config.policy.output_dir)
    for machine in config.machines:
        machine.ssh.known_hosts = local(machine.ssh.known_hosts)
        machine.ssh.client_keys = [local(p) for p in machine.ssh.client_keys]
        for system in machine.systems:
            if system.ssh.known_hosts is not None:
                system.ssh.known_hosts = local(system.ssh.known_hosts)
            if system.ssh.client_keys is not None:
                system.ssh.client_keys = [local(p) for p in system.ssh.client_keys]
    for benchmark in config.benchmarks:
        benchmark.source_archive = local(benchmark.source_archive)
        if check_sources:
            inspect_archive(benchmark)
    return config
