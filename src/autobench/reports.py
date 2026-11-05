"""UnixBench final INDEX parsing, arithmetic means, and comparison workbooks."""

from __future__ import annotations

import io
import math
import re
import time
from copy import copy
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.comments import Comment
from openpyxl.utils import get_column_letter

from autobench.artifacts import result_path
from autobench.config import Config

REPORT_TEMPLATE = Path(__file__).with_name("templates") / "unixbench.xlsx"

TEST_ORDER = (
    "Dhrystone 2 using register variables",
    "Double-Precision Whetstone",
    "Execl Throughput",
    "File Copy 1024 bufsize 2000 maxblocks",
    "File Copy 256 bufsize 500 maxblocks",
    "File Copy 4096 bufsize 8000 maxblocks",
    "Pipe Throughput",
    "Pipe-based Context Switching",
    "Process Creation",
    "Shell Scripts (1 concurrent)",
    "Shell Scripts (8 concurrent)",
    "System Call Overhead",
)
PROFILE = re.compile(r"\brunning\s+(\d+)\s+parallel\s+cop(?:y|ies)\s+of tests\b", re.I)
HEADER = re.compile(
    r"^System Benchmarks\s+(Index Values|Partial Index)\s+BASELINE\s+RESULT\s+INDEX$"
)
TOTAL = re.compile(r"^System Benchmarks Index Score\s*(\(Partial Only\))?\s+(\S+)\s*$")
ROW = re.compile(r"^(.*?)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")


class ConflictingResults(ValueError):
    pass


def merge_profiles(target: dict[int, dict], source: dict[int, dict]):
    """Combine complementary sources without counting any profile twice."""
    for copies, profile in source.items():
        if copies not in target:
            target[copies] = profile
            continue
        current = target[copies]
        for name, value in profile["tests"].items():
            previous = current["tests"].get(name)
            if previous is not None and value is not None and previous != value:
                raise ConflictingResults("同一轮的报告包含冲突的 INDEX 结果，该轮已排除")
            current["tests"][name] = previous if previous is not None else value
        if (
            current["total"] is not None
            and profile["total"] is not None
            and current["total"] != profile["total"]
        ):
            raise ConflictingResults("同一轮的报告包含冲突的总分，该轮已排除")
        if current["total"] is None:
            current["total"] = profile["total"]
        current["partial"] |= profile["partial"]


def number(value: str) -> float | None:
    try:
        result = float(value)
    except ValueError:
        return None
    return result if math.isfinite(result) and result >= 0 else None


def parse_unixbench(text: str) -> dict[int, dict]:
    """Read only final System Benchmarks tables, never the raw throughput rows."""
    profiles = {}
    copies = None
    current = None
    in_table = False

    def finish():
        if current is None or not (
            any(value is not None for value in current["tests"].values())
            or current["total"] is not None
        ):
            return
        merge_profiles(profiles, {copies: current})

    for raw in text.splitlines():
        line = raw.strip()
        profile = PROFILE.search(line)
        if profile:
            finish()
            copies = int(profile[1])
            current = None
            in_table = False
            continue
        header = HEADER.fullmatch(line)
        if header and copies and current is None:
            current = {"tests": {}, "total": None, "partial": header[1] == "Partial Index"}
            in_table = True
            continue
        if current is None:
            continue
        total = TOTAL.fullmatch(line)
        if total:
            current["total"] = number(total[2])
            current["partial"] |= bool(total[1])
            in_table = False
            continue
        if not in_table or not line or line.startswith("="):
            continue
        row = ROW.fullmatch(line)
        if row and (number(row[2]) is not None or row[2] == "---") and number(row[3]) is not None:
            name = " ".join(row[1].split())
            value = number(row[4])
            if name in current["tests"] and current["tests"][name] != value:
                raise ConflictingResults("同一轮包含冲突的测试项结果")
            current["tests"][name] = value
    finish()
    return profiles


def read_round(root: Path, relative: str) -> dict[int, dict]:
    """Prefer text reports; stdout is a fallback, not another observation."""
    results = result_path(root, relative + "/results")
    parsed = {}
    if results.is_dir():
        for entry in sorted(results.iterdir()):
            if entry.name.startswith(".") or entry.suffix.lower() in {
                ".html",
                ".htm",
                ".csv",
                ".log",
                ".json",
            }:
                continue
            path = result_path(root, entry.relative_to(root).as_posix())
            if path.is_file():
                value = parse_unixbench(path.read_text(encoding="utf-8", errors="replace"))
                if value:
                    merge_profiles(parsed, value)
    if parsed:
        return parsed
    stdout = result_path(root, relative + "/run.stdout")
    if stdout.is_file():
        return parse_unixbench(stdout.read_text(encoding="utf-8", errors="replace"))
    return {}


