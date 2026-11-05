import io
import shutil

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from autobench.artifacts import UnsafeArtifactPath
from autobench.reports import (
    REPORT_TEMPLATE,
    TEST_ORDER,
    ConflictingResults,
    build_report,
    parse_unixbench,
    read_round,
    report_workbook,
)
from autobench.storage import Store
from autobench.web import create_app


def unixbench_text(value=100, total=10, copies=1, *, partial=False, extra=""):
    header = "Partial Index" if partial else "Index Values"
    suffix = " (Partial Only)" if partial else ""
    return (
        f"64 CPUs in system; running {copies} parallel {'copy' if copies == 1 else 'copies'} of tests\n"
        # Raw RESULT values must never be mistaken for INDEX values.
        f"{TEST_ORDER[0]}     999999.0 lps (10.0 s, 7 samples)\n"
        f"System Benchmarks {header}     BASELINE     RESULT     INDEX\n"
        f"{TEST_ORDER[0]}     116700.0     999999.0     {value}\n"
        f"{TEST_ORDER[1]}     55.0     550.0     {value * 2}\n"
        f"{extra}"
        "                                                                  ========\n"
        f"System Benchmarks Index Score{suffix}     {total}\n"
    )


def write_round(
    root, system, repetition, text, *, machine="host1", filename="report", benchmark="ub"
):
    directory = root / machine / system / benchmark / f"round-{repetition:03d}"
    (directory / "results").mkdir(parents=True, exist_ok=True)
    (directory / "results" / filename).write_text(text)
    return directory


@pytest.fixture
def report_run(config):
    config.benchmarks[0].repetitions = 3
    store = Store(config.policy.state_dir)
    run_id = store.create(config)
    root = config.policy.output_dir / run_id
    for index, value in enumerate((100, 200, 900), 1):
        text = unixbench_text(value, value / 10)
        if index != 2:
            text += unixbench_text(value * 10, value, 64)
        directory = write_round(root, "os1", index, text)
        (directory / "run.stdout").write_text(text)
        # Identical reports are duplicate sources of one observation.
        (directory / "results" / "duplicate.txt").write_text(text)
    for index in (1, 2):
        write_round(root, "os2", index, unixbench_text(480, 48) + unixbench_text(4800, 480, 64))
    for job in store.jobs(run_id):
        store.update(job["id"], "succeeded" if job["system"] == "os1" else "failed")
    try:
        yield store, run_id, root
    finally:
        store.close()


def test_parser_uses_index_and_separates_parallel_counts():
    value = parse_unixbench(unixbench_text(123.4, 42) + unixbench_text(987.6, 84, 8))
    assert set(value) == {1, 8}
    assert value[1]["tests"][TEST_ORDER[0]] == 123.4
    assert value[8]["total"] == 84
    assert not value[1]["partial"]


def test_parser_partial_whitespace_unknown_and_invalid_values():
    text = unixbench_text(
        1e3,
        0,
        partial=True,
        extra=(
            "Additional Test       1.0    50.0    5e1\n"
            "Not Indexed           ---    50.0    ---\n"
            "Broken                1.0    50.0    nan\n"
            "Infinite              1.0    50.0    inf\n"
            "Negative              1.0    50.0    -1\n"
        ),
    ).replace("     ", "\t ")
    parsed = parse_unixbench(text)[1]
    assert parsed["partial"] and parsed["total"] == 0
    assert parsed["tests"] == {
        TEST_ORDER[0]: 1000,
        TEST_ORDER[1]: 2000,
        "Additional Test": 50,
        "Not Indexed": None,
        "Broken": None,
        "Infinite": None,
        "Negative": None,
    }


