import { at, patch, newProject, copy } from "./model.js";
export const configSteps = [
  "连接机器",
  "选择系统",
  "设置测试",
  "运行设置",
  "检查运行",
];
export const reviewStep = configSteps.length - 1;
export const state = {
  info: null,
  document: {},
  text: "",
  path: null,
  target: "",
  revision: null,
  dirty: true,
  touched: false,
  formDirty: false,
  version: 0,
  epoch: 0,
  mode: "form",
  page: "config",
  step: 0,
  machine: 0,
  system: null,
  benchmark: 0,
  validation: null,
  errors: [],
  opened: new Set(),
};
const listeners = new Set();
export const subscribe = (fn) => {
  listeners.add(fn);
  return () => listeners.delete(fn);
};
export function emit(layout = false) {
  for (const fn of listeners) fn(layout);
}
export function change(path, value, layout = false) {
  patch(state.document, path, value);
  state.errors = state.errors.filter(
    (error) => JSON.stringify(error.path) !== JSON.stringify(path),
  );
  dirty(layout);
}
export function dirty(layout = false) {
  state.touched = true;
  state.version++;
  state.dirty = true;
  state.formDirty = true;
  state.validation = null;
  if (layout) state.errors = [];
  emit(layout);
}
export function value(path, fallback = "") {
  return at(state.document, path) ?? fallback;
}
let noticeTimer, noticeHideTimer;
export function show(message, tone = "success") {
  clearTimeout(noticeTimer);
  clearTimeout(noticeHideTimer);
  const notice = document.getElementById("notice");
  notice.textContent = message;
  notice.dataset.tone = tone;
  notice.dataset.phase = "visible";
  notice.hidden = false;
  noticeTimer = setTimeout(() => {
    noticeTimer = undefined;
    notice.dataset.phase = "closing";
    const hide = () => {
      notice.hidden = true;
      notice.textContent = "";
      delete notice.dataset.phase;
      noticeHideTimer = undefined;
    };
    const duration =
      parseFloat(
        getComputedStyle(notice).getPropertyValue("--notice-exit-duration"),
      ) || 0;
    if (duration) noticeHideTimer = setTimeout(hide, duration);
    else hide();
  }, ["warning", "error"].includes(tone) ? 5000 : 3000);
}
export async function api(url, body) {
  let response;
  try {
    response = await fetch(
      url,
      body === undefined
        ? {}
        : {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          },
    );
  } catch {
    throw new Error("无法连接 Web 服务，请检查服务是否仍在运行。");
  }
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(
      typeof result.detail === "string" ? result.detail : "请求失败",
    );
    error.fields = result.errors || [];
    error.status = response.status;
    throw error;
  }
  return result;
}
export async function currentText() {
  if (state.mode === "yaml")
    return document.getElementById("yaml-editor").value;
  if (!state.formDirty) return state.text;
  return (await api("/api/config/render", { document: state.document })).text;
}
export function accept(saved, { draft = false } = {}) {
  state.document = saved.document || {};
  state.text = saved.text || "";
  state.path = saved.path || null;
  state.target = saved.path || state.info.base_dir + "/autobench.yaml";
  state.revision = saved.revision || null;
  state.dirty = draft;
  state.touched = draft;
  if (saved.path) sessionStorage.setItem("autobench-config-path", saved.path);
  else sessionStorage.removeItem("autobench-config-path");
  state.formDirty = false;
  state.version++;
  state.epoch++;
  state.errors = [];
  state.validation = null;
  state.step = 0;
  state.machine = 0;
  state.system = null;
  state.benchmark = 0;
  state.opened.clear();
  if (saved.document) state.mode = "form";
  if (!saved.document) {
    state.mode = "yaml";
    show(saved.parse_error || "配置结构无法显示，请在 YAML 中修正。", "error");
  }
  document.getElementById("yaml-editor").value = state.text;
  emit(true);
}
export async function createProject() {
  state.mode = "form";
  const document = newProject();
  const rendered = await api("/api/config/render", { document });
  accept(rendered, { draft: true });
  state.touched = false;
}
export async function load(path) {
  const version = state.version;
  const result = await api("/api/config/load", { path });
  if (version !== state.version)
    throw new Error("加载期间配置有新修改，请保存后重新加载。");
  accept(result);
}
export async function save(target = state.target) {
  if (!target?.trim()) throw new Error("请选择配置在服务器上的保存位置。");
  const version = state.version,
    epoch = state.epoch;
  const text = await currentText();
  if (version !== state.version)
    throw new Error("配置已继续编辑，请重新保存。");
  let result;
  try {
    result = await api("/api/config/save", {
      path: target,
      text,
      revision: target === state.path ? state.revision : null,
    });
  } catch (error) {
    if (version === state.version) {
      state.errors = error.fields || [];
      emit();
    }
    throw error;
  }
  if (epoch !== state.epoch) {
    show("原配置已保存，当前编辑的其他配置已保留。");
    return false;
  }
  sessionStorage.setItem("autobench-config-path", result.path);
  state.path = result.path;
  state.target = result.path;
  state.revision = result.revision;
  if (version !== state.version) {
    emit();
    show("已保存提交时的版本，后续修改尚未保存。", "warning");
    return false;
  }
  state.text = result.text;
  if (state.mode === "yaml") state.document = result.document;
  state.formDirty = false;
  state.dirty = false;
  state.errors = [];
  document.getElementById("yaml-editor").value = result.text;
  emit();
  show("配置已保存。");
  return true;
}
export function savedBody() {
  if (state.dirty || !state.path || state.target !== state.path)
    throw new Error("请先保存当前配置。");
  return { path: state.path, revision: state.revision };
}
export async function validate() {
  const version = state.version;
  try {
    const result = await api("/api/config/validate", savedBody());
    if (version !== state.version) {
      show("配置已继续修改，请重新检查。", "warning");
      return false;
    }
    state.validation = { ...result, version };
    state.errors = [];
    emit(true);
    show("配置检查通过，可以开始测试。");
    return true;
  } catch (error) {
    if (version === state.version) {
      state.errors = error.fields?.length
        ? error.fields
        : [{ path: [], message: error.message }];
      emit(true);
    }
    throw error;
  }
}
export async function switchMode(mode) {
  if (mode === state.mode) return;
  const version = state.version;
  if (mode === "yaml") {
    const text = await currentText();
    if (version !== state.version) throw new Error("内容已改变，请重新切换。");
    state.text = text;
    state.formDirty = false;
    document.getElementById("yaml-editor").value = text;
  } else {
    const text = document.getElementById("yaml-editor").value;
    const parsed = await api("/api/config/parse", { text });
    if (version !== state.version) throw new Error("内容已改变，请重新切换。");
    state.document = parsed.document;
    state.text = text;
    state.formDirty = false;
  }
  state.mode = mode;
  emit(true);
}
export function sourceChanged(text) {
  state.touched = true;
  state.text = text;
  state.version++;
  state.dirty = true;
  state.formDirty = false;
  state.validation = null;
  state.errors = [];
  emit();
}
export const discard = () =>
  !state.dirty ||
  !state.touched ||
  window.confirm("当前修改尚未保存，确定放弃这些修改吗？");
export function targetChanged(path) {
  if (path === state.target) return;
  state.target = path;
  state.version++;
  state.validation = null;
  emit(true);
}
export async function waitOperation(operation, onProgress) {
  while (operation.status === "running") {
    if (onProgress) onProgress(operation);
    await new Promise((resolve) => setTimeout(resolve, 600));
    operation = await api("/api/operations/" + operation.id);
  }
  if (operation.status === "failed")
    throw new Error(operation.error || "操作失败");
  if (operation.status === "stopped")
    throw new Error("控制服务已停止，请重新执行检查。");
  return operation.result;
}
export const documentCopy = () => copy(state.document);
