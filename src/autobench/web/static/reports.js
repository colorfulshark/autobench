import { api } from "./state.js";
import { node, button, hint, heading, table, date } from "./ui.js";

export function createReportPanel(reportUrl) {
  const root = node("section", "panel report-panel");
  root.setAttribute("aria-label", "UnixBench 平均结果报告");
  let data = null;
  let loading = false;
  let error = "";
  let baseline = "";
  let copies = null;
  let request = 0;

  function url(excel = false) {
    const target = new URL(reportUrl, window.location.origin);
    if (excel) target.pathname += ".xlsx";
    if (baseline) target.searchParams.set("baseline", baseline);
    return target.pathname + target.search;
  }

  async function load() {
    const token = ++request;
    loading = true;
    error = "";
    render();
    try {
      const result = await api(url());
      if (token !== request) return;
      data = result;
      baseline = result.baseline;
      if (!data.profiles.some((p) => p.copies === copies))
        copies = data.profiles[0]?.copies ?? null;
    } catch (failure) {
      if (token !== request) return;
      error = failure.message;
    } finally {
      if (token === request) {
        loading = false;
        render();
      }
    }
  }

  function select(labelText, id, options, value, change) {
    const field = node("div", "report-field");
    const label = node("label", "field-label", labelText);
    label.htmlFor = id;
    const input = node("select");
    input.id = id;
    for (const [key, text] of options) {
      const option = node("option", "", text);
      option.value = key;
      option.selected = String(key) === String(value);
      input.append(option);
    }
    input.addEventListener("change", () => change(input.value));
    field.append(label, input);
    return field;
  }

  function score(cell) {
    const box = node("div", "report-score");
    box.append(node("span", "", cell.display));
    box.append(
      node(
        "span",
        "secondary",
        `有效轮次 ${cell.valid_runs}/${cell.planned_runs}`,
      ),
    );
    return box;
  }

  function reportTable(headers, rows) {
    const scroll = node("div", "table-scroll report-table");
    scroll.tabIndex = 0;
    scroll.setAttribute("role", "region");
    scroll.setAttribute("aria-label", headers[0] + "对比表，可横向滚动");
    const grid = table(headers, rows);
    grid.querySelectorAll("tr").forEach((row) => {
      [...row.children].slice(1).forEach((cell) => cell.classList.add("num"));
    });
    scroll.append(grid);
    return scroll;
  }

  function render() {
    const focused = root.contains(document.activeElement)
      ? document.activeElement.id
      : "";
    const actions = node("div", "actions");
    const refresh = button(
      loading ? "生成中…" : data ? "刷新报告" : "生成报告",
      load,
      "button secondary",
    );
    refresh.id = "generate-report";
    refresh.disabled = loading;
    actions.append(refresh);
    if (data && !loading && !error) {
      const download = node("a", "button secondary", "下载 Excel");
      download.id = "download-report";
      download.href = url(true);
      download.download = "";
      actions.append(download);
    }
    const header = heading(
      "UnixBench 平均结果报告",
      "各正式轮次 INDEX 分数的算术平均值，预热不计入。",
      actions,
    );
    root.replaceChildren(header);
    root.setAttribute("aria-busy", String(loading));
    if (loading) {
      const message = hint(
        data ? "正在更新，以下为上次生成的结果。" : "正在读取已回收的正式轮次…",
      );
      message.setAttribute("role", "status");
      root.append(message);
    }
    if (error) {
      const message = node("p", "callout warning", `报告生成失败：${error}`);
      message.setAttribute("role", "alert");
      root.append(message, button("重试", load, "text-button"));
    }
    if (!data) {
      if (!loading && !error)
        root.append(hint("生成报告以查看均值、相对基线变化率和有效轮次数。"));
      return;
    }
    const controls = node("div", "report-controls");
    controls.append(
      select(
        "比较基线",
        "report-baseline",
        data.groups.map((group) => [group.id, group.label]),
        baseline,
        (value) => {
          baseline = value;
          load();
        },
      ),
      hint(
        `生成时间：${date(data.generated_at)} · 基线：${data.groups.find((g) => g.id === data.baseline).label}`,
      ),
    );
    root.append(controls);
    const summaries = node("div", "report-summary-grid");
    const rounds = node("section", "report-rounds");
    rounds.append(
      node("h3", "report-subheading", "轮次概况"),
      reportTable(
        ["比较组", "计划轮次", "已回收", "已解析"],
        data.groups.map((g) => [
          g.label,
          g.planned_runs,
          g.discovered_runs,
          g.parsed_runs,
        ]),
      ),
    );
    summaries.append(rounds);
    root.append(summaries);
    if (!data.profiles.length) {
      root.append(hint("暂无可解析的 INDEX 结果。结果回收后可刷新报告。"));
    } else {
      const overall = node("section", "report-overall");
      overall.append(
        node("h3", "report-subheading", "总分对比"),
        reportTable(
          ["比较组", ...data.profiles.map((p) => p.label)],
          data.groups.map((g) => [
            g.label,
            ...data.profiles.map((p) => score(p.overall[g.id])),
          ]),
        ),
      );
      summaries.append(overall);
      const detail = node("div", "report-detail-heading");
      detail.append(
        node("h3", "report-subheading", "单项明细"),
        select(
          "并发数",
          "report-copies",
          data.profiles.map((p) => [p.copies, p.label]),
          copies,
          (value) => {
            copies = Number(value);
            render();
          },
        ),
      );
      root.append(detail);
      const profile = data.profiles.find((p) => p.copies === copies);
      root.append(
        reportTable(
          ["测试项", ...data.groups.map((g) => g.label)],
          profile.tests.map((t) => [
            t.name,
            ...data.groups.map((g) => score(t.values[g.id])),
          ]),
        ),
        hint(
          "分数（相对基线变化率）；Partial 表示部分测试总分，测试范围可能不同。",
        ),
      );
    }
    const warnings = node("details", "technical report-warnings");
    warnings.open = !data.profiles.length;
    warnings.append(
      node("summary", "", `统计警告 · ${data.warnings.length} 条`),
    );
    const list = node("ul");
    for (const warning of data.warnings) list.append(node("li", "", warning));
    warnings.append(
      data.warnings.length ? list : hint("所有轮次已解析，无统计警告。"),
    );
    root.append(warnings);
    if (focused) root.querySelector(`#${focused}`)?.focus();
  }

  render();
  return root;
}