def test_custom_invalid_test_remains_visible_as_missing(config):
    store = Store(config.policy.state_dir)
    run_id = store.create(config)
    root = config.policy.output_dir / run_id
    write_round(root, "os1", 1, unixbench_text(partial=True, extra="Custom Broken  1  10  nan\n"))
    report = build_report(run_id, config, store.jobs(run_id), root)
    store.close()
    missing = next(t for t in report["profiles"][0]["tests"] if t["name"] == "Custom Broken")
    assert missing["values"]["host1/os1/ub"]["display"] == "N/A"
    assert any("Custom Broken" in warning for warning in report["warnings"])


def test_report_keeps_config_order_and_machine_benchmark_groups_separate(config):
    machine = config.machines[0].model_copy(deep=True)
    machine.id = "host2"
    machine.systems.reverse()
    config.machines.insert(0, machine)
    benchmark = config.benchmarks[0].model_copy(deep=True)
    benchmark.id = "ub2"
    config.benchmarks.append(benchmark)
    store = Store(config.policy.state_dir)
    run_id = store.create(config)
    root = config.policy.output_dir / run_id
    write_round(root, "os2", 1, unixbench_text(30), machine="host2")
    write_round(root, "os1", 1, unixbench_text(10))
    write_round(root, "os1", 1, unixbench_text(20), benchmark="ub2")
    report = build_report(run_id, config, store.jobs(run_id), root)
    store.close()
    assert [g["id"] for g in report["groups"]] == [
        "host2/os2/ub",
        "host2/os2/ub2",
        "host2/os1/ub",
        "host2/os1/ub2",
        "host1/os1/ub",
        "host1/os1/ub2",
        "host1/os2/ub",
        "host1/os2/ub2",
    ]
    assert report["baseline"] == "host2/os2/ub"
    values = report["profiles"][0]["tests"][0]["values"]
    assert [values[key]["mean"] for key in ("host2/os2/ub", "host1/os1/ub", "host1/os1/ub2")] == [
        30,
        10,
        20,
    ]


def test_parser_deduplicates_and_rejects_conflicting_profiles():
    text = unixbench_text()
    assert parse_unixbench(text + text) == parse_unixbench(text)
    with pytest.raises(ConflictingResults):
        parse_unixbench(text + unixbench_text(200))
    with pytest.raises(ConflictingResults):
        parse_unixbench(text.replace("    100\n", "    100\n" + f"{TEST_ORDER[0]}  1  2  200\n"))


@pytest.mark.parametrize(
    "text",
    [
        "",
        "benchmark failed",
        "System Benchmarks Index Score 99\n",
        "running 1 parallel copy of tests\n",
    ],
)
def test_parser_has_no_fabricated_results(text):
    assert parse_unixbench(text) == {}


def test_parser_retains_valid_items_from_truncated_summary():
    text = unixbench_text().split("Double-Precision Whetstone     55.0")[0]
    parsed = parse_unixbench(text)[1]
    assert parsed["tests"] == {TEST_ORDER[0]: 100}
    assert parsed["total"] is None


def test_round_prefers_report_and_falls_back_to_stdout(tmp_path):
    directory = write_round(tmp_path, "os1", 1, unixbench_text(100), filename="custom.out")
    (directory / "run.stdout").write_text(unixbench_text(999))
    (directory / "results" / "report.html").write_text(unixbench_text(999))
    assert read_round(tmp_path, "host1/os1/ub/round-001")[1]["tests"][TEST_ORDER[0]] == 100
    (directory / "results" / "custom.out").write_text("no final table")
    assert read_round(tmp_path, "host1/os1/ub/round-001")[1]["tests"][TEST_ORDER[0]] == 999


def test_round_merges_complementary_reports_once(tmp_path):
    write_round(tmp_path, "os1", 1, unixbench_text(100), filename="single.txt")
    write_round(tmp_path, "os1", 1, unixbench_text(6400, 640, 64), filename="multi.txt")
    write_round(
        tmp_path,
        "os1",
        1,
        unixbench_text(100).split("System Benchmarks Index Score")[0],
        filename="truncated.txt",
    )
    result = read_round(tmp_path, "host1/os1/ub/round-001")
    assert set(result) == {1, 64}
    assert result[1]["total"] == 10
    assert result[64]["tests"][TEST_ORDER[0]] == 6400


