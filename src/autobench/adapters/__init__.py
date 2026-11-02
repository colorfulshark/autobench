from typing import Protocol

from autobench.config import Benchmark


class BenchmarkAdapter(Protocol):
    def requirements(self, benchmark: Benchmark) -> list[str]: ...
    def execution(self, benchmark: Benchmark) -> dict: ...
    def metadata(self, benchmark: Benchmark) -> dict: ...


def get_adapter(kind: str) -> BenchmarkAdapter:
    if kind == "unixbench":
        from .unixbench import UnixBench

        return UnixBench()
    raise ValueError(f"unknown benchmark adapter: {kind}")
