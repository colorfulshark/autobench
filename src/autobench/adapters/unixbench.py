import shlex

from autobench.config import Benchmark


class UnixBench:
    def requirements(self, benchmark: Benchmark) -> list[str]:
        # CC overrides are passed to make as argv entries, never evaluated by AutoBench.
        compiler = next((a[3:] for a in benchmark.make_args if a.startswith("CC=")), "gcc")
        words = shlex.split(compiler)
        if not words:
            raise ValueError("CC cannot be empty")
        return ["make", "perl", words[0]]

    def execution(self, benchmark: Benchmark) -> dict:
        return {
            "build_argv": ["make", *benchmark.make_args],
            "run_argv": ["./Run", *benchmark.args, *benchmark.tests],
            "source_subdir": benchmark.source_subdir,
            "build_timeout": benchmark.build_timeout,
            "timeout": benchmark.timeout,
            "artifact_dir": "results",
            "result_env": "UB_RESULTDIR",
            "compiler_argv": shlex.split(
                next((a[3:] for a in benchmark.make_args if a.startswith("CC=")), "gcc")
            ),
        }

    def metadata(self, benchmark: Benchmark) -> dict:
        return {
            "adapter": "unixbench",
            "source_sha256": benchmark.source_sha256,
            "make_args": benchmark.make_args,
            "args": benchmark.args,
            "tests": benchmark.tests,
            "warnings": [
                "UnixBench may overwrite LANG internally; environment.json records "
                "the launch environment. Source code is not modified."
            ],
        }