def test_means_baseline_missing_rounds_and_warmup(config, report_run):
    store, run_id, root = report_run
    warmup = root / "host1/os1/ub/warmup/results"
    warmup.mkdir(parents=True)
    (warmup / "report").write_text(unixbench_text(999999))
    report = build_report(run_id, config, store.jobs(run_id), root)
    assert report["baseline"] == "host1/os1/ub"
    assert [g["parsed_runs"] for g in report["groups"]] == [3, 2]
    assert [g["discovered_runs"] for g in report["groups"]] == [3, 2]
    profile = report["profiles"][0]
    assert profile["tests"][0]["values"]["host1/os1/ub"]["mean"] == 400
    assert profile["overall"]["host1/os1/ub"]["mean"] == 40
    assert profile["tests"][0]["values"]["host1/os2/ub"]["change_percent"] == pytest.approx(20)
    assert profile["tests"][0]["values"]["host1/os2/ub"]["display"] == "480.00 (+20.00%)"
    assert profile["tests"][2]["values"]["host1/os1/ub"]["display"] == "N/A"
    assert report["profiles"][1]["tests"][0]["values"]["host1/os1/ub"]["mean"] == 5000
    assert report["profiles"][1]["tests"][0]["values"]["host1/os1/ub"]["valid_runs"] == 2
    assert any("3/3" not in w and "有效轮次 2/3" in w for w in report["warnings"])
    assert any("轮次状态为 failed" in w for w in report["warnings"])
    other = build_report(run_id, config, store.jobs(run_id), root, "host1/os2/ub")
    assert other["profiles"][0]["overall"]["host1/os1/ub"]["change_percent"] == pytest.approx(
        -100 / 6
    )


def test_conflicting_round_is_excluded(config, report_run):
    store, run_id, root = report_run
    write_round(root, "os1", 1, unixbench_text(9000), filename="conflict.txt")
    report = build_report(run_id, config, store.jobs(run_id), root)
    assert report["groups"][0]["parsed_runs"] == 2
    assert report["profiles"][0]["tests"][0]["values"]["host1/os1/ub"]["mean"] == 550
    assert any("冲突" in warning for warning in report["warnings"])


def test_partial_zero_baseline_and_default_first_valid_group(config, tmp_path):
    store = Store(config.policy.state_dir)
    run_id = store.create(config)
    root = config.policy.output_dir / run_id
    write_round(root, "os2", 1, unixbench_text(0, 0, 4, partial=True))
    report = build_report(run_id, config, store.jobs(run_id), root)
    store.close()
    assert report["baseline"] == "host1/os2/ub"
    assert report["profiles"][0]["overall"]["host1/os2/ub"]["display"] == "0.00 (N/A) [Partial]"
    assert len(report["profiles"][0]["tests"]) == 2
    assert any("基线缺失或为零" in w for w in report["warnings"])


def test_workbook_matches_report_and_keeps_names_as_text(config, report_run):
    store, run_id, root = report_run
    report = build_report(run_id, config, store.jobs(run_id), root)
    report["profiles"][0]["tests"][0]["name"] = "=SUM(1,2)"
    book = load_workbook(io.BytesIO(report_workbook(report)))
    assert book.sheetnames == ["Summary", "1 parallel copy", "64 parallel copies"]
    assert book["1 parallel copy"]["A16"].data_type == "s"
    assert book["1 parallel copy"]["A16"].value == "=SUM(1,2)"
    for profile in report["profiles"]:
        sheet = book[profile["label"]]
        assert sheet.freeze_panes == "A4"
        assert sheet.auto_filter.ref == "A3:C3"
        for test in profile["tests"]:
            row = next(cell.row for cell in sheet["A"] if cell.value == test["name"])
            for col, group in enumerate(report["groups"], 2):
                cell = sheet.cell(row, col)
                assert cell.value == test["values"][group["id"]]["display"]
                assert "Valid runs:" in cell.comment.text
                assert cell.alignment.horizontal is None
    summary = list(book["Summary"].values)
    assert ("Overall Score", None, None) in summary
    assert any(row[0] == "Warnings" for row in summary)


