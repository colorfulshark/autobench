import { state, api, waitOperation, show } from "./state.js";
import { configStateDir, statusLabels, statusTone } from "./model.js";
import {
  $,
  node,
  icon,
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
  listSynced: 0,
  listScroll: 0,
  detail: null,
  detailError: "",
  detailSynced: 0,
  detailRequest: null,
  after: 0,
  events: [],
  eventError: "",
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
const pollInterval = 2000;
const terminalRunStatuses = ["succeeded", "failed", "cancelled"];
let pollTimer;
let pollDelay = pollInterval;
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
    view.listSynced = 0;
    view.listScroll = 0;
    view.listRequest = null;
  }
  view.directory = path;
}
function jobReason(job) {
  if (!["paused", "cancel_pending", "failed", "interrupted", "cancelled", "blocked"]
    .includes(job.phase || job.status)) return "";
  if (job.reason === "target boot verified after lost reboot acknowledgement; ready to resume")
    return "已确认目标系统启动，等待恢复控制。";
  const esp = job.reason?.match(
    /^cannot read the GRUB menu for (Boot[0-9A-F]{4}): the EFI system partition for \1 is not mounted$/,
  );
  if (esp) return `无法读取 ${esp[1]} 的 GRUB 菜单。目标 EFI 系统分区尚未挂载。`;
  return job.reason || "";
}
function selectRun(id, focus = false) {
  if (!view.selected && id && state.page === "runs") view.listScroll = window.scrollY;
  view.selected = id;
  view.detail = null;
  view.detailError = "";
  view.detailSynced = 0;
  view.report = null;
  view.renderKey = null;
  view.after = 0;
  view.events = [];
  view.eventError = "";
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
    renderDetail();
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
    view.listSynced = Date.now();
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
  view.detailSynced = Date.now();
  view.detailError = "";
  renderDetail();
  renderSelection();
  try {
    const events = await api(url("/events", { after: view.after }));
    if (token !== view.token) return;
    for (const event of events) {
      if (event.sequence <= view.after) continue;
      view.after = event.sequence;
      view.events.push(event);
    }
    view.events = view.events.slice(-5000);
    view.eventError = "";
  } catch (error) {
    if (token !== view.token) return;
    view.eventError = error.message;
  }
  updateEvents();
  if (Date.now() - view.artifactsAt > 8000 || terminalDetail()) {
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
  renderDetail();
}
function terminalDetail() {
  return view.detail && !view.detail.controller_active &&
    terminalRunStatuses.includes(view.detail.status);
}
function syncMessage(error, synced) {
  return synced ? `同步失败，数据可能已过期。上次同步：${date(synced / 1000)}。 ${error}` : error;
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
  $("run-count").textContent = view.listReady ? `${list.length} 次运行` : "运行记录";
  const root = $("runs-list");
  if (view.listError && !view.listReady) {
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
  const stale = view.listError
    ? node("p", "callout warning", syncMessage(view.listError, view.listSynced))
    : null;
  stale?.setAttribute("role", "status");
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
    if (stale) root.prepend(stale);
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
  if (stale) root.prepend(stale);
  if (focusedRun) focusRun(focusedRun);
}
function focusRun(id, preventScroll = false) {
  const row = [...$("runs-list").querySelectorAll("[data-run]")].find(
    (control) => control.dataset.run === id,
  );
  (row || $("run-search")).focus({ preventScroll });
}
function renderSelection() {
  const selected = Boolean(view.selected);
  $("run-selection").hidden = !selected;
  $("runs-overview").hidden = selected;
  $("header-runs").hidden = selected || state.page !== "runs";
  const status = $("run-detail-status");
  status.hidden = !selected || (Boolean(view.detail) && !view.detailError);
  const text = view.detailError
    ? syncMessage(view.detailError, view.detailSynced)
    : "正在加载运行详情…";
  if (status.textContent !== text) status.textContent = text;
  status.classList.toggle("warning", Boolean(view.detailError));
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
  const renderKey = JSON.stringify([d, view.control, Boolean(view.artifacts.length)]);
  const artifactKey = JSON.stringify([view.artifacts, view.artifactError]);
  if (view.renderKey === renderKey && view.artifactKey === artifactKey) return;
  if (view.renderKey === renderKey && view.report?.isConnected) {
    root.lastElementChild.replaceWith(artifactPanel(root.lastElementChild));
    view.artifactKey = artifactKey;
    return;
  }
  view.renderKey = renderKey;
  const oldMatrix = root.querySelector("details[data-matrix]")?.open || false;
  const previousTaskTables = view.report?.isConnected
    ? root.querySelector(".run-task-tables")
    : null;
  const taskScrollLeft = previousTaskTables?.scrollLeft ?? 0;
  const taskTablesFocused = previousTaskTables === document.activeElement;
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
  for (const controlButton of actions.querySelectorAll("button"))
    controlButton.dataset.runControl = controlButton.textContent;
  const oldFocus = document.activeElement;
  const actionName = root.contains(oldFocus) ? oldFocus.dataset.runControl : null;
  const matrixFocused = oldFocus.matches("details[data-matrix] > summary");
  const copyFocused = oldFocus.matches(".run-copy-id");
  if (view.control)
    actions.querySelectorAll("button").forEach((b) => (b.disabled = true));
  const archive = node("a", "button secondary", "下载完整归档");
  archive.prepend(icon("download"));
  archive.href = url("/archive");
  archive.download = "";
  if (view.artifacts.length) actions.append(archive);
  panel.append(
    heading("运行详情", d.run_id, badge(
      d.cancel_requested && d.controller_active && !terminalRunStatuses.includes(d.status)
        ? "cancel_pending" : d.status,
    ), "layers"),
    node(
      "p",
      "callout " + (["failed", "paused"].includes(d.status) ? "warning" : ""),
      outcomeHint(d),
    ),
    actions,
  );
  panel.querySelector(".callout").prepend(icon("info"));
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
  for (const [label, n, symbol] of [
    ["已结束 / 总任务", `${complete} / ${d.jobs.length}`, "circleCheck"],
    ["进行中", active, "timer"],
    ["失败", failed, "circleError"],
    ["阻塞", blocked, "pause"],
  ]) {
    const box = node("div", "stat");
    const figures = node("div", "stat-value");
    figures.append(node("strong", "", n), node("span", "", label));
    box.append(icon(symbol), figures);
    stats.append(box);
  }
  panel.append(stats);
  const progress = node("progress", "run-progress");
  if (d.jobs.length) {
    progress.max = d.jobs.length;
    progress.value = complete;
  }
  progress.setAttribute("aria-label", "已结束任务");
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
  for (const job of d.jobs) {
    const reason = jobReason(job);
    if (!reason) continue;
    const line = node("p", "run-reason", `${job.machine} / ${job.system}：${reason}`);
    line.dataset.tone = statusTone(job.phase || job.status);
    panel.append(line);
  }
  const copyId = button(
    "复制完整运行 ID",
    async () => {
      try {
        await navigator.clipboard.writeText(d.run_id);
        show("运行 ID 已复制。");
      } catch {
        window.prompt("复制运行 ID", d.run_id);
      }
    },
    "text-button run-copy-id",
  );
  copyId.prepend(icon("copy"));
  panel.append(copyId);
  const matrix = node("details", "technical");
  matrix.dataset.matrix = "true";
  matrix.open = oldMatrix;
  const matrixTitle = node("summary", "", "按机器与系统查看任务");
  matrixTitle.prepend(icon("server"));
  matrix.append(matrixTitle);
  const grouped = new Map();
  for (const job of d.jobs) {
    const key = job.machine + " / " + job.system;
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(job);
  }
  const taskTables = node("div", "table-scroll run-task-tables");
  taskTables.tabIndex = 0;
  taskTables.setAttribute("role", "region");
  taskTables.setAttribute("aria-label", "机器与系统任务表");
  for (const [name, jobs] of grouped) {
    const taskTable = table(
      ["测试", "次数", "状态", "说明"],
      jobs.map((j) => [
        j.benchmark,
        j.repetition,
        badge(j.status),
        jobReason(j) || "—",
      ]),
    );
    taskTable.classList.add("run-task-table");
    taskTables.append(node("h3", "group-label", name), taskTable);
  }
  if (grouped.size) matrix.append(taskTables);
  panel.append(matrix);
  if (view.report?.isConnected) {
    root.firstElementChild.replaceWith(panel);
    if (view.artifactKey !== artifactKey) {
      const previous = root.lastElementChild;
      previous.replaceWith(artifactPanel(previous));
    }
  } else {
    view.report = createReportPanel(url("/report"));
    root.replaceChildren(panel, eventPanel(), view.report, artifactPanel());
  }
  taskTables.scrollLeft = taskScrollLeft;
  if (taskTablesFocused) taskTables.focus({ preventScroll: true });
  if (matrixFocused) matrixTitle.focus({ preventScroll: true });
  if (copyFocused) copyId.focus({ preventScroll: true });
  if (actionName) {
    const replacement = [...actions.querySelectorAll("[data-run-control]")].find(
      (controlButton) => controlButton.dataset.runControl === actionName && !controlButton.disabled,
    );
    (replacement || copyId).focus({ preventScroll: true });
  }
  view.artifactKey = artifactKey;
}
function eventPanel() {
  const events = node("details", "panel technical run-events");
  events.dataset.events = "true";
  const title = node("summary", "", "原始运行事件");
  title.prepend(icon("code"));
  const notice = node("p", "callout warning");
  notice.id = "run-event-notice";
  notice.setAttribute("role", "status");
  notice.hidden = true;
  const log = node("pre", "event-log", "正在加载运行事件…");
  log.tabIndex = 0;
  log.setAttribute("aria-label", "运行事件日志");
  const follow = button("回到底部", () => {
    log.scrollTop = log.scrollHeight;
    log.focus({ preventScroll: true });
  }, "text-button");
  follow.id = "follow-events";
  follow.hidden = true;
  log.addEventListener("scroll", () => { follow.hidden = nearLogEnd(log); });
  events.addEventListener("toggle", () => {
    if (events.open && follow.hidden) log.scrollTop = log.scrollHeight;
  });
  events.append(title, notice, log, follow);
  return events;
}
const nearLogEnd = (log) => log.scrollHeight - log.scrollTop - log.clientHeight < 24;
function updateEvents() {
  const log = $("run-detail").querySelector(".event-log");
  const notice = $("run-event-notice");
  if (!log || !notice) return;
  notice.hidden = !view.eventError;
  if (notice.textContent !== view.eventError) notice.textContent = view.eventError;
  if (view.eventError && !log.dataset.lastSequence) {
    log.textContent = "尚未读取运行事件";
    return;
  }
  const lastSequence = Number(log.dataset.lastSequence || 0);
  if (lastSequence !== view.after || !log.dataset.lastSequence) {
    const selection = window.getSelection();
    const selecting = selection && !selection.isCollapsed && log.contains(selection.anchorNode);
    const follow = nearLogEnd(log) && !selecting;
    const format = (event) => `${date(event.time)}  ${event.message}\n`;
    if (lastSequence && view.events[0]?.sequence <= lastSequence && !(
      log.dataset.firstSequence && Number(log.dataset.firstSequence) < view.events[0].sequence
    )) {
      log.append(view.events.filter((event) => event.sequence > lastSequence).map(format).join(""));
    } else {
      const scrollTop = log.scrollTop;
      log.textContent = view.events.map(format).join("") || "暂无事件";
      log.scrollTop = scrollTop;
    }
    log.dataset.firstSequence = view.events[0]?.sequence || 0;
    log.dataset.lastSequence = view.after;
    if (follow) log.scrollTop = log.scrollHeight;
    $("follow-events").hidden = nearLogEnd(log);
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
  row.append(icon("file"), names);
  if (!/\.(html?|svg)$/i.test(item.path)) {
    const open = button("预览", () => preview(item), "text-button");
    open.prepend(icon("eye"));
    open.dataset.previewPath = item.path;
    row.append(open);
  }
  const download = node("a", "text-button", "下载");
  download.prepend(icon("download"));
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
    const label = node("summary", "", name);
    label.prepend(icon(depth === 0 ? "server" : depth < 3 ? "cube" : "folder"));
    folder.append(
      label,
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
  const panel = node("section", "panel artifact-panel");
  panel.append(heading("结果与日志", null, null, "layers"));
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
function renderPreviewPath(path, parts) {
  const breadcrumb = $("preview-path");
  const directoryIcons = ["server", "folder", "cube"];
  breadcrumb.title = path;
  breadcrumb.replaceChildren(...parts.map((part, index) => {
    const segment = node("span", "preview-path-segment");
    if (index) {
      const slash = node("span", "visually-hidden", "/");
      slash.setAttribute("aria-hidden", "true");
      segment.append(slash, icon("chevronRight", "icon preview-path-separator"));
    }
    const symbol = index === parts.length - 1 ? "file" : directoryIcons[index] || "folder";
    segment.append(icon(symbol), node("span", "", part));
    return segment;
  }));
}
async function preview(item) {
  const token = ++view.previewToken;
  const result = await api(
    url("/artifact", { path: item.path, preview: "true" }),
  );
  if (token !== view.previewToken) return;
  view.previewPath = item.path;
  const parts = item.path.split("/");
  $("preview-title").textContent = parts.at(-1);
  renderPreviewPath(item.path, parts);
  $("artifact-preview").textContent =
    result.text +
    (result.truncated ? "\n\n仅预览前 256 KiB，请下载查看完整文件。" : "");
  const drawer = $("preview-drawer");
  drawer.showModal();
  drawer.scrollTop = 0;
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
    window.scrollTo(0, view.listScroll);
    focusRun(id, true);
    refreshRuns();
  });
  const search = $("run-search");
  function applySearch() {
    view.search = search.value;
    renderList();
  }
  search.addEventListener("input", (event) => {
    if (!event.isComposing) applySearch();
  });
  search.addEventListener("compositionend", applySearch);
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
  $("preview-file-icon").append(icon("file"));
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
  async function poll() {
    if (state.page === "runs" && !document.hidden) {
      if (!view.selected) await refreshRuns();
      else if (!terminalDetail() || view.detailError || view.eventError)
        await refreshDetail();
      const failed = view.selected ? view.detailError || view.eventError : view.listError;
      pollDelay = failed ? Math.min(pollDelay * 2, 30000) : pollInterval;
    }
    clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, pollDelay);
  }
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && state.page === "runs") poll();
  });
  pollTimer = setTimeout(poll, pollInterval);
  return view.selected;
}
