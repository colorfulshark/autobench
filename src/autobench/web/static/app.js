"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  info: null,
  document: {},
  text: "",
  path: null,
  revision: null,
  dirty: true,
  mode: "form",
  formDirty: false,
  editVersion: 0,
  documentEpoch: 0,
  page: "config",
  run: null,
  after: 0,
  eventLines: [],
  refreshBusy: false,
  watched: new Set(),
  artifactsAt: 0,
};
const labels = {
  item: "项目",
  version: "配置版本",
  machines: "测试机器",
  benchmarks: "性能测试",
  policy: "调度策略",
  id: "标识",
  ssh: "SSH 连接",
  systems: "操作系统",
  host: "主机地址",
  port: "端口",
  username: "用户名",
  password: "密码",
  client_keys: "私钥文件",
  agent: "使用 SSH Agent",
  known_hosts: "主机密钥文件",
  host_key_alias: "主机密钥别名",
  proxy: "SOCKS5 代理",
  remote_dir: "远端工作目录",
  boot_entry: "UEFI 启动项",
  machine_id: "系统 Machine ID",
  env: "测试环境变量",
  type: "测试类型",
  source_archive: "本地源码包",
  source_subdir: "源码子目录",
  source_sha256: "源码 SHA256",
  make_args: "编译参数",
  args: "运行参数",
  repetitions: "重复次数",
  build_timeout: "编译超时（秒）",
  timeout: "运行超时（秒）",
  parallel_machines: "并行机器数",
  poll_interval: "轮询间隔（秒）",
  settle_seconds: "启动后稳定等待（秒）",
  reconnect_timeout: "重连超时（秒）",
  boot_timeout: "系统启动超时（秒）",
  connect_timeout: "SSH 连接超时（秒）",
  state_dir: "本地状态目录",
  output_dir: "结果输出目录",
};
const statusLabels = {
  pending: "待执行",
  running: "运行中",
  succeeded: "成功",
  failed: "失败",
  paused: "已暂停",
  cancelled: "已取消",
  cancel_pending: "等待取消",
  blocked: "已阻塞",
  interrupted: "已中断",
  collecting: "收集中",
  building: "编译中",
  staging: "准备中",
  preparing: "准备中",
  submitting: "提交中",
  switching: "切换系统",
  submitted: "已提交",
  stopped: "控制端已停止",
  completed: "完成",
};
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}
function notify(message, error = false) {
  $("notice").textContent = message;
  $("notice").className = error ? "error" : "";
  $("notice").hidden = false;
}
async function api(path, body, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    ...(body === undefined
      ? {}
      : { method: "POST", body: JSON.stringify(body) }),
  });
  const result = await response.json();
  if (!response.ok)
    throw new Error(
      typeof result.detail === "string"
        ? result.detail
        : "请求失败，请检查输入",
    );
  return result;
}
function action(id, fn) {
  $(id).addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.classList.add("busy");
    try {
      await fn(event);
    } catch (error) {
      notify(error.message, true);
    } finally {
      button.classList.remove("busy");
      button.disabled = false;
      updateReady();
    }
  });
}
function has(object, key) {
  return (
    object !== null && typeof object === "object" && Object.hasOwn(object, key)
  );
}
function get(path) {
  return path.reduce(
    (value, key) => (has(value, key) ? value[key] : undefined),
    state.document,
  );
}
function assign(object, key, value) {
  Object.defineProperty(object, key, {
    value,
    writable: true,
    enumerable: true,
    configurable: true,
  });
}
function set(path, value) {
  const parent = get(path.slice(0, -1));
  if (value === undefined) delete parent[path.at(-1)];
  else assign(parent, path.at(-1), value);
  markDirty();
}
function resolve(schema) {
  if (schema.$ref)
    return {
      ...state.info.schema.$defs[schema.$ref.split("/").at(-1)],
      ...schema,
      $ref: undefined,
    };
  return schema;
}
function actual(schema) {
  schema = resolve(schema);
  return schema.anyOf
    ? resolve(schema.anyOf.find((item) => item.type !== "null") || {})
    : schema;
}
function nullable(schema) {
  return resolve(schema).anyOf?.some((item) => item.type === "null");
}
function seed(schema) {
  const source = resolve(schema);
  schema = actual(schema);
  if (has(source, "default") && source.default !== null)
    return structuredClone(source.default);
  if (schema.const !== undefined) return schema.const;
  if (schema.enum) return schema.enum[0];
  if (schema.type === "object") {
    const value = {};
    for (const key of schema.required || [])
      assign(value, key, seed(schema.properties[key]));
    return value;
  }
  if (schema.type === "array")
    return Array.from({ length: schema.minItems || 0 }, () =>
      seed(schema.items),
    );
  if (schema.type === "boolean") return false;
  if (["integer", "number"].includes(schema.type))
    return (
      schema.minimum ??
      (schema.exclusiveMinimum !== undefined ? schema.exclusiveMinimum + 1 : 1)
    );
  return "";
}
function updateReady() {
  const ready = Boolean(
    state.path && !state.dirty && $("config-path").value === state.path,
  );
  document.querySelectorAll(".requires-saved").forEach((button) => {
    if (!button.classList.contains("busy")) button.disabled = !ready;
  });
  $("save-state").textContent = ready ? "已保存" : "未保存";
  $("save-state").className = `badge${ready ? "" : " dirty"}`;
  $("execution-hint").textContent = ready
    ? "配置已保存，可执行校验、诊断和测试。"
    : "请先保存配置，再执行校验、诊断或测试。";
  $("inspection-config").textContent = ready
    ? state.path
    : "请先在配置页面保存当前配置。";
}
function updateSummary() {
  const machines = Array.isArray(state.document.machines)
    ? state.document.machines
    : [];
  const benchmarks = Array.isArray(state.document.benchmarks)
    ? state.document.benchmarks
    : [];
  const systems = machines.reduce(
    (total, machine) =>
      total + (Array.isArray(machine?.systems) ? machine.systems.length : 0),
    0,
  );
  $("matrix-summary").textContent =
    `${machines.length} 台机器 · ${systems} 个系统 · ${benchmarks.length} 项测试`;
  const selected = $("inspection-machine").value;
  $("inspection-machine").replaceChildren(new Option("全部机器", ""));
  for (const machine of machines)
    if (typeof machine?.id === "string")
      $("inspection-machine").add(new Option(machine.id, machine.id));
  $("inspection-machine").value = selected;
}
function markDirty(formChanged = true) {
  state.editVersion++;
  state.dirty = true;
  state.formDirty ||= formChanged;
  updateReady();
  updateSummary();
}
function smallButton(text, title, fn) {
  const button = el("button", "small", text);
  button.type = "button";
  button.title = title;
  button.setAttribute("aria-label", title);
  button.addEventListener("click", fn);
  return button;
}
function infer(value) {
  if (Array.isArray(value)) return { type: "array", items: {} };
  if (value !== null && typeof value === "object")
    return { type: "object", additionalProperties: {} };
  return {
    type:
      typeof value === "number"
        ? "number"
        : typeof value === "boolean"
          ? "boolean"
          : "string",
  };
}
function field(name, rawSchema, path, required = false) {
  const source = resolve(rawSchema);
  let schema = actual(rawSchema);
  const value = get(path);
  if (!schema.type && !schema.properties) schema = infer(value);
  const node = el("div", "field");
  const header = el("div", "field-header");
  const label = el("label", "field-name", labels[name] || name);
  const controlId = `field-${path.map(encodeURIComponent).join("-")}`;
  label.htmlFor = controlId;
  header.append(label, el("span", "field-key", name));
  const mode = el("select", "field-mode");
  mode.setAttribute("aria-label", `${labels[name] || name}设置方式`);
  mode.add(new Option(required ? "待填写（必填）" : "继承 / 未填写", "unset"));
  mode.add(new Option("指定值", "value"));
  const inheritedSSH = path.at(-2) === "ssh" && path.includes("systems");
  const canClear =
    nullable(rawSchema) &&
    (!inheritedSSH || ["password", "host_key_alias", "proxy"].includes(name));
  if (canClear || value === null)
    mode.add(new Option("显式清除（null）", "null"));
  mode.value =
    value === undefined ? "unset" : value === null ? "null" : "value";
  mode.addEventListener("change", () => {
    set(
      path,
      mode.value === "unset"
        ? undefined
        : mode.value === "null"
          ? null
          : seed(rawSchema),
    );
    renderForm();
  });
  header.append(mode);
  node.append(header);
  if (value === undefined || value === null) {
    let hint =
      value === null
        ? "显式写入 null，不继承上层设置。"
        : required
          ? "请选择指定值并填写。"
          : "未写入配置，使用默认值或继承机器设置。";
    if (value === undefined && has(source, "default"))
      hint += ` 默认：${JSON.stringify(source.default)}`;
    node.append(el("div", "hint", hint));
    return node;
  }
  const mismatched =
    (schema.type === "object" &&
      (typeof value !== "object" || Array.isArray(value))) ||
    (schema.type === "array" && !Array.isArray(value));
  if (mismatched) {
    node.append(
      el(
        "div",
        "issue",
        "当前字段类型不符合表单结构，请在 YAML 中修正，或重新选择指定值。",
      ),
    );
    node.append(el("pre", "", JSON.stringify(value, null, 2)));
    return node;
  }
  if (schema.type === "object" || schema.properties) {
    const children = el("div", "object-fields");
    if (schema.properties) {
      const keys = new Set([
        ...Object.keys(schema.properties),
        ...Object.keys(value),
      ]);
      for (const key of keys)
        children.append(
          field(
            key,
            schema.properties[key] || infer(value[key]),
            [...path, key],
            schema.required?.includes(key),
          ),
        );
    } else {
      for (const key of Object.keys(value)) {
        const row = el("div", "map-row");
        const keyInput = el("input");
        keyInput.value = key;
        keyInput.placeholder = "变量名";
        keyInput.setAttribute("aria-label", `${labels[name] || name}名称`);
        keyInput.addEventListener("change", () => {
          if (keyInput.value !== key && has(value, keyInput.value)) {
            keyInput.value = key;
            notify("名称已存在", true);
            return;
          }
          const item = value[key];
          delete value[key];
          assign(value, keyInput.value, item);
          markDirty();
          renderForm();
        });
        const valueInput = el("input");
        valueInput.value =
          typeof value[key] === "string"
            ? value[key]
            : JSON.stringify(value[key]);
        valueInput.setAttribute("aria-label", `${key}的值`);
        valueInput.addEventListener("input", () => {
          assign(value, key, valueInput.value);
          markDirty();
        });
        row.append(
          keyInput,
          valueInput,
          smallButton("×", `删除 ${key}`, () => {
            delete value[key];
            markDirty();
            renderForm();
          }),
        );
        children.append(row);
      }
      const add = el("button", "add-item", "＋ 添加键值");
      add.addEventListener("click", () => {
        let key = "NEW_KEY";
        while (has(value, key)) key += "_";
        assign(value, key, "");
        markDirty();
        renderForm();
      });
      children.append(add);
    }
    node.append(children);
  } else if (schema.type === "array") {
    value.forEach((item, index) => {
      const card = el("div", "array-item");
      const bar = el("div", "item-bar");
      bar.append(
        el(
          "strong",
          "",
          `${String(index + 1).padStart(2, "0")}  ${typeof item?.id === "string" ? item.id : labels[name] || name}`,
        ),
      );
      const up = smallButton("↑", "上移", () => {
        [value[index - 1], value[index]] = [value[index], value[index - 1]];
        markDirty();
        renderForm();
      });
      up.disabled = index === 0;
      const down = smallButton("↓", "下移", () => {
        [value[index + 1], value[index]] = [value[index], value[index + 1]];
        markDirty();
        renderForm();
      });
      down.disabled = index === value.length - 1;
      bar.append(
        up,
        down,
        smallButton("删除", `删除第 ${index + 1} 项`, () => {
          value.splice(index, 1);
          markDirty();
          renderForm();
        }),
      );
      card.append(
        bar,
        field("item", schema.items || infer(item), [...path, index], true),
      );
      node.append(card);
    });
    const add = el("button", "add-item", `＋ 添加${labels[name] || "项目"}`);
    add.addEventListener("click", () => {
      value.push(seed(schema.items || { type: "string" }));
      markDirty();
      renderForm();
    });
    node.append(add);
  } else {
    let input;
    if (schema.const !== undefined || schema.enum) {
      input = el("select");
      const values = schema.enum || [schema.const];
      if (!values.includes(value))
        input.add(new Option(String(value), String(value)));
      values.forEach((item) =>
        input.add(new Option(String(item), String(item))),
      );
      input.value = String(value);
      input.addEventListener("change", () =>
        set(
          path,
          values.find((item) => String(item) === input.value) ?? input.value,
        ),
      );
    } else if (schema.type === "boolean") {
      input = el("input");
      input.type = "checkbox";
      input.checked = Boolean(value);
      input.addEventListener("change", () => set(path, input.checked));
    } else {
      input = el("input");
      input.spellcheck = false;
      input.type = ["integer", "number"].includes(schema.type)
        ? "number"
        : name === "password"
          ? "password"
          : "text";
      input.value = String(value);
      input.autocomplete = name === "password" ? "new-password" : "off";
      if (input.type === "number") {
        input.step = schema.type === "integer" ? "1" : "any";
        if (schema.minimum !== undefined) input.min = schema.minimum;
        if (schema.maximum !== undefined) input.max = schema.maximum;
      }
      input.addEventListener("input", () =>
        set(
          path,
          input.type === "number" && input.value !== ""
            ? Number(input.value)
            : input.value,
        ),
      );
    }
    input.id = controlId;
    if (name === "password") {
      const control = el("div", "password-control");
      control.append(
        input,
        smallButton("显示", "显示或隐藏密码", (event) => {
          input.type = input.type === "password" ? "text" : "password";
          event.currentTarget.textContent =
            input.type === "password" ? "显示" : "隐藏";
        }),
      );
      node.append(control);
    } else node.append(input);
  }
  return node;
}
function renderForm() {
  const open = new Set(
    [...$("form-editor").querySelectorAll("details[open]")].map(
      (node) => node.dataset.key,
    ),
  );
  const initial = !$("form-editor").children.length;
  $("form-editor").replaceChildren();
  const properties = state.info.schema.properties;
  const keys = new Set([
    ...Object.keys(properties),
    ...Object.keys(state.document),
  ]);
  let index = 0;
  for (const key of keys) {
    const section = el("details", "form-section");
    section.dataset.key = key;
    section.open = initial ? key === "machines" : open.has(key);
    const heading = el("summary", "", labels[key] || key);
    heading.append(
      el(
        "span",
        "section-index",
        `${String(++index).padStart(2, "0")} / ${key.toUpperCase()}`,
      ),
    );
    const body = el("div", "section-body");
    body.append(
      field(
        key,
        properties[key] || infer(state.document[key]),
        [key],
        state.info.schema.required?.includes(key),
      ),
    );
    section.append(heading, body);
    $("form-editor").append(section);
  }
  updateSummary();
}
async function currentText() {
  if (state.mode === "yaml") return $("yaml-editor").value;
  if (!state.formDirty) return state.text;
  const value = await api("/api/config/render", { document: state.document });
  return value.text;
}
async function switchMode(mode) {
  if (state.mode === mode) return;
  const version = state.editVersion;
  if (mode === "yaml") {
    const text = await currentText();
    if (version !== state.editVersion)
      throw new Error("配置已继续编辑，请重新切换模式。");
    state.text = text;
    state.formDirty = false;
    $("yaml-editor").value = state.text;
  } else {
    const text = $("yaml-editor").value;
    const value = await api("/api/config/parse", { text });
    if (version !== state.editVersion)
      throw new Error("配置已继续编辑，请重新切换模式。");
    state.text = text;
    state.document = value.document;
    renderForm();
  }
  state.mode = mode;
  $("form-editor").hidden = mode !== "form";
  $("yaml-panel").hidden = mode !== "yaml";
  for (const item of ["form", "yaml"]) {
    $("mode-" + item).classList.toggle("selected", item === mode);
    $("mode-" + item).setAttribute("aria-selected", String(item === mode));
  }
  updateMeta();
}
function updateMeta() {
  $("yaml-meta").textContent =
    `${$("yaml-editor").value.split("\n").length} 行 · UTF-8 · YAML`;
}
function canDiscard() {
  return (
    !state.dirty || window.confirm("当前修改尚未保存，确定放弃这些修改吗？")
  );
}
async function acceptDocument(value, dirty = false) {
  state.editVersion++;
  state.documentEpoch++;
  state.text = value.text;
  state.document = value.document || {};
  state.path = value.path || null;
  state.revision = value.revision || null;
  state.dirty = dirty;
  state.formDirty = false;
  $("config-path").value = state.path || "";
  $("yaml-editor").value = state.text;
  if (!value.document) {
    state.mode = "form";
    await switchMode("yaml");
    notify(value.parse_error || "YAML 格式错误，请在源码模式修正。", true);
  }
  renderForm();
  updateReady();
  updateMeta();
  $("validation-result").hidden = true;
}
async function loadConfig(path, discard = true) {
  if (discard && !canDiscard()) return;
  const version = state.editVersion;
  const value = await api("/api/config/load", { path });
  if (version !== state.editVersion)
    throw new Error("加载期间配置已继续编辑，请保存修改后重新加载。");
  await acceptDocument(value);
  if (value.document) notify("已加载配置文件。");
}
async function saveConfig(target) {
  if (!target.trim()) throw new Error("请填写本机配置文件路径。");
  const version = state.editVersion;
  const epoch = state.documentEpoch;
  const text = await currentText();
  if (version !== state.editVersion)
    throw new Error("配置已继续编辑，请重新保存。");
  const value = await api("/api/config/save", {
    path: target,
    text,
    revision: target === state.path ? state.revision : null,
  });
  if (epoch !== state.documentEpoch) {
    notify(`已保存 ${value.path}，当前编辑的其他配置已保留。`);
    return;
  }
  if (version !== state.editVersion) {
    state.path = value.path;
    state.revision = value.revision;
    $("config-path").value = value.path;
    updateReady();
    notify("已保存提交时的版本，后续修改尚未保存。");
    return;
  }
  await acceptDocument(value);
  notify("配置已保存。");
}
function savedBody() {
  if (!state.path || state.dirty || $("config-path").value !== state.path)
    throw new Error("请先保存配置。");
  return { path: state.path, revision: state.revision };
}
function setPage(name) {
  state.page = name;
  for (const page of ["config", "inspect", "runs"])
    $("page-" + page).hidden = page !== name;
  document
    .querySelectorAll(".nav")
    .forEach((button) =>
      button.classList.toggle("active", button.dataset.page === name),
    );
  $("breadcrumb").textContent = {
    config: "配置编辑",
    inspect: "机器诊断",
    runs: "运行记录",
  }[name];
  if (name === "runs")
    refreshRuns().catch((error) => notify(error.message, true));
}
function configStateDir() {
  const directory =
    state.document.policy?.state_dir || "~/.local/state/autobench";
  if (typeof directory !== "string")
    throw new Error("配置中的状态目录必须是字符串。");
  if (directory.startsWith("/") || directory.startsWith("~")) return directory;
  const base = state.path
    ? state.path.slice(0, state.path.lastIndexOf("/"))
    : state.info.base_dir;
  return base + "/" + directory;
}
function stateDir() {
  return $("state-dir").value.trim() || state.info.state_dir;
}
function runUrl(suffix = "", extra = {}) {
  const query = new URLSearchParams({ state_dir: stateDir(), ...extra });
  return `/api/runs/${encodeURIComponent(state.run)}${suffix}?${query}`;
}
function date(value) {
  return value
    ? new Date(value * 1000).toLocaleString("zh-CN", { hour12: false })
    : "—";
}
function badge(status) {
  return el("span", "badge", statusLabels[status] || status);
}
function table(headers, rows) {
  const node = el("table");
  const head = el("tr");
  headers.forEach((header) => head.append(el("th", "", header)));
  const thead = el("thead");
  thead.append(head);
  node.append(thead);
  const body = el("tbody");
  rows.forEach((cells) => {
    const row = el("tr");
    cells.forEach((cell) => {
      const td = el("td");
      if (!(cell instanceof Node)) td.title = String(cell ?? "—");
      td.append(
        cell instanceof Node ? cell : el("span", "", String(cell ?? "—")),
      );
      row.append(td);
    });
    body.append(row);
  });
  node.append(body);
  return node;
}
async function chooseRun(id) {
  state.run = id;
  state.after = 0;
  state.eventLines = [];
  state.artifactsAt = 0;
  $("events").textContent = "";
  $("artifacts").replaceChildren();
  $("preview-card").hidden = true;
  sessionStorage.setItem(
    "autobench-selection",
    JSON.stringify({ id, stateDir: stateDir() }),
  );
  await refreshDetail();
}
async function refreshRuns() {
  if (state.refreshBusy) return;
  state.refreshBusy = true;
  try {
    const directory = stateDir();
    const runs = await api(
      `/api/runs?${new URLSearchParams({ state_dir: directory })}`,
    );
    if (directory !== stateDir()) return;
    $("run-count").textContent = `${runs.length} 次运行`;
    if (!runs.length)
      $("runs-list").replaceChildren(
        el(
          "div",
          "empty",
          "此状态目录暂无运行记录。可从配置页面启动测试，或填写已有 CLI 运行的状态目录。",
        ),
      );
    else {
      const rows = runs.map((run) => {
        const button = el(
          "button",
          "text-button mono",
          run.id.slice(0, 12) + "…",
        );
        button.title = run.id;
        button.addEventListener("click", () =>
          chooseRun(run.id).catch((error) => notify(error.message, true)),
        );
        return [
          button,
          badge(run.status),
          run.cancel_requested ? "已请求取消" : "—",
          date(run.created),
          date(run.updated),
        ];
      });
      $("runs-list").replaceChildren(
        table(["运行 ID", "状态", "取消请求", "创建时间", "更新时间"], rows),
      );
    }
    if (state.run) await refreshDetail();
  } finally {
    state.refreshBusy = false;
  }
}
async function refreshDetail() {
  if (!state.run) return;
  const id = state.run;
  const directory = stateDir();
  const detail = await api(runUrl());
  if (id !== state.run || directory !== stateDir()) return;
  $("run-detail").hidden = false;
  $("run-id").textContent = id;
  $("run-status").textContent = statusLabels[detail.status] || detail.status;
  $("controller-status").textContent =
    `${detail.controller_active ? "控制端正在推进任务" : "当前没有活动控制端，可按需恢复或收集"}${detail.cancel_requested ? " · 已持久化取消请求" : ""} · ${detail.state_dir}`;
  document.querySelectorAll("[data-action]").forEach((button) => {
    if (!button.classList.contains("busy"))
      button.disabled =
        detail.controller_active && button.dataset.action !== "cancel";
  });
  $("archive-download").href = runUrl("/archive");
  $("jobs").replaceChildren(
    table(
      ["机器", "系统", "测试", "次数", "状态", "原因 / 产物位置"],
      detail.jobs.map((job) => [
        job.machine,
        job.system,
        job.benchmark,
        job.repetition,
        badge(job.status),
        job.reason || job.output || "—",
      ]),
    ),
  );
  const events = await api(runUrl("/events", { after: state.after }));
  if (id !== state.run || directory !== stateDir()) return;
  for (const event of events) {
    state.after = event.sequence;
    state.eventLines.push(
      `${date(event.time)}  ${event.job_id ? event.job_id.slice(0, 8) + "  " : ""}${event.message}`,
    );
  }
  if (events.length) {
    const atBottom =
      $("events").scrollHeight -
        $("events").scrollTop -
        $("events").clientHeight <
      40;
    state.eventLines = state.eventLines.slice(-5000);
    $("events").textContent = state.eventLines.join("\n");
    if (atBottom) $("events").scrollTop = $("events").scrollHeight;
  }
  if (Date.now() - state.artifactsAt > 10000) {
    state.artifactsAt = Date.now();
    try {
      await refreshArtifacts(id, directory);
    } catch (error) {
      $("artifacts").textContent = error.message;
    }
  }
}
async function refreshArtifacts(id, directory) {
  const items = await api(runUrl("/artifacts"));
  if (id !== state.run || directory !== stateDir()) return;
  const nodes = items.map((item) => {
    const row = el("div", "artifact-row");
    row.append(el("span", "mono", item.path));
    if (!/\.(html?|svg)$/i.test(item.path)) {
      const preview = el("button", "", "预览");
      const url = runUrl("/artifact", { path: item.path, preview: "true" });
      preview.addEventListener("click", async () => {
        try {
          const value = await api(url);
          $("preview-card").hidden = false;
          $("preview-title").textContent = item.path;
          $("artifact-preview").textContent =
            value.text +
            (value.truncated
              ? "\n\n[仅预览前 256 KiB，请下载查看完整文件]"
              : "");
          $("preview-card").scrollIntoView({
            behavior: "smooth",
            block: "nearest",
          });
        } catch (error) {
          notify(error.message, true);
        }
      });
      row.append(preview);
    }
    const link = el("a", "", "下载 ↓");
    link.href = runUrl("/artifact", { path: item.path });
    link.download = item.path.split("/").at(-1);
    row.append(link);
    return row;
  });
  $("artifacts").replaceChildren(
    ...(nodes.length
      ? nodes
      : [
          el(
            "p",
            "muted",
            "暂无已回收文件。可使用“仅收集结果”回收远端已封存的产物。",
          ),
        ]),
  );
}
function renderInspection(results) {
  $("inspection-result").replaceChildren();
  for (const item of results) {
    const card = el("div", "card inspection-card");
    const heading = el("div", "card-heading");
    heading.append(
      el("h2", "", item.machine),
      badge(item.errors?.length ? "failed" : "succeeded"),
    );
    card.append(heading);
    if (item.probe)
      card.append(
        el(
          "p",
          "muted mono",
          `Machine ID: ${item.probe.machine_id || "未知"} · BootCurrent: ${item.probe.efi?.current || "未知"}`,
        ),
      );
    for (const system of item.systems || [])
      card.append(
        el(
          "div",
          "system-row",
          `${system.id} · ${system.current ? "当前系统" : "未启动"} · ${system.dependencies_checked ? "已检查依赖" : "依赖未检查"} · ${system.identity_checked ? "已核对身份" : "身份未核对"}`,
        ),
      );
    for (const error of item.errors || []) card.append(el("p", "issue", error));
    const details = el("details");
    details.append(
      el("summary", "muted", "完整检查结果、启动项与环境覆盖"),
      el("pre", "", JSON.stringify(item, null, 2)),
    );
    card.append(details);
    $("inspection-result").append(card);
  }
}
async function watchOperation(operation) {
  if (!operation.id || state.watched.has(operation.id)) return;
  state.watched.add(operation.id);
  try {
    while (operation.status === "running") {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      operation = await api(`/api/operations/${operation.id}`);
    }
    if (operation.status === "failed") {
      notify(operation.error || "操作失败", true);
      if (["discover", "doctor"].includes(operation.kind))
        $("inspection-result").replaceChildren(
          el("pre", "result error", operation.error),
        );
    } else if (
      ["discover", "doctor"].includes(operation.kind) &&
      operation.result
    ) {
      renderInspection(operation.result);
      notify("机器检查完成。");
    } else
      notify(
        `操作结束：${statusLabels[operation.result] || operation.result || operation.status}`,
      );
    if (state.page === "runs") await refreshRuns();
  } catch (error) {
    notify(error.message, true);
  } finally {
    state.watched.delete(operation.id);
  }
}
async function inspect(kind) {
  const operation = await api("/api/inspections", {
    ...savedBody(),
    kind,
    machine: $("inspection-machine").value || null,
  });
  $("inspection-result").replaceChildren(
    el("div", "empty", "正在连接目标机器并读取当前系统信息…"),
  );
  notify("检查已提交，可继续使用其他页面。");
  watchOperation(operation);
}
async function boot() {
  state.info = await api("/api/info");
  $("version").textContent = `AutoBench v${state.info.version}`;
  $("server-address").textContent = window.location.host;
  $("state-dir").value = state.info.state_dir;
  $("base-dir").textContent = `工作目录：${state.info.base_dir}`;
  const initial = await api("/api/config/parse", { text: state.info.template });
  await acceptDocument(
    { text: state.info.template, document: initial.document },
    true,
  );
  document
    .querySelectorAll(".nav")
    .forEach((button) =>
      button.addEventListener("click", () => setPage(button.dataset.page)),
    );
  action("load", () => loadConfig($("config-path").value));
  action("save", () => saveConfig($("config-path").value));
  action("save-as", () => {
    $("save-path").value = "";
    $("save-dialog").showModal();
  });
  $("save-dialog")
    .querySelector("form")
    .addEventListener("submit", async (event) => {
      if (event.submitter?.value === "cancel") return;
      event.preventDefault();
      $("confirm-save-as").disabled = true;
      try {
        await saveConfig($("save-path").value);
        $("save-dialog").close();
      } catch (error) {
        notify(error.message, true);
      } finally {
        $("confirm-save-as").disabled = false;
      }
    });
  action("new", async () => {
    if (!canDiscard()) return;
    await acceptDocument(
      {
        text: state.info.template,
        document: structuredClone(initial.document),
      },
      true,
    );
    notify("已创建模板，请填写目标机器信息并保存。");
  });
  $("import").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file || !canDiscard()) {
      event.target.value = "";
      return;
    }
    const text = await file.text();
    let value;
    try {
      value = await api("/api/config/parse", { text });
    } catch (error) {
      value = { document: null, parse_error: error.message };
    }
    await acceptDocument({ text, ...value }, true);
    event.target.value = "";
    if (value.document) notify("已导入内容，请指定本机文件路径并保存。");
  });
  action("export", async () => {
    const text = await currentText();
    const url = URL.createObjectURL(
      new Blob([text], { type: "text/yaml;charset=utf-8" }),
    );
    const link = el("a");
    link.href = url;
    link.download = state.path?.split("/").at(-1) || "autobench.yaml";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  action("mode-form", () => switchMode("form"));
  action("mode-yaml", () => switchMode("yaml"));
  $("yaml-editor").addEventListener("input", () => {
    state.text = $("yaml-editor").value;
    markDirty(false);
    updateMeta();
  });
  $("yaml-editor").addEventListener("keydown", (event) => {
    if (event.key === "Tab") {
      event.preventDefault();
      const input = event.target;
      input.setRangeText("  ", input.selectionStart, input.selectionEnd, "end");
      input.dispatchEvent(new Event("input"));
    }
  });
  $("config-path").addEventListener("input", updateReady);
  action("validate", async () => {
    try {
      const result = await api("/api/config/validate", savedBody());
      $("validation-result").className = "result";
      $("validation-result").textContent =
        `校验通过：${result.machines} 台机器\n` +
        JSON.stringify(result, null, 2);
      notify("完整校验通过。");
    } catch (error) {
      $("validation-result").className = "result error";
      $("validation-result").textContent = error.message;
      throw error;
    } finally {
      $("validation-result").hidden = false;
    }
  });
  action("start", async () => {
    const value = await api("/api/runs", savedBody());
    $("state-dir").value = value.state_dir;
    await chooseRun(value.run_id);
    setPage("runs");
    notify(`测试已启动：${value.run_id}`);
    watchOperation(value.operation);
  });
  action("discover", () => inspect("discover"));
  action("doctor", () => inspect("doctor"));
  action("refresh-runs", async () => {
    state.run = null;
    $("run-detail").hidden = true;
    sessionStorage.removeItem("autobench-selection");
    await refreshRuns();
  });
  action("config-state", async () => {
    $("state-dir").value = configStateDir();
    state.run = null;
    $("run-detail").hidden = true;
    sessionStorage.removeItem("autobench-selection");
    await refreshRuns();
  });
  $("state-dir").addEventListener("input", () => {
    state.run = null;
    $("run-detail").hidden = true;
    sessionStorage.removeItem("autobench-selection");
  });
  document.querySelectorAll("[data-action]").forEach((button) =>
    button.addEventListener("click", async () => {
      button.disabled = true;
      button.classList.add("busy");
      try {
        const result = await api(
          `/api/runs/${state.run}/${button.dataset.action}`,
          { state_dir: stateDir() },
        );
        notify(
          result.status === "cancel_requested"
            ? "取消请求已持久化，活动控制端将处理。"
            : "操作已提交。",
        );
        watchOperation(result);
        state.artifactsAt = 0;
        await refreshDetail();
      } catch (error) {
        notify(error.message, true);
      } finally {
        button.disabled = false;
        button.classList.remove("busy");
      }
    }),
  );
  action("close-preview", () => {
    $("preview-card").hidden = true;
  });
  window.addEventListener("beforeunload", (event) => {
    if (state.dirty) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  if (state.info.config) {
    try {
      await loadConfig(state.info.config, false);
    } catch (error) {
      $("config-path").value = state.info.config;
      notify(error.message, true);
    }
  }
  try {
    const selection = JSON.parse(
      sessionStorage.getItem("autobench-selection") || "null",
    );
    if (selection?.id) {
      $("state-dir").value = selection.stateDir;
      await chooseRun(selection.id);
      setPage("runs");
    }
  } catch (error) {
    sessionStorage.removeItem("autobench-selection");
    notify(error.message, true);
  }
  for (const operation of await api("/api/operations"))
    if (operation.status === "running") watchOperation(operation);
  setInterval(() => {
    if (state.page === "runs" && !document.hidden)
      refreshRuns().catch((error) => notify(error.message, true));
  }, 2000);
}
boot().catch((error) => notify(`初始化失败：${error.message}`, true));