def profile_name(copies: int) -> str:
    return f"{copies} parallel {'copy' if copies == 1 else 'copies'}"


def build_report(
    run_id: str, config: Config, jobs: list[dict], root: Path, baseline: str | None = None
) -> dict:
    groups = []
    samples = {}
    warnings = []
    for machine in config.machines:
        for system in machine.systems:
            for benchmark in config.benchmarks:
                key = f"{machine.id}/{system.id}/{benchmark.id}"
                group_jobs = [
                    job
                    for job in jobs
                    if (job["machine"], job["system"], job["benchmark"])
                    == (machine.id, system.id, benchmark.id)
                ]
                group = {
                    "id": key,
                    "label": " / ".join((machine.id, system.id, benchmark.id)),
                    "planned_runs": len(group_jobs),
                    "discovered_runs": 0,
                    "parsed_runs": 0,
                }
                groups.append(group)
                samples[key] = []
                for job in group_jobs:
                    relative = f"{key}/round-{job['repetition']:03d}"
                    directory = result_path(root, relative)
                    if not directory.is_dir():
                        warnings.append(f"{relative}：结果未回收，未计入平均值。")
                        continue
                    group["discovered_runs"] += 1
                    try:
                        parsed = read_round(root, relative)
                    except ConflictingResults as exc:
                        warnings.append(f"{relative}：{exc}")
                        continue
                    except OSError:
                        warnings.append(f"{relative}：结果文件读取失败，未计入平均值。")
                        continue
                    if not parsed:
                        warnings.append(f"{relative}：没有可解析的 INDEX 结果。")
                        continue
                    group["parsed_runs"] += 1
                    samples[key].append(parsed)
                    if job["status"] != "succeeded":
                        warnings.append(
                            f"{relative}：轮次状态为 {job['status']}，仅计入有效 INDEX 值。"
                        )

    keys = {group["id"] for group in groups}
    if baseline is not None and baseline not in keys:
        raise ValueError("未知基线组")
    baseline = baseline or next((key for key, values in samples.items() if values), groups[0]["id"])
    profiles = []
    copies_set = sorted({copies for values in samples.values() for run in values for copies in run})
    for copies in copies_set:
        names = {
            name
            for values in samples.values()
            for run in values
            for name in run.get(copies, {}).get("tests", {})
        }
        if any(
            not run[copies]["partial"]
            for values in samples.values()
            for run in values
            if copies in run
        ):
            names.update(TEST_ORDER)
        tests = [name for name in TEST_ORDER if name in names] + sorted(names - set(TEST_ORDER))

        def aggregate(name: str | None) -> dict:
            cells = {}
            for group in groups:
                key = group["id"]
                values = []
                partial = False
                for run in samples[key]:
                    profile = run.get(copies)
                    if profile is None:
                        continue
                    value = profile["total"] if name is None else profile["tests"].get(name)
                    if value is not None:
                        values.append(value)
                        partial |= profile["partial"]
                cells[key] = {
                    "mean": math.fsum(value / len(values) for value in values) if values else None,
                    "valid_runs": len(values),
                    "planned_runs": group["planned_runs"],
                    "partial": partial,
                }
                if len(values) < group["planned_runs"]:
                    warnings.append(
                        f"{group['label']} · {profile_name(copies)} · {name or 'Overall Score'}："
                        f"有效轮次 {len(values)}/{group['planned_runs']}，仅按有效值平均。"
                    )
            base = cells[baseline]["mean"]
            if base is None or base == 0:
                warnings.append(
                    f"{profile_name(copies)} · {name or 'Overall Score'}：基线缺失或为零，变化率为 N/A。"
                )
            for cell in cells.values():
                delta = (
                    (cell["mean"] / base - 1) * 100 if base and cell["mean"] is not None else None
                )
                cell["change_percent"] = (
                    delta if delta is not None and math.isfinite(delta) else None
                )
                cell["display"] = format_cell(cell)
            return cells

        overall = aggregate(None)
        if any(cell["partial"] for cell in overall.values()):
            warnings.append(
                f"{profile_name(copies)}：标记 Partial 的总分为部分总分，测试范围可能不同。"
            )
        profiles.append(
            {
                "copies": copies,
                "label": profile_name(copies),
                "overall": overall,
                "tests": [{"name": name, "values": aggregate(name)} for name in tests],
            }
        )
    return {
        "run_id": run_id,
        "data_directory": str(root),
        "generated_at": time.time(),
        "baseline": baseline,
        "groups": groups,
        "profiles": profiles,
        "warnings": warnings,
    }


def format_cell(cell: dict) -> str:
    if cell["mean"] is None:
        return "N/A"
    change = cell["change_percent"]
    # Avoid displaying a negative zero after rounding.
    delta = "N/A" if change is None else "0.00%" if abs(change) < 0.005 else f"{change:+.2f}%"
    suffix = " [Partial]" if cell["partial"] else ""
    return f"{cell['mean']:.2f} ({delta}){suffix}"


