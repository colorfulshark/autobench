import { state, change, show, dirty } from "./state.js";
import { at, own, put, statusLabels, statusTone } from "./model.js";
export const $ = (id) => document.getElementById(id);
export function icon(name, cls = "icon") {
  const paths = {
    arrowDown: "M12 4v16m-6-6 6 6 6-6",
    arrowLeft: "M20 12H4m6-6-6 6 6 6",
    arrowRight: "M4 12h16m-6-6 6 6-6 6",
    arrowUp: "M12 20V4m-6 6 6-6 6 6",
    calendar: "M4 5h16v16H4zM8 3v4m8-4v4M4 10h16m-11 4h2m2 0h2m-6 3h2",
    chart: "M4 13h3v8H4zm6-4h3v12h-3zm6-5h3v17h-3z",
    chevronRight: "m9 6 6 6-6 6",
    chain:
      "M10 13a4 4 0 0 0 6 .5l4-4a4 4 0 0 0-5.7-5.7L12 6m2 5a4 4 0 0 0-6-.5l-4 4a4 4 0 0 0 5.7 5.7L12 18",
    check: "M5 12l4 4L19 6",
    circleCheck: "M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0ZM8 12l3 3 5-6",
    circleError: "M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0ZM9 9l6 6m0-6-6 6",
    clipboard: "M9 5H5v16h14V5h-4M9 3h6v4H9zm0 11 2 2 4-4",
    code: "m7 6-5 6 5 6m10-12 5 6-5 6m-3-15-4 18",
    copy: "M8 7h12v14H8zM4 17V3h12",
    cube: "m12 2 9 5v10l-9 5-9-5V7Zm-9 5 9 5 9-5m-9 5v10",
    database:
      "M21 5c0 2.2-4 4-9 4S3 7.2 3 5s4-4 9-4 9 1.8 9 4ZM3 5v14c0 2.2 4 4 9 4s9-1.8 9-4V5M3 12c0 2.2 4 4 9 4s9-1.8 9-4",
    edit: "M4 16l-1 5 5-1L20 8a2.8 2.8 0 0 0-4-4L4 16Zm10-10 4 4",
    download: "M12 3v12m-5-5 5 5 5-5M4 15v5h16v-5",
    error: "M6 6l12 12M6 18L18 6",
    eye: "M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6S2 12 2 12Zm13 0a3 3 0 1 0-6 0 3 3 0 0 0 6 0Z",
    eyeOff:
      "M3 3l18 18M10 6.2c.6-.1 1.3-.2 2-.2 6.4 0 10 6 10 6a17 17 0 0 1-3.1 3.2M6.1 7.8A17 17 0 0 0 2 12s3.6 6 10 6c1.5 0 2.9-.3 4.1-.9M10 10a2.8 2.8 0 0 0 4 4",
    file: "M13 3H5v18h14V9Zm0 0v6h6",
    flame: "M13 2c1 4 5 6 6 10a7 7 0 1 1-14 1c0-2 1-4 3-6 0 3 1 4 2 4 2-3 3-6 3-9Z",
    flask: "M9 3h6m-5 0v7L4 19a1.2 1.2 0 0 0 1 2h14a1.2 1.2 0 0 0 1-2l-6-9V3",
    folder: "M3 5h6l3 3h9v12H3a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Z",
    globe:
      "M22 12a10 10 0 1 0-20 0 10 10 0 0 0 20 0ZM2 12h20m-10 10c5-5 5-15 0-20-5 5-5 15 0 20Z",
    info: "M21 12a9 9 0 1 0-18 0 9 9 0 0 0 18 0ZM12 11v5m0-8h.01",
    key: "M8 14a7 7 0 1 1 5 3l-3 3H7v2H2v-4l6-6m9-4h.01",
    layers: "m2 7 10-5 10 5-10 5Zm0 5 10 5 10-5M2 17l10 5 10-5",
    monitor: "M3 4h18v13H3zm5 17h8m-4-4v4",
    lock: "M7 10V7a5 5 0 1 1 10 0v3M4 10h16v11H4Zm8 4v3",
    network:
      "M15 5a3 3 0 1 0-6 0 3 3 0 0 0 6 0Zm-8 14a3 3 0 1 0-6 0 3 3 0 0 0 6 0Zm16 0a3 3 0 1 0-6 0 3 3 0 0 0 6 0ZM12 8v4m-8 4v-4h16v4",
    pending: "M12 5v7l4 2",
    pause: "M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0ZM10 8v8m4-8v8",
    play: "M6 3l15 9-15 9Z",
    plus: "M12 5v14M5 12h14",
    scan: "M3 8V4h4m10 0h4v4m0 8v4h-4m-10 0H3v-4m4-4h10",
    server: "M3 3h18v8H3zm0 10h18v8H3zm4-6h.01m-.01 10h.01m4-10h6m-6 10h6",
    timer: "M21 13a9 9 0 1 1-9-9m4 0h5v5M12 8v5l3 2",
    hourglass: "M6 3h12M6 21h12M7 3v4l5 5-5 5v4m10-18v4l-5 5 5 5v4",
    power: "M12 3v9M6 6a9 9 0 1 0 12 0",
    settings:
      "m9 3-.6 2.3-2 .9L4.2 5.6 2.5 8.5l1.7 1.7v2.6l-1.7 1.7 1.7 2.9 2.2-.6 2 .9L9 20h4l.6-2.3 2-.9 2.2.6 1.7-2.9-1.7-1.7v-2.6l1.7-1.7-1.7-2.9-2.2.6-2-.9L13 3Zm5 8.5a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z",
    shield:
      "M12 2c-2 2-6 3-9 4v5c0 5 4 9 9 11 5-2 9-6 9-11V6c-3-1-7-2-9-4Zm-4 10 3 3 5-5",
    tag: "M3 3h8l10 10-8 8L3 11Zm4 4h.01",
    trash: "M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7",
    user: "M16 7a4 4 0 1 0-8 0 4 4 0 0 0 8 0Zm-4 7c-5 0-9 2-9 7h18c0-5-4-7-9-7Z",
    warning: "M12 3 2 21h20Zm0 6v5m0 3h.01",
  };
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", cls);
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.7");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(svg.namespaceURI, "path");
  path.setAttribute("d", paths[name] || paths.pending);
  svg.append(path);
  return svg;
}
export function node(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
export function button(text, fn, cls = "button", symbol) {
  const e = node("button", cls, text);
  e.type = "button";
  if (symbol) e.prepend(icon(symbol));
  if (fn) e.addEventListener("click", () => perform(e, fn));
  return e;
}
export async function perform(element, fn) {
  if (element.matches(":disabled, [aria-disabled=true]")) return;
  element.setAttribute("aria-disabled", "true");
  element.setAttribute("aria-busy", "true");
  element.classList.add("busy");
  try {
    return await fn();
  } catch (error) {
    show(error.message, "error");
  } finally {
    element.removeAttribute("aria-disabled");
    element.removeAttribute("aria-busy");
    element.classList.remove("busy");
  }
}
export function statusIndicator(className) {
  const status = node("span", "inline-status " + className);
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  status.setAttribute("aria-atomic", "true");
  return status;
}
export function updateStatusIndicator(
  status,
  phase,
  message,
  symbol = { success: "check", error: "error" }[phase],
) {
  if (
    status.dataset.phase === phase &&
    status.getAttribute("aria-label") === message
  )
    return;
  status.dataset.phase = phase;
  status.setAttribute("aria-label", message);
  status.replaceChildren();
  if (phase === "running" || symbol) {
    const mark = node("span", "status-icon");
    mark.setAttribute("aria-hidden", "true");
    if (symbol) mark.append(icon(symbol));
    status.append(mark);
  }
  if (phase !== "idle") {
    const visible = phase === "error" || (phase === "stale" && !symbol);
    status.append(node("span", visible ? "status-message" : "visually-hidden", message));
  }
}
export function pill(text, tone = "neutral") {
  const e = node("span", "pill", text);
  e.dataset.tone = tone;
  return e;
}
export const badge = (status) =>
  Object.hasOwn(statusLabels, status)
    ? pill(statusLabels[status], statusTone(status))
    : pill(status ? `状态未知（${status}）` : "状态未知", "warning");
export function row(...children) {
  const e = node("div", "field-row");
  e.append(...children);
  return e;
}
export function hint(text) {
  return node("p", "hint", text);
}
export function heading(title, description, actions, symbol) {
  const e = node("div", "section-heading");
  const t = node("div");
  const label = node("h2", symbol ? "icon-heading" : "", title);
  if (symbol) label.prepend(icon(symbol));
  t.append(label);
  if (description) t.append(hint(description));
  e.append(t);
  if (actions) e.append(actions);
  return e;
}
export function advanced(title, paths, build, key = title) {
  const e = node("details", "advanced");
  e.dataset.section = key;
  e.open = state.opened.has(key);
  const count = paths.filter(
    (path) => at(state.document, path) !== undefined,
  ).length;
  const s = node("summary");
  s.append(icon("chevronRight", "icon disclosure-chevron"), node("span", "", title));
  if (count) s.append(node("span", "advanced-count", `已配置 ${count} 项`));
  const content = node("div", "advanced-body");
  build(content);
  e.append(s, content);
  e.addEventListener("toggle", () => {
    if (e.open) state.opened.add(key);
    else state.opened.delete(key);
  });
  return e;
}
export function inputField(
  label,
  path,
  {
    fallback = "",
    placeholder = "",
    type = "text",
    help = "",
    reset = false,
    nullable = false,
    emptyValue = "",
    browse = null,
    width = "",
    required = false,
    leadingIcon = "",
    labelIcon = "",
  } = {},
) {
  const wrap = node("div", "field" + (width ? " " + width : ""));
  wrap.dataset.fieldPath = JSON.stringify(path);
  const header = node("div", "field-label");
  const lab = node("label", "", label);
  if (required) lab.append(node("span", "required-mark", " *"));
  if (labelIcon) {
    lab.classList.add("icon-heading");
    lab.prepend(icon(labelIcon));
  }
  const id = "input-" + path.map(encodeURIComponent).join("-");
  lab.htmlFor = id;
  header.append(lab);
  if (reset)
    header.append(
      button(
        "恢复默认",
        () => change(path, undefined, true),
        "link-button reset-field",
      ),
    );
  const controls = node("div", "input-controls");
  const raw = at(state.document, path);
  const input = node("input");
  input.id = id;
  input.type = type;
  input.value =
    raw === undefined || raw === null ? String(fallback) : String(raw);
  input.placeholder = placeholder;
  input.spellcheck = false;
  input.autocomplete = type === "password" ? "new-password" : "off";
  if (required) input.setAttribute("aria-required", "true");
  if (type === "number") input.step = "any";
  input.addEventListener("input", () => {
    change(
      path,
      type === "number" && input.value !== ""
        ? Number(input.value)
        : input.value === "" ? emptyValue : input.value,
    );
    wrap.classList.remove("invalid");
    input.removeAttribute("aria-invalid");
    wrap.querySelector(".field-error")?.remove();
    if (help) input.setAttribute("aria-describedby", id + "-help");
    else input.removeAttribute("aria-describedby");
  });
  if (leadingIcon) {
    controls.classList.add("with-input-icon");
    controls.append(icon(leadingIcon, "icon input-icon"));
  }
  controls.append(input);
  if (type === "password") {
    const reveal = button(
      leadingIcon ? "" : "显示",
      () => {
        const visible = input.type === "password";
        input.type = visible ? "text" : "password";
        if (leadingIcon)
          reveal.replaceChildren(icon(visible ? "eyeOff" : "eye"));
        else reveal.textContent = visible ? "隐藏" : "显示";
        reveal.setAttribute("aria-label", `${visible ? "隐藏" : "显示"}${label}`);
      },
      "icon-button reveal",
    );
    if (leadingIcon) reveal.append(icon("eye"));
    reveal.setAttribute("aria-label", "显示" + label);
    controls.append(reveal);
  }
  if (browse)
    controls.append(
      button("选择…", () => browse(path, input.value), "browse-button"),
    );
  if (nullable)
    controls.append(
      button("清除", () => change(path, null, true), "link-button"),
    );
  wrap.append(header, controls);
  if (help) {
    const description = hint(help);
    description.id = id + "-help";
    input.setAttribute("aria-describedby", description.id);
    wrap.append(description);
  }
  const errors = state.errors.filter(
    (error) => JSON.stringify(error.path) === JSON.stringify(path),
  );
  if (errors.length) {
    wrap.classList.add("invalid");
    input.setAttribute("aria-invalid", "true");
    const message = node("p", "field-error", errors.map(friendlyError).join("；"));
    message.id = id + "-error";
    input.setAttribute("aria-describedby", help ? `${message.id} ${id}-help` : message.id);
    wrap.append(message);
  }
  if (
    raw !== undefined &&
    raw !== null &&
    type === "number" &&
    typeof raw !== "number"
  )
    wrap.append(hint("原始值不是数字，请修改此处或在 YAML 中修正。"));
  return wrap;
}
export function select(label, options, current, onChange, id) {
  const wrap = node("div", "field");
  const lab = node("label", "field-label", label);
  const input = node("select");
  input.id = id;
  lab.htmlFor = id;
  for (const [key, text] of options) input.add(new Option(text, key));
  input.value = current;
  input.addEventListener("change", () => onChange(input.value));
  wrap.append(lab, input);
  return wrap;
}
export function toggle(label, checked, onChange) {
  const e = node("label", "toggle");
  const input = node("input");
  input.type = "checkbox";
  input.checked = checked;
  input.addEventListener("change", () => onChange(input.checked));
  e.append(input, node("span", "", label));
  return e;
}
export function empty(title, text, action) {
  const e = node("div", "empty");
  e.append(icon("info", "icon empty-icon"), node("h3", "", title), hint(text));
  if (action) e.append(action);
  return e;
}
export function jsonDetails(title, value) {
  const e = node("details", "technical");
  e.append(
    node("summary", "", title),
    node("pre", "code-block", JSON.stringify(value, null, 2)),
  );
  return e;
}
export function listEditor(
  label,
  path,
  { browse = null, help = "每项是一个独立参数，不执行 Shell 展开。" } = {},
) {
  const e = node("div", "list-editor");
  e.dataset.fieldPath = JSON.stringify(path);
  e.append(node("h3", "", label));
  let items = at(state.document, path);
  if (!Array.isArray(items)) {
    if (items !== undefined)
      e.append(hint("当前值无法以列表显示，请在 YAML 中修正。"));
    items = [];
  }
  items.forEach((item, index) => {
    const r = node("div", "list-row");
    r.dataset.fieldPath = JSON.stringify([...path, index]);
    const input = node("textarea");
    input.rows = 1;
    input.value = String(item);
    input.setAttribute("aria-label", `${label}第 ${index + 1} 项`);
    input.addEventListener("input", () =>
      change([...path, index], input.value),
    );
    r.append(input);
    if (browse)
      r.append(
        button(
          "选择",
          async () => {
            const chosen = await browse(input.value);
            if (chosen) change([...path, index], chosen, true);
          },
          "icon-button",
        ),
      );
    const up = button(
      "",
      () => {
        [items[index - 1], items[index]] = [items[index], items[index - 1]];
        dirty(true);
      },
      "icon-button",
      "arrowUp",
    );
    up.disabled = index === 0;
    up.setAttribute("aria-label", `${label}上移第 ${index + 1} 项`);
    const down = button(
      "",
      () => {
        [items[index + 1], items[index]] = [items[index], items[index + 1]];
        dirty(true);
      },
      "icon-button",
      "arrowDown",
    );
    down.disabled = index === items.length - 1;
    const remove = button(
      "",
      () => {
        items.splice(index, 1);
        dirty(true);
      },
      "icon-button",
      "trash",
    );
    remove.setAttribute("aria-label", `删除${label}第 ${index + 1} 项`);
    r.append(up, down, remove);
    e.append(r);
  });
  e.append(
    button(
      "添加一项",
      () => change(path, [...items, ""], true),
      "text-button",
      "plus",
    ),
  );
  if (help) e.append(hint(help));
  return e;
}
export function envEditor(path, { showTitle = true } = {}) {
  const e = node("div", "env-editor");
  if (showTitle) e.append(node("h3", "", "环境变量"));
  const raw = at(state.document, path);
  const entries =
    raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
  for (const [key, val] of Object.entries(entries)) {
    const r = node("div", "env-row");
    r.dataset.fieldPath = JSON.stringify([...path, key]);
    const k = node("input");
    k.value = key;
    k.setAttribute("aria-label", "环境变量名称");
    k.placeholder = "变量名";
    k.addEventListener("change", () => {
      if (k.value !== key && own(entries, k.value)) {
        k.value = key;
        show("变量名已存在。", "error");
        return;
      }
      delete entries[key];
      put(entries, k.value, val);
      dirty(true);
    });
    const v = node("textarea");
    v.rows = typeof val === "string" && val.includes("\n") ? 3 : 1;
    v.value = String(val);
    v.setAttribute("aria-label", `${key}的值`);
    v.placeholder = "字符串值（允许为空）";
    v.addEventListener("input", () => {
      put(entries, key, v.value);
      dirty();
    });
    const remove = button(
      "",
      () => {
        delete entries[key];
        dirty(true);
      },
      "icon-button",
      "trash",
    );
    remove.setAttribute("aria-label", `删除环境变量 ${key}`);
    r.append(k, v, remove);
    e.append(r);
    if (typeof val !== "string")
      e.append(hint(`${key} 的原始值不是字符串；修改值后会按字符串保存。`));
  }
  const actions = node("div", "env-actions");
  actions.append(
    button(
      "添加变量",
      () => {
        let key = "NEW_VAR";
        while (own(entries, key)) key += "_";
        put(entries, key, "");
        change(path, entries, true);
      },
      "text-button",
      "plus",
    ),
    hint("值按字面字符串传入；测试级变量覆盖系统级变量。"),
  );
  e.append(actions);
  return e;
}
export function table(headers, rows) {
  const e = node("table");
  const thead = node("thead"),
    tr = node("tr");
  for (const title of headers) {
    const cell = node("th", "", title);
    cell.scope = "col";
    tr.append(cell);
  }
  thead.append(tr);
  e.append(thead);
  const body = node("tbody");
  for (const cells of rows) {
    const row = node("tr");
    for (const cell of cells) {
      const td = node("td");
      if (typeof cell === "number") td.classList.add("num");
      td.append(
        cell instanceof Node ? cell : node("span", "", String(cell ?? "—")),
      );
      if (!(cell instanceof Node)) td.title = String(cell ?? "—");
      row.append(td);
    }
    body.append(row);
  }
  e.append(body);
  for (let index = 0; index < headers.length; index++) {
    if (rows.some((cells) => typeof cells[index] === "number"))
      tr.children[index].classList.add("num");
  }
  return e;
}
const dateFormat = new Intl.DateTimeFormat("sv-SE", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  timeZoneName: "longOffset",
});
export const date = (timestamp) =>
  timestamp ? dateFormat.format(timestamp * 1000).replace("−", "-") : "—";

