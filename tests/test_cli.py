import json

from typer.testing import CliRunner

from autobench.cli import app
from autobench.storage import Store

runner = CliRunner()


def test_cli_help_and_validation(config, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(config.model_dump_json())
    assert runner.invoke(app, ["--help"]).exit_code == 0
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 0, result.output
    assert "SHA256" in result.output


def test_invalid_environment_cli_exits_nonzero(config, tmp_path):
    value = config.model_dump(mode="json")
    value["benchmarks"][0]["env"]["UB_RESULTDIR"] = "/tmp/escape"
    path = tmp_path / "bad.yaml"
    path.write_text(json.dumps(value))
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 1 and "reserved" in result.output


def test_status_uses_local_snapshot_without_connecting(config):
    store = Store(config.policy.state_dir)
    run_id = store.create(config)
    store.close()
    result = runner.invoke(app, ["status", run_id, "--state-dir", str(config.policy.state_dir)])
    assert result.exit_code == 0, result.output
    value = json.loads(result.output)
    assert value["run_id"] == run_id and len(value["jobs"]) == 2


def test_unknown_run_has_clean_error(tmp_path):
    result = runner.invoke(app, ["status", "missing", "--state-dir", str(tmp_path)])
    assert result.exit_code == 1 and "unknown run" in result.output