def report_workbook(report: dict) -> bytes:
    """Fill the supplied workbook template without imposing application UI styles."""
    book = load_workbook(REPORT_TEMPLATE)
    summary = book["Summary"]
    groups = report["groups"]
    profiles = report["profiles"]
    sources = list(book)[1:]
    details = []
    for profile in profiles:
        source = sources[0] if profile["copies"] == 1 else sources[1]
        sheet = book.copy_worksheet(source)
        sheet.views = copy(source.views)
        sheet.auto_filter = copy(source.auto_filter)
        details.append(sheet)
    for source in sources:
        book.remove(source)

    def write(sheet, row, col, value, style=None):
        cell = sheet.cell(row, col, value)
        if isinstance(value, str):
            cell.data_type = "s"
        if style is not None:
            cell._style = copy(style)
        return cell

    def score(sheet, row, col, data):
        # Partial and sample counts belong to comments/warnings, preserving the
        # template's visible "average (vs baseline)" cell format.
        cell = write(sheet, row, col, data["display"].removesuffix(" [Partial]"))
        cell.comment = Comment(
            f"Valid runs: {data['valid_runs']}/{data['planned_runs']}"
            + ("; Partial Index" if data["partial"] else ""),
            "AutoBench",
        )

    # Resize the two 13-group example blocks from the bottom upward, leaving
    # section styles and the template's blank separator rows intact.
    count = len(groups)
    for first in (25, 9):
        if count < 13:
            summary.delete_rows(first + count, 13 - count)
        elif count > 13:
            summary.insert_rows(first + 13, count - 13)
    offset = count - 13
    overall_header = 24 + offset
    overall_first = 25 + offset
    notes_first = 43 + 2 * offset
    warnings_first = 49 + 2 * offset
    write(summary, 3, 2, report.get("data_directory", report["run_id"]))
    write(summary, 4, 2, next(g["label"] for g in groups if g["id"] == report["baseline"]))
    write(summary, 5, 2, count)
    # Keep the template's three-column Run Summary; no extra timestamp or
    # Planned Runs column is inserted into the exported workbook.
    for row, group in enumerate(groups, 9):
        for col, value in enumerate(
            (group["label"], group["discovered_runs"], group["parsed_runs"]), 1
        ):
            write(summary, row, col, value)
    header_style = copy(summary.cell(overall_header, 1)._style)
    for col, profile in enumerate(profiles, 2):
        write(summary, overall_header, col, profile["label"], header_style)
    for col in range(len(profiles) + 2, 4):
        summary.cell(overall_header, col).value = None
        summary.cell(overall_header, col)._style = copy(summary.cell(3, 1)._style)
    for row, group in enumerate(groups, overall_first):
        write(summary, row, 1, group["label"])
        for col, profile in enumerate(profiles, 2):
            score(summary, row, col, profile["overall"][group["id"]])
    for col in range(4, max(3, len(profiles) + 1) + 1):
        summary.column_dimensions[get_column_letter(col)].width = summary.column_dimensions[
            "C"
        ].width
    write(
        summary,
        notes_first,
        1,
        "Each machine / system / benchmark is treated as one test group; its formal rounds are treated as individual runs. Warmup is excluded.",
    )
    for row, warning in enumerate(report["warnings"] or ["No warnings."], warnings_first):
        write(summary, row, 1, warning)

    for profile, sheet in zip(profiles, details):
        sheet.title = profile["label"]
        write(sheet, 1, 1, f"Detailed Results - {profile['label']}")
        header_style = copy(sheet["B3"]._style)
        # Discard unused example columns and their widths, retaining each used
        # template column's own width rather than replacing it with one default.
        last_col = count + 1
        if last_col < sheet.max_column:
            sheet.delete_cols(last_col + 1, sheet.max_column - last_col)
        for name in list(sheet.column_dimensions):
            if sheet.column_dimensions[name].min > last_col:
                del sheet.column_dimensions[name]
        for col, group in enumerate(groups, 2):
            write(sheet, 3, col, group["label"], header_style)
            if col > 14:
                sheet.column_dimensions[get_column_letter(col)].width = 32
        tests = {test["name"]: test for test in profile["tests"]}
        names = list(TEST_ORDER) + [name for name in tests if name not in TEST_ORDER]
        for row, name in enumerate(names, 4):
            write(sheet, row, 1, name)
            for col, group in enumerate(groups, 2):
                missing = {
                    "display": "N/A",
                    "valid_runs": 0,
                    "planned_runs": group["planned_runs"],
                    "partial": False,
                }
                data = tests[name]["values"][group["id"]] if name in tests else missing
                score(sheet, row, col, data)
        sheet.auto_filter.ref = f"A3:{get_column_letter(last_col)}3"
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()