export function friendlyError(error) {
  const messages = {
    missing: "请填写此项",
    string_type: "请输入文本",
    int_type: "请输入整数",
    bool_type: "请使用 true 或 false，或通过开关设置",
    int_parsing: "请输入整数",
    int_from_float: "请输入整数",
    greater_than: "数值需要大于允许的下限",
    greater_than_equal: "数值小于允许的下限",
    less_than_equal: "数值超过允许的上限",
    extra_forbidden: "不支持此配置字段",
    file_missing: "服务器上的文件不存在，请重新选择",
    source: "源码包检查未通过，请核对文件或高级源码设置",
  };
  if (messages[error.code]) return messages[error.code];
  if (error.message?.includes("IDs must"))
    return "标识只能使用字母、数字、下划线或连字符，且同组不能重复";
  if (error.message?.includes("UB_RESULTDIR"))
    return "UB_RESULTDIR 由工具管理，请移除此环境变量";
  if (error.message?.includes("duplicate system boot_entry"))
    return "共用 UEFI 启动项的系统需要分别设置不同的 GRUB 启动项。";
  if (error.message?.includes("boot_entry"))
    return "启动项需要是四位十六进制字符串";
  if (error.message?.includes("test names cannot"))
    return "测试名不能为空、包含空白或 NUL，也不能以 - 开头";
  return error.message;
}
