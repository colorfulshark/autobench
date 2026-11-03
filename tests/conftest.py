import io
import sys
import tarfile
from pathlib import Path

import pytest

from autobench.config import Config, inspect_archive

RUN = """import json, os, pathlib, sys
out = pathlib.Path(os.environ["UB_RESULTDIR"])
(out / "observed.json").write_text(json.dumps(dict(os.environ)))
print("benchmark finished")
"""

REMOVED_OUTPUTS = {
    "archive-manifest.json",
    "binaries-manifest.json",
    "source-metadata.json",
    "system-before.json",
    "system-after.json",
    "task.json",
    "environment.json",
    "environment-overrides.json",
    "environment-delta.json",
}


def assert_no_removed_outputs(root: Path):
    assert not [path for path in root.rglob("*") if path.name in REMOVED_OUTPUTS]


def source_archive(path: Path, run=RUN, make=None):
    make = make or "all:\n\t@echo build-ok\n\t@printenv RUNTIME_ONLY || true\n"
    with tarfile.open(path, "w:gz") as archive:
        for name, value, mode in (
            ("UnixBench/Run", f"#!{sys.executable}\n" + run, 0o755),
            ("UnixBench/Makefile", make, 0o644),
            ("UnixBench/pgms/probe", "#!/bin/sh\necho fixture-binary\n", 0o755),
            ("UnixBench/testdir/input", "fresh input\n", 0o644),
            (
                "UnixBench/src/probe.c",
                '#include <stdio.h>\nint main(void) { puts("built"); }\n',
                0o644,
            ),
        ):
            data = value.encode()
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = mode
            archive.addfile(member, io.BytesIO(data))
    return path


@pytest.fixture
def archive(tmp_path):
    return source_archive(tmp_path / "source.tar.gz")


@pytest.fixture
def config(tmp_path, archive):
    known = tmp_path / "known_hosts"
    known.touch()
    parsed = Config.model_validate(
        {
            "machines": [
                {
                    "id": "host1",
                    "ssh": {"host": "localhost", "username": "bench", "known_hosts": known},
                    "systems": [
                        {
                            "id": "os1",
                            "boot_entry": "0001",
                            "env": {"SHARED": "system", "SYSTEM_ONLY": "yes"},
                        },
                        {
                            "id": "os2",
                            "boot_entry": "0002",
                            "env": {"SHARED": "other-system"},
                        },
                    ],
                }
            ],
            "benchmarks": [
                {
                    "id": "ub",
                    "source_archive": archive,
                    "env": {"SHARED": "benchmark", "RUNTIME_ONLY": "yes"},
                }
            ],
            "policy": {
                "state_dir": tmp_path / "state",
                "output_dir": tmp_path / "output",
                "poll_interval": 0.001,
                "settle_seconds": 0,
                "reconnect_timeout": 0.1,
                "boot_timeout": 0.05,
            },
        }
    )
    inspect_archive(parsed.benchmarks[0])
    return parsed
