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
import { createReportPanel } from "./reports.js";
const view = {
  directory: "",
  selected: null,
  list: [],
  listKey: null,
  listReady: false,
  listError: "",
  detail: null,
  detailError: "",
  detailRequest: null,
  after: 0,
  events: [],
  artifacts: [],
  artifactKey: null,
  artifactError: "",
  artifactsAt: 0,
  listRequest: null,
  control: false,
  search: "",
  filter: "",
  token: 0,
  renderKey: null,
  report: null,
  previewToken: 0,
  previewPath: null,
};
export const directory = () => view.directory || state.info.state_dir;
const query = (extra) =>
  new URLSearchParams({ state_dir: directory(), ...extra });
function url(suffix = "", extra = {}) {
  return `/api/runs/${encodeURIComponent(view.selected)}${suffix}?${query(extra)}`;
}
function storage() {
  sessionStorage.setItem("autobench-run-directory", directory());
  if (view.selected)
    sessionStorage.setItem(
      "autobench-selection",
      JSON.stringify({ id: view.selected, stateDir: directory() }),
    );
  else sessionStorage.removeItem("autobench-selection");
}
function changeDirectory(path) {
  if (path !== directory()) {
    view.list = [];
    view.listReady = false;
    view.listError = "";
    view.listRequest = null;
  }
  view.directory = path;
}
function selectRun(id, focus = false) {
  view.selected = id;
  view.detail = null;
  view.detailError = "";
  view.report = null;
  view.renderKey = null;
  view.after = 0;
  view.events = [];
  view.artifacts = [];
  view.artifactKey = null;
  view.artifactError = "";
  view.artifactsAt = 0;
  view.token++;
  view.detailRequest = null;
  storage();
  closePreview();
  renderRuns();
  if (focus) {
    window.scrollTo(0, 0);
    $("back-to-runs").focus({ preventScroll: true });
  }
  if (id) return refreshDetail();
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
  changeDirectory(result.state_dir);
  selectRun(result.run_id);
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
  if (view.listRequest) return;
  const request = api("/api/runs?" + query());
  view.listRequest = request;
  try {
    const list = await request;
    if (view.listRequest !== request) return;
    view.list = list;
    view.listReady = true;
    view.listError = "";
  } catch (error) {
    if (view.listRequest !== request) return;
    view.listError = error.message;
  } finally {
    if (view.listRequest === request) view.listRequest = null;
  }
  renderList();
  if (view.selected) await refreshDetail();
}
function refreshDetail() {
  if (!view.selected) return;
  if (view.detailRequest) return view.detailRequest;
  const token = view.token;
  view.detailRequest = readDetail(token)
    .catch((error) => {
      if (token !== view.token) return;
      view.detailError = error.message;
      renderSelection();
    })
    .finally(() => {
      if (token === view.token) view.detailRequest = null;
    });
  return view.detailRequest;
}
async function readDetail(token) {
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
      if (token !== view.token) return;
      view.artifactError =
        error.status === 404
          ? "结果尚未回收，可在任务封存后收集。"
          : error.message;
    }
  }
  view.detailError = "";
  renderDetail();
  renderSelection();
}
function renderList() {
  $("state-location").textContent = directory();
  $("state-location").title = directory();
  const list = view.list.filter(
    (run) =>
      (!view.filter || run.status === view.filter) &&
      (!view.search ||
        `${run.id} ${date(run.created)} ${statusLabels[run.status] || run.status}`
          .toLowerCase()
          .includes(view.search.toLowerCase())),
  );
  const key = JSON.stringify([
    view.listReady,
    view.listError,
    list,
    view.search,
    view.filter,
  ]);
  if (key === view.listKey) return;
  view.listKey = key;
  $("run-count").textContent = view.listReady ? `${list.length} 次运行` : "";
  const root = $("runs-list");
  if (view.listError) {
    root.replaceChildren(
      empty(
        "无法加载运行记录",
        view.listError,
        button("重试", refreshRuns, "button secondary"),
      ),
    );
    return;
  }
  if (!view.listReady) {
    const message = node("p", "callout", "正在读取运行记录…");
    message.setAttribute("role", "status");
    root.replaceChildren(message);
    return;
  }
  if (!list.length) {
    const filtered = Boolean(view.search || view.filter);
    root.replaceChildren(
      empty(
        filtered ? "没有匹配的运行记录" : "此目录暂无运行记录",
        filtered
          ? "可以清除筛选条件查看全部运行。"
          : "从测试配置开始一次测试，或切换到已有记录的目录。",
        filtered
          ? button(
              "清除筛选",
              () => {
                $("run-search").value = view.search = "";
                $("run-filter").value = view.filter = "";
                renderList();
                $("run-search").focus();
              },
              "button secondary",
            )
          : button(
              "前往测试配置",
              () => {
                $("tab-config").click();
                $("tab-config").focus();
              },
              "button secondary",
            ),
      ),
    );
    return;
  }
  const focusedRun =
    root.contains(document.activeElement) && document.activeElement.dataset.run;
  root.replaceChildren(
    table(
      ["创建时间", "状态", "最近更新", "操作"],
      list.map((run) => {
        const title = node("div");
        title.append(
          node("strong", "", date(run.created)),
          node("div", "secondary mono", run.id),
        );
        const open = button(
          "查看详情",
          () => selectRun(run.id, true),
          "text-button",
        );
        open.dataset.run = run.id;
        return [title, badge(run.status), date(run.updated), open];
      }),
    ),
  );
  if (focusedRun) focusRun(focusedRun);
}
function focusRun(id) {
  const row = [...$("runs-list").querySelectorAll("[data-run]")].find(
    (control) => control.dataset.run === id,
  );
  (row || $("run-search")).focus();
}
function renderSelection() {
  const selected = Boolean(view.selected);
  $("run-selection").hidden = !selected;
  $("runs-overview").hidden = selected;
  $("header-runs").hidden = selected || state.page !== "runs";
  const status = $("run-detail-status");
  status.hidden = !selected || (Boolean(view.detail) && !view.detailError);
  status.textContent = view.detailError || "正在加载运行详情…";
  status.classList.toggle("error", Boolean(view.detailError));
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
  const renderKey = JSON.stringify([d, view.after, view.control]);
  const artifactKey = JSON.stringify([view.artifacts, view.artifactError]);
  if (view.renderKey === renderKey && view.artifactKey === artifactKey) return;
  view.renderKey = renderKey;
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
  const panel = node("section", "panel run-summary");
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
      "building",
      "warming_up",
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
    ["已结束 / 总任务", `${complete} / ${d.jobs.length}`],
    ["进行中", active],
    ["失败", failed],
    ["阻塞", blocked],
  ]) {
    const box = node("div", "stat");
    box.append(node("strong", "", n), node("span", "", label));
    stats.append(box);
  }
  panel.append(stats);
  const progress = node("progress", "run-progress");
  progress.max = d.jobs.length || 1;
  progress.value = complete;
  progress.setAttribute("aria-label", "已完成任务");
  panel.append(progress);
  const current = d.jobs.filter((j) =>
    [
      "preparing",
      "building",
      "warming_up",
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
  if (view.report?.isConnected) {
    root.firstElementChild.replaceWith(panel);
    if (view.artifactKey !== artifactKey) {
      const previous = root.lastElementChild;
      previous.replaceWith(artifactPanel(previous));
    }
  } else {
    view.report = createReportPanel(url("/report"));
    root.replaceChildren(panel, view.report, artifactPanel());
  }
  view.artifactKey = artifactKey;
  const log = root.querySelector(".event-log");
  if (log) {
    log.scrollTop =
      !logScroll || logScroll.bottom ? log.scrollHeight : logScroll.top;
  }
}
function artifactRow(item, basename) {
  const row = node("li", "artifact-row");
  const names = node("div", "artifact-name");
  const detail = node("details", "file-location");
  detail.dataset.keep = "path-" + item.path;
  detail.append(
    node("summary", "", `${Math.ceil(item.size / 1024)} KiB · 文件位置`),
    node("code", "", item.path),
  );
  names.append(node("strong", "", basename), detail);
  row.append(names);
  if (!/\.(html?|svg)$/i.test(item.path)) {
    const open = button("预览", () => preview(item), "text-button");
    open.dataset.previewPath = item.path;
    row.append(open);
  }
  const download = node("a", "text-button", "下载");
  download.href = url("/artifact", { path: item.path });
  download.download = basename;
  row.append(download);
  return row;
}
function renderArtifactTree(directory, parentPath = "", depth = 0) {
  const list = node("ul", "artifact-tree");
  list.setAttribute("role", "list");
  for (const [name, child] of directory.directories) {
    const path = parentPath ? `${parentPath}/${name}` : name;
    const item = node("li");
    const folder = node("details", "artifact-folder");
    folder.dataset.keep = "folder-" + path;
    folder.open = depth < 2;
    folder.append(
      node("summary", "", name),
      renderArtifactTree(child, path, depth + 1),
    );
    item.append(folder);
    list.append(item);
  }
  for (const { item, basename } of directory.files) {
    list.append(artifactRow(item, basename));
  }
  return list;
}
function artifactPanel(previous) {
  const panel = node("section", "panel");
  panel.append(heading("结果与日志"));
  if (!view.artifacts.length) {
    panel.append(hint(view.artifactError || "暂无已回收文件。"));
    return panel;
  }
  const tree = { directories: new Map(), files: [] };
  for (const item of view.artifacts) {
    const parts = item.path.split("/");
    const basename = parts.pop();
    let directory = tree;
    for (const name of parts) {
      if (!directory.directories.has(name)) {
        directory.directories.set(name, { directories: new Map(), files: [] });
      }
      directory = directory.directories.get(name);
    }
    directory.files.push({ item, basename });
  }
  panel.append(renderArtifactTree(tree));
  if (previous) {
    const openDetails = new Map(
      [...previous.querySelectorAll("details[data-keep]")].map((detail) => [
        detail.dataset.keep,
        detail.open,
      ]),
    );
    for (const detail of panel.querySelectorAll("details[data-keep]")) {
      if (openDetails.has(detail.dataset.keep)) {
        detail.open = openDetails.get(detail.dataset.keep);
      }
    }
  }
  return panel;
}
async function preview(item) {
  const token = ++view.previewToken;
  const result = await api(
    url("/artifact", { path: item.path, preview: "true" }),
  );
  if (token !== view.previewToken) return;
  view.previewPath = item.path;
  $("preview-title").textContent = item.path.split("/").at(-1);
  $("preview-path").textContent = item.path;
  $("artifact-preview").textContent =
    result.text +
    (result.truncated ? "\n\n仅预览前 256 KiB，请下载查看完整文件。" : "");
  $("preview-drawer").showModal();
  $("preview-content").scrollTop = 0;
}
export function closePreview(restoreFocus = false) {
  view.previewToken++;
  $("preview-drawer").close();
  if (restoreFocus) {
    const trigger = [
      ...$("run-detail").querySelectorAll("[data-preview-path]"),
    ].find((element) => element.dataset.previewPath === view.previewPath);
    (trigger || $("back-to-runs")).focus({ preventScroll: true });
  }
  view.previewPath = null;
}
export function renderRuns() {
  renderSelection();
  renderList();
  renderDetail();
}
export async function initRuns() {
  view.directory =
    sessionStorage.getItem("autobench-run-directory") || state.info.state_dir;
  $("back-to-runs").addEventListener("click", () => {
    const id = view.selected;
    selectRun(null);
    window.scrollTo(0, 0);
    focusRun(id);
  });
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
      if (path) {
        changeDirectory(path);
        selectRun(null);
        await refreshRuns();
      }
    }),
  );
  $("use-config-location").addEventListener("click", () =>
    perform($("use-config-location"), () => {
      changeDirectory(
        configStateDir(state.document, state.target, state.info.base_dir),
      );
      selectRun(null);
      return refreshRuns();
    }),
  );
  $("close-preview").addEventListener("click", () => closePreview(true));
  const drawer = $("preview-drawer");
  drawer.addEventListener("cancel", (event) => {
    event.preventDefault();
    closePreview(true);
  });
  const isBackdrop = (event) =>
    event.target === drawer && event.clientX < drawer.getBoundingClientRect().left;
  let pressedBackdrop = false;
  drawer.addEventListener("pointerdown", (event) => {
    pressedBackdrop = isBackdrop(event);
  });
  drawer.addEventListener("click", (event) => {
    if (pressedBackdrop && isBackdrop(event)) closePreview(true);
    pressedBackdrop = false;
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