def test_export_uses_template_styles_and_layout(config, report_run):
    store, run_id, root = report_run
    report = build_report(run_id, config, store.jobs(run_id), root)
    # Match the template's group count so every style and coordinate can be
    # compared directly, including blank separator rows and column widths.
    example = report["groups"][0]
    report["groups"] = [{**example, "id": f"group-{i}", "label": f"Group {i}"} for i in range(13)]
    report["baseline"] = "group-0"
    report["warnings"] = []
    for profile in report["profiles"]:
        for values in [profile["overall"], *[test["values"] for test in profile["tests"]]]:
            sample = values[example["id"]]
            values.clear()
            values.update({g["id"]: sample for g in report["groups"]})
    template = load_workbook(REPORT_TEMPLATE)
    result = load_workbook(io.BytesIO(report_workbook(report)))
    assert result.sheetnames == template.sheetnames
    for actual, expected in zip(result, template):
        assert actual.calculate_dimension() == expected.calculate_dimension()
        assert actual.freeze_panes == expected.freeze_panes
        assert actual.views == expected.views
        assert actual.auto_filter == expected.auto_filter
        assert actual.sheet_format == expected.sheet_format
        assert actual.page_margins == expected.page_margins
        assert actual.page_setup == expected.page_setup
        assert actual.print_options == expected.print_options
        assert list(actual.column_dimensions) == list(expected.column_dimensions)
        for column in actual.column_dimensions:
            assert dict(actual.column_dimensions[column]) == dict(
                expected.column_dimensions[column]
            )
        for row in expected:
            for original in row:
                generated = actual[original.coordinate]
                assert tuple(generated._style or [0] * 9) == tuple(original._style or [0] * 9), (
                    actual.title,
                    original.coordinate,
                )
    summary = result["Summary"]
    assert summary["A3"].value == "Test data"
    assert summary["B3"].value == str(root)
    assert summary["A6"].value is None
    assert summary["C8"].value == "Parsed Runs"
    assert summary.max_column == 3
    assert summary["A7"].fill.fgColor.rgb == "FFD9EAF7"
    assert summary["A8"].fill.fgColor.rgb == "FF5B9BD5"
    assert summary["A8"].font.color.rgb == "FFFFFFFF"
    assert summary["A1"].font.sz == 14
    assert summary["A9"].font.name == "Calibri"


@pytest.mark.parametrize("groups", [1, 15])
def test_template_resizes_without_leaking_example_values(config, report_run, groups):
    store, run_id, root = report_run
    report = build_report(run_id, config, store.jobs(run_id), root)
    example = report["groups"][0]
    report["groups"] = [{**example, "id": f"g-{i}", "label": f"Group {i}"} for i in range(groups)]
    report["baseline"] = "g-0"
    for profile in report["profiles"]:
        for values in [profile["overall"], *[test["values"] for test in profile["tests"]]]:
            sample = values[example["id"]]
            values.clear()
            values.update({g["id"]: sample for g in report["groups"]})
    result = load_workbook(io.BytesIO(report_workbook(report)))
    summary = result["Summary"]
    assert summary.cell(10 + groups, 1).value == "Overall Score"
    assert summary.cell(13 + 2 * groups, 1).value == "Detailed Results"
    assert summary.cell(16 + 2 * groups, 1).value == "Parsing Notes"
    assert summary.cell(22 + 2 * groups, 1).value == "Warnings"
    for sheet in list(result)[1:]:
        assert sheet.max_column == groups + 1
        assert len(sheet.column_dimensions) == groups + 1
    assert not any(
        "scaleos" in str(c.value) or "20260901" in str(c.value)
        for s in result
        for row in s
        for c in row
    )


