import { api } from "./state.js";
import { node, button, hint, heading, icon, table, date } from "./ui.js";

const REPORT_TITLE = "UnixBench 结果报告";
const AGGREGATION_LABELS = { mean: "平均数", median: "中位数" };

export function createReportPanel(reportUrl) {
  const root = node("section", "panel report-panel");
  root.setAttribute("aria-label", REPORT_TITLE);
  let data = null;
  let loading = false;
  let error = "";
  let baseline = "";
  let aggregation = "mean";
  let copies = null;
  let request = 0;

  function url(excel = false) {
    const target = new URL(reportUrl, window.location.origin);
    if (excel) target.pathname += ".xlsx";
    const selection = excel ? data : { baseline, aggregation };
    if (selection.baseline) target.searchParams.set("baseline", selection.baseline);
    target.searchParams.set("aggregation", selection.aggregation);
    return target.pathname + target.search;
  }

  async function load() {
    const token = ++request;
    loading = true;
    error = "";
    updateStatus();
    try {
      const result = await api(url());
      if (token !== request) return;
      if (!data || (data.profiles.length && !result.profiles.length))
        warnings.open = !result.profiles.length;
      data = result;
      baseline = result.baseline;
      aggregation = result.aggregation;
      if (!data.profiles.some((p) => p.copies === copies))
        copies = data.profiles[0]?.copies ?? null;
      updateReport();
    } catch (failure) {
      if (token !== request) return;
      error = failure.message;
    } finally {
      if (token === request) {
        loading = false;
        updateStatus();
      }
    }
  }

  function select(labelText, id, change) {
    const field = node("div", "report-field");
    const label = node("label", "field-label", labelText);
    label.htmlFor = id;
    const input = node("select");
    input.id = id;
    input.addEventListener("change", () => change(input.value));
    field.append(label, input);
    return {
      field,
      update(options, value) {
        const matches =
          options.length === input.options.length &&
          options.every(([key, text], index) => {
            const option = input.options[index];
            return option.value === String(key) && option.label === text;
          });
        if (!matches) {
          input.replaceChildren(
            ...options.map(([key, text]) => {
              const option = node("option", "", text);
              option.value = key;
              return option;
            }),
          );
        }
        if (input.value !== String(value)) input.value = value;
      },
    };
  }

  function score(cell) {
    const value = node("span", "report-score-value", cell.display);
    const change = cell.change_percent;
    // Match the report's displayed precision when the change rounds to zero.
    if (change && !cell.display.includes("(0.00%)")) {
      const improved = change > 0;
      const arrow = icon(
        improved ? "arrowUp" : "arrowDown",
        "icon report-score-arrow",
      );
      arrow.dataset.tone = improved ? "success" : "error";
      value.append(
        arrow,
        node("span", "visually-hidden", improved ? " 较基线提升" : " 较基线下降"),
      );
    }
    return value;
  }

  function reportTable(label) {
    const scroll = node("div", "table-scroll report-table");
    scroll.tabIndex = 0;
    scroll.setAttribute("role", "region");
    scroll.setAttribute("aria-label", label + "对比表，可横向滚动");
    return {
      scroll,
      update(headers, rows) {
        const grid = table(headers, rows);
        grid.querySelectorAll("tr").forEach((row) => {
          [...row.children].slice(1).forEach((cell) => cell.classList.add("num"));
        });
        const scrollLeft = scroll.scrollLeft;
        scroll.replaceChildren(grid);
        scroll.scrollLeft = scrollLeft;
      },
    };
  }

  const actions = node("div", "actions");
  const refresh = button("刷新报告", load, "button secondary", "file");
  const download = node("a", "button secondary", "下载 Excel");
  download.id = "download-report";
  download.download = "";
  download.setAttribute("role", "link");
  actions.append(refresh, download);
  const header = heading(
    REPORT_TITLE,
    "仅统计正式轮次的有效 INDEX 分数，预热不计入。",
    actions,
    "chart",
  );
  const status = hint();
  status.setAttribute("role", "status");
  const failure = node("div");
  const failureMessage = node("p", "callout warning");
  failureMessage.setAttribute("role", "alert");
  failure.append(failureMessage, button("重试", load, "text-button"));

  const controls = node("div", "report-controls");
  const baselineSelect = select("比较基线", "report-baseline", (value) => {
    baseline = value;
    load();
  });
  const aggregationSelect = select("统计口径", "report-aggregation", (value) => {
    aggregation = value;
    load();
  });
  const metadata = hint();
  controls.append(baselineSelect.field, aggregationSelect.field, metadata);

  const overall = node("section", "report-overall");
  const overallTable = reportTable("比较组");
  overall.append(node("h3", "report-subheading", "总分对比"), overallTable.scroll);
  const empty = hint("暂无可解析的 INDEX 结果。结果回收后可刷新报告。");

  const detail = node("div", "report-detail-heading");
  const copiesSelect = select("并发数", "report-copies", (value) => {
    copies = Number(value);
    updateDetail();
  });
  detail.append(node("h3", "report-subheading", "单项明细"), copiesSelect.field);
  const detailTable = reportTable("测试项");
  const detailHint = hint(
    "分数（相对基线变化率）；Partial 表示部分测试总分，测试范围可能不同。",
  );

  const warnings = node("details", "technical report-warnings");
  const warningSummary = node("summary");
  const warningList = node("ul");
  const noWarnings = hint("所有轮次已解析，无统计警告。");
  warnings.append(warningSummary, warningList, noWarnings);

  function updateStatus() {
    root.setAttribute("aria-busy", String(loading));
    refresh.hidden = !data;
    refresh.disabled = loading;
    download.hidden = !data || Boolean(error);
    if (loading || download.hidden) download.removeAttribute("href");
    else download.href = url(true);
    download.setAttribute("aria-disabled", String(loading));
    status.classList.toggle("visually-hidden", Boolean(data));
    status.hidden = !loading;
    status.textContent = data
      ? "正在更新，以下为上次生成的结果。"
      : "正在读取已回收的正式轮次…";
    failure.hidden = !error;
    failureMessage.textContent = error ? `报告生成失败：${error}` : "";
  }

  function updateReport() {
    controls.hidden = false;
    overall.hidden = false;
    warnings.hidden = false;
    baselineSelect.update(data.groups.map((group) => [group.id, group.label]), baseline);
    aggregationSelect.update(Object.entries(AGGREGATION_LABELS), aggregation);
    const baselineGroup = data.groups.find((group) => group.id === data.baseline);
    metadata.textContent = `生成时间：${date(data.generated_at)} · 基线：${baselineGroup.label} · 统计口径：${AGGREGATION_LABELS[data.aggregation]}`;
    const index128 = data.profiles.findIndex((profile) => profile.copies === 128);
    const roundColumnOffset = index128 === -1 ? data.profiles.length : index128 + 1;
    const leadingProfiles = data.profiles.slice(0, roundColumnOffset);
    const trailingProfiles = data.profiles.slice(roundColumnOffset);
    overallTable.update(
      [
        "比较组",
        ...leadingProfiles.map((p) => p.label),
        "计划轮次",
        "已回收",
        "已解析",
        ...trailingProfiles.map((p) => p.label),
      ],
      data.groups.map((g) => [
        g.label,
        ...leadingProfiles.map((p) => score(p.overall[g.id])),
        g.planned_runs,
        g.discovered_runs,
        g.parsed_runs,
        ...trailingProfiles.map((p) => score(p.overall[g.id])),
      ]),
    );
    const hasProfiles = data.profiles.length > 0;
    empty.hidden = hasProfiles;
    detail.hidden = !hasProfiles;
    detailTable.scroll.hidden = !hasProfiles;
    detailHint.hidden = !hasProfiles;
    if (hasProfiles) {
      copiesSelect.update(data.profiles.map((p) => [p.copies, p.label]), copies);
      updateDetail();
    } else {
      detailTable.scroll.replaceChildren();
    }
    warningSummary.textContent = `统计警告 · ${data.warnings.length} 条`;
    warningList.replaceChildren();
    for (const warning of data.warnings) warningList.append(node("li", "", warning));
    warningList.hidden = data.warnings.length === 0;
    noWarnings.hidden = !warningList.hidden;
  }

  function updateDetail() {
    const profile = data.profiles.find((p) => p.copies === copies);
    detailTable.update(
      ["测试项", ...data.groups.map((g) => g.label)],
      profile.tests.map((t) => [
        t.name,
        ...data.groups.map((g) => score(t.values[g.id])),
      ]),
    );
  }

  for (const part of [controls, overall, empty, detail, detailTable.scroll, detailHint, warnings])
    part.hidden = true;
  root.append(
    header,
    status,
    failure,
    controls,
    overall,
    empty,
    detail,
    detailTable.scroll,
    detailHint,
    warnings,
  );
  updateStatus();
  return {
    element: root,
    loadInitially() {
      if (request === 0) load();
    },
  };
}
