import { state, api, waitOperation, show } from "./state.js";
import { configStateDir, statusLabels } from "./model.js";
import {
  $,
  node,
  button,
  hint,
  badge,
  heading,
  table,
  date,
  empty,
  perform,
} from "./ui.js";
import { chooseFile } from "./files.js";
const view = {
  directory: "",
  selected: null,
  list: [],
  detail: null,
  after: 0,
  events: [],
  artifacts: [],
  artifactError: "",
  artifactsAt: 0,
  busy: false,
  control: false,
  search: "",
  filter: "",
  token: 0,
  renderKey: null,
};
export const directory = () => view.directory || state.info.state_dir;
const query = (extra) =>
  new URLSearchParams({ state_dir: directory(), ...extra });
function url(suffix = "", extra = {}) {
  return `/api/runs/${encodeURIComponent(view.selected)}${suffix}?${query(extra)}`;
}
function storage() {
  if (view.selected)
    sessionStorage.setItem(
      "autobench-selection",
      JSON.stringify({ id: view.selected, stateDir: directory() }),
    );
  else sessionStorage.removeItem("autobench-selection");
}
export function setDirectory(path) {
  view.directory = path;
  view.selected = null;
  view.detail = null;
  view.token++;
  storage();
  renderRuns();
  return refreshRuns();
}
export async function selectRun(id) {
  view.selected = id;
  view.detail = null;
  view.renderKey = null;
  view.after = 0;
  view.events = [];
  view.artifacts = [];
  view.artifactError = "";
  view.artifactsAt = 0;
  view.token++;
  storage();
  $("preview-drawer").hidden = true;
  await refreshDetail();
  renderRuns();
}
export async function openRun(id, path) {
  view.directory = path;
  await selectRun(id);
  await refreshRuns();
}
export async function startRun() {
  if (
    !state.validation ||
    state.validation.version !== state.version ||
    state.dirty
  )
    throw new Error("请先保存并检查当前配置。");
  const result = await api("/api/runs", {
    path: state.path,
    revision: state.revision,
  });
  await openRun(result.run_id, result.state_dir);
  observe(result.operation);
  return result;
}
async function observe(operation) {
  try {
    const result = await waitOperation(operation);
    show(`操作结束：${statusLabels[result] || result || "已完成"}`);
    view.artifactsAt = 0;
    await refreshRuns();
  } catch (error) {
    show(error.message, "error");
  }
}
async function control(action) {
  if (view.control) return;
  view.control = true;
  renderDetail();
  try {
    const result = await api(`/api/runs/${view.selected}/${action}`, {
      state_dir: directory(),
    });
    show(
      result.status === "cancel_requested"
        ? "取消请求已保存，控制器将处理远端任务。"
        : "操作已提交。",
    );
    if (result.id) observe(result);
    view.artifactsAt = 0;
  } finally {
    view.control = false;
    await refreshDetail();
  }
}
export async function refreshRuns() {
  if (view.busy) return;
  view.busy = true;
  const path = directory();
  try {
    const list = await api("/api/runs?" + query());
    if (path !== directory()) return;
    view.list = list;
    renderList();
    if (view.selected) await refreshDetail();
  } finally {
    view.busy = false;
  }
}
async function refreshDetail() {
  if (!view.selected) return;
  const token = view.token;
  const detail = await api(url());
  if (token !== view.token) return;
  view.detail = detail;
  const events = await api(url("/events", { after: view.after }));
  if (token !== view.token) return;
  for (const event of events) {
    if (event.sequence <= view.after) continue;
    view.after = event.sequence;
    view.events.push(event);
  }
  view.events = view.events.slice(-5000);
  if (Date.now() - view.artifactsAt > 8000) {
    view.artifactsAt = Date.now();
    try {
      const artifacts = await api(url("/artifacts"));
      if (token !== view.token) return;
      view.artifacts = artifacts;
      view.artifactError = "";
    } catch (error) {
      view.artifactError =
        error.status === 404
          ? "结果尚未回收，可在任务封存后收集。"
          : error.message;
    }
  }
  renderDetail();
}
function renderList() {
  $("state-location").textContent = directory();
  const list = view.list.filter(
    (run) =>
      (!view.filter || run.status === view.filter) &&
      (!view.search ||
        `${run.id} ${date(run.created)} ${statusLabels[run.status] || run.status}`
          .toLowerCase()
          .includes(view.search.toLowerCase())),
  );
  $("run-count").textContent = `${list.length} 次运行`;
  if (!list.length) {
    $("runs-list").replaceChildren(
      empty(
        "没有匹配的运行记录",
        "可从配置页面开始测试，或更改历史记录的位置。",
      ),
    );
    return;
  }
  $("runs-list").replaceChildren(
    table(
      ["创建时间", "状态", "最近更新", "操作"],
      list.map((run) => {
        const title = node("div");
        title.append(
          node("strong", "", date(run.created)),
          node("div", "secondary mono", run.id),
        );
        return [
          title,
          badge(run.status),
          date(run.updated),
          button(
            view.selected === run.id ? "当前查看" : "查看详情",
            () => selectRun(run.id),
            "text-button",
          ),
        ];
      }),
    ),
  );
}
function outcomeHint(detail) {
  if (
    detail.cancel_requested &&
    !detail.controller_active &&
    !["succeeded", "cancelled"].includes(detail.status)
  )
    return "取消请求已保存。继续处理取消以停止可达任务并回收结果；不可达机器恢复连接后可再次处理。";
  if (
    !detail.controller_active &&
    ["pending", "running", "paused"].includes(detail.status)
  )
    return "控制器当前未连接，可能仍有远端任务。恢复控制后会核对原任务，不会重复提交。";
  if (detail.status === "failed")
    return "本次运行存在失败任务。失败测试不会自动重跑；如需重新测试，请修改配置并创建新运行。";
  if (detail.status === "cancelled")
    return "运行已取消。已回收的日志与结果仍可查看和下载。";
  if (detail.status === "succeeded") return "全部任务已成功完成并回收结果。";
  if (detail.cancel_requested)
    return "取消请求已持久化；不可达机器恢复连接后可继续处理。";
  return "关闭浏览器不会停止任务，状态每 2 秒自动更新。";
}
function renderDetail() {
  const d = view.detail;
  const root = $("run-detail");
  if (!d) {
    root.hidden = true;
    return;
  }
  root.hidden = false;
  const renderKey = JSON.stringify([
    d,
    view.after,
    view.artifacts,
    view.artifactError,
    view.control,
  ]);
  if (view.renderKey === renderKey) return;
  view.renderKey = renderKey;
  const openDetails = new Map(
    [...root.querySelectorAll("details[data-keep]")].map((e) => [
      e.dataset.keep,
      e.open,
    ]),
  );
  const previousLog = root.querySelector(".event-log");
  const logScroll = previousLog
    ? {
        top: previousLog.scrollTop,
        bottom:
          previousLog.scrollHeight -
            previousLog.scrollTop -
            previousLog.clientHeight <
          40,
      }
    : null;
  const oldEvents = root.querySelector("details[data-events]")?.open || false;
  const oldMatrix = root.querySelector("details[data-matrix]")?.open || false;
  const panel = node("section", "panel");
  const actions = node("div", "actions");
  const recoverable =
    !d.controller_active && ["pending", "running", "paused"].includes(d.status);
  const final = ["succeeded", "cancelled"].includes(d.status);
  if (recoverable)
    actions.append(
      button(
        d.cancel_requested ? "继续处理取消" : "恢复控制",
        () => control(d.cancel_requested ? "cancel" : "resume"),
        "button primary",
      ),
    );
  if (!d.controller_active && !final)
    actions.append(
      button("仅收集结果", () => control("fetch"), "button secondary"),
    );
  if (!final && d.status !== "failed" && !d.cancel_requested)
    actions.append(
      button("取消运行", () => control("cancel"), "button danger"),
    );
  if (view.control)
    actions.querySelectorAll("button").forEach((b) => (b.disabled = true));
  const archive = node("a", "button secondary", "下载完整归档");
  archive.href = url("/archive");
  archive.download = "";
  if (view.artifacts.length) actions.append(archive);
  panel.append(
    heading("运行详情", `${d.run_id}`, badge(d.status)),
    node(
      "p",
      "callout " + (["failed", "paused"].includes(d.status) ? "warning" : ""),
      outcomeHint(d),
    ),
    actions,
  );
  const stats = node("div", "stat-grid");
  const complete = d.jobs.filter((j) =>
    ["succeeded", "failed", "cancelled", "interrupted"].includes(j.status),
  ).length;
  const active = d.jobs.filter((j) =>
    [
      "preparing",
      "submitting",
      "running",
      "switching",
      "collecting",
      "stabilizing",
    ].includes(j.phase || j.status),
  ).length;
  const failed = d.jobs.filter((j) =>
    ["failed", "interrupted"].includes(j.status),
  ).length;
  const blocked = d.jobs.filter((j) => j.status === "blocked").length;
  for (const [label, n] of [
    ["已完成 / 总任务", `${complete} / ${d.jobs.length}`],
    ["进行中", active],
    ["失败", failed],
    ["阻塞", blocked],
  ]) {
    const box = node("div", "stat");
    box.append(node("strong", "", n), node("span", "", label));
    stats.append(box);
  }
  panel.append(stats);
  const current = d.jobs.filter((j) =>
    [
      "preparing",
      "submitting",
      "running",
      "switching",
      "collecting",
      "paused",
      "cancel_pending",
      "stabilizing",
    ].includes(j.phase || j.status),
  );
  if (current.length)
    panel.append(
      node(
        "p",
        "current-stage",
        current
          .map(
            (j) =>
              `${j.machine} / ${j.system}：${statusLabels[j.phase || j.status] || j.status}${j.wait_seconds !== undefined ? `（约 ${j.wait_seconds} 秒）` : ""}`,
          )
          .join("；"),
      ),
    );
  for (const job of d.jobs.filter((j) => j.reason))
    panel.append(
      node("p", "run-reason", `${job.machine} / ${job.system}：${job.reason}`),
    );
  panel.append(
    button(
      "复制完整运行 ID",
      async () => {
        try {
          await navigator.clipboard.writeText(d.run_id);
          show("运行 ID 已复制。");
        } catch {
          window.prompt("复制运行 ID", d.run_id);
        }
      },
      "text-button",
    ),
  );
  const matrix = node("details", "technical");
  matrix.dataset.matrix = "true";
  matrix.open = oldMatrix;
  matrix.append(node("summary", "", "按机器与系统查看任务"));
  const grouped = new Map();
  for (const job of d.jobs) {
    const key = job.machine + " / " + job.system;
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(job);
  }
  for (const [name, jobs] of grouped) {
    matrix.append(node("h3", "group-label", name));
    const scroll = node("div", "table-scroll");
    scroll.append(
      table(
        ["测试", "次数", "状态", "说明"],
        jobs.map((j) => [
          j.benchmark,
          j.repetition,
          badge(j.status),
          j.reason || "—",
        ]),
      ),
    );
    matrix.append(scroll);
  }
  panel.append(matrix);
  const events = node("details", "technical");
  events.dataset.events = "true";
  events.open = oldEvents;
  events.append(
    node("summary", "", "原始运行事件"),
    node(
      "pre",
      "event-log",
      view.events
        .map((event) => `${date(event.time)}  ${event.message}`)
        .join("\n") || "暂无事件",
    ),
  );
  panel.append(events);
  root.replaceChildren(panel, artifactPanel());
  root.querySelectorAll("details[data-keep]").forEach((e) => {
    if (openDetails.has(e.dataset.keep))
      e.open = openDetails.get(e.dataset.keep);
  });
  const log = root.querySelector(".event-log");
  if (log) {
    log.scrollTop =
      !logScroll || logScroll.bottom ? log.scrollHeight : logScroll.top;
  }
}
function artifactCategory(path) {
  if (/(^|\/)build\.(stdout|stderr)$/.test(path)) return "编译日志";
  if (/(^|\/)run\.(stdout|stderr)$/.test(path)) return "运行日志";
  if (path.includes("/results/") || /\.html?$/i.test(path)) return "测试报告";
  if (/environment|system-before|system-after|toolchain/.test(path))
    return "环境信息";
  return "其他文件";
}
function artifactPanel() {
  const panel = node("section", "panel");
  panel.append(heading("结果与日志", "选择文件预览，或下载原始结果。"));
  if (!view.artifacts.length) {
    panel.append(hint(view.artifactError || "暂无已回收文件。"));
    return panel;
  }
  for (const category of [
    "测试报告",
    "运行日志",
    "编译日志",
    "环境信息",
    "其他文件",
  ]) {
    const items = view.artifacts.filter(
      (item) => artifactCategory(item.path) === category,
    );
    if (!items.length) continue;
    const group = node("details", "artifact-group");
    group.dataset.keep = "category-" + category;
    group.open = ["测试报告", "运行日志"].includes(category);
    group.append(node("summary", "", `${category} · ${items.length}`));
    for (const item of items) {
      const r = node("div", "artifact-row");
      const names = node("div", "artifact-name");
      const basename = item.path.split("/").at(-1);
      names.append(node("strong", "", basename));
      const detail = node("details", "file-location");
      detail.dataset.keep = "path-" + item.path;
      detail.append(
        node("summary", "", `${Math.ceil(item.size / 1024)} KiB · 文件位置`),
        node("code", "", item.path),
      );
      names.append(detail);
      r.append(names);
      if (!/\.(html?|svg)$/i.test(item.path))
        r.append(button("预览", () => preview(item), "text-button"));
      const a = node("a", "text-button", "下载");
      a.href = url("/artifact", { path: item.path });
      a.download = basename;
      r.append(a);
      group.append(r);
    }
    panel.append(group);
  }
  return panel;
}
async function preview(item) {
  const token = view.token;
  const result = await api(
    url("/artifact", { path: item.path, preview: "true" }),
  );
  if (token !== view.token) return;
  $("preview-drawer").hidden = false;
  $("preview-title").textContent = item.path.split("/").at(-1);
  $("preview-path").textContent = item.path;
  $("artifact-preview").textContent =
    result.text +
    (result.truncated ? "\n\n仅预览前 256 KiB，请下载查看完整文件。" : "");
}
export function renderRuns() {
  renderList();
  renderDetail();
}
export async function initRuns() {
  view.directory = state.info.state_dir;
  $("run-search").addEventListener("input", () => {
    view.search = $("run-search").value;
    renderList();
  });
  $("run-filter").addEventListener("change", () => {
    view.filter = $("run-filter").value;
    renderList();
  });
  $("change-location").addEventListener("click", () =>
    perform($("change-location"), async () => {
      const path = await chooseFile(directory(), {
        kind: "directory",
        title: "选择历史状态目录",
      });
      if (path) await setDirectory(path);
    }),
  );
  $("use-config-location").addEventListener("click", () =>
    perform($("use-config-location"), () =>
      setDirectory(
        configStateDir(state.document, state.target, state.info.base_dir),
      ),
    ),
  );
  $("close-preview").addEventListener("click", () => {
    $("preview-drawer").hidden = true;
  });
  try {
    const saved = JSON.parse(
      sessionStorage.getItem("autobench-selection") || "null",
    );
    if (saved?.id) {
      view.directory = saved.stateDir;
      await selectRun(saved.id);
    }
  } catch {
    sessionStorage.removeItem("autobench-selection");
    view.selected = null;
  }
  setInterval(() => {
    if (state.page === "runs" && !document.hidden)
      refreshRuns().catch((error) => show(error.message, "error"));
  }, 2000);
  return view.selected;
}