def test_partial_excel_keeps_template_rows_and_cell_format(config):
    store = Store(config.policy.state_dir)
    run_id = store.create(config)
    root = config.policy.output_dir / run_id
    write_round(root, "os1", 1, unixbench_text(100, 10, 8, partial=True))
    report = build_report(run_id, config, store.jobs(run_id), root)
    store.close()
    result = load_workbook(io.BytesIO(report_workbook(report)))
    sheet = result["8 parallel copies"]
    assert sheet.max_row == 15
    assert sheet["A4"].value == TEST_ORDER[0]
    assert sheet["B4"].value == "100.00 (0.00%)"
    assert "Partial Index" in sheet["B4"].comment.text
    assert sheet["B6"].value == "N/A"
    assert result["Summary"]["B14"].value == "10.00 (0.00%)"


@pytest.fixture
def report_client(config, tmp_path):
    with TestClient(
        create_app(base_dir=tmp_path, state_dir=config.policy.state_dir),
        base_url="http://127.0.0.1:8765",
    ) as client:
        yield client


def test_report_api_and_excel_with_frozen_history(config, report_run, report_client):
    _, run_id, _ = report_run
    config.benchmarks[0].source_archive.unlink()
    url = f"/api/runs/{run_id}/report"
    response = report_client.get(
        url, params={"state_dir": str(config.policy.state_dir), "baseline": "host1/os2/ub"}
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["baseline"] == "host1/os2/ub"
    assert response.headers["cache-control"] == "no-store"
    response = report_client.get(url + ".xlsx", params={"baseline": report["baseline"]})
    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["content-type"]
    assert f"unixbench-{run_id}.xlsx" in response.headers["content-disposition"]
    book = load_workbook(io.BytesIO(response.content))
    assert (
        book["1 parallel copy"]["B4"].value
        == report["profiles"][0]["tests"][0]["values"]["host1/os1/ub"]["display"]
    )
    for suffix in ("", ".xlsx"):
        assert (
            report_client.get(url + suffix, params={"baseline": "../../etc/passwd"}).status_code
            == 422
        )
        assert report_client.get("/api/runs/invalid/report" + suffix).status_code == 422


def test_empty_report_is_available_without_remote_actions(config, report_client):
    store = Store(config.policy.state_dir)
    run_id = store.create(config)
    response = report_client.get(f"/api/runs/{run_id}/report")
    assert response.status_code == 200
    assert response.json()["profiles"] == []
    assert response.json()["warnings"]
    assert all(job["status"] == "pending" for job in store.jobs(run_id))
    store.close()


@pytest.mark.parametrize(
    "target",
    [
        "host1",
        "host1/os1/ub/round-001",
        "host1/os1/ub/round-001/results",
        "host1/os1/ub/round-001/results/report",
    ],
)
def test_report_api_rejects_symlink_inputs(config, report_run, report_client, tmp_path, target):
    store, run_id, root = report_run
    selected = root / target
    outside = tmp_path / "outside"
    selected.rename(outside)
    selected.symlink_to(outside, target_is_directory=outside.is_dir())
    for suffix in ("", ".xlsx"):
        response = report_client.get(f"/api/runs/{run_id}/report{suffix}")
        assert response.status_code == 403, response.text
    with pytest.raises(UnsafeArtifactPath):
        build_report(run_id, config, store.jobs(run_id), root) if target == "host1" else read_round(
            root, "host1/os1/ub/round-001"
        )


def test_report_api_rejects_replaced_output_root(config, report_run, report_client, tmp_path):
    _, run_id, _ = report_run
    outside = tmp_path / "moved-output"
    shutil.move(config.policy.output_dir, outside)
    config.policy.output_dir.symlink_to(outside, target_is_directory=True)
    assert report_client.get(f"/api/runs/{run_id}/report").status_code == 403
