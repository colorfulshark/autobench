import { state, change, show, dirty } from "./state.js";
import { at, own, put, statusLabels, statusTone } from "./model.js";
export const $ = (id) => document.getElementById(id);
export function node(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
export function button(text, fn, cls = "button") {
  const e = node("button", cls, text);
  e.type = "button";
  if (fn) e.addEventListener("click", () => perform(e, fn));
  return e;
}
export async function perform(element, fn) {
  element.disabled = true;
  element.classList.add("busy");
  try {
    return await fn();
  } catch (error) {
    show(error.message, "error");
  } finally {
    element.disabled = false;
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
export function updateStatusIndicator(status, phase, message) {
  if (
    status.dataset.phase === phase &&
    status.getAttribute("aria-label") === message
  )
    return;
  status.dataset.phase = phase;
  status.setAttribute("aria-label", message);
  status.replaceChildren();
  if (["running", "success", "error"].includes(phase)) {
    const icon = node(
      "span",
      "status-icon",
      phase === "success" ? "✓" : phase === "error" ? "✗" : "",
    );
    icon.setAttribute("aria-hidden", "true");
    status.append(icon);
  }
  if (phase === "error" || phase === "stale")
    status.append(node("span", "status-message", message));
}
export function pill(text, tone = "neutral") {
  const e = node("span", "pill", text);
  e.dataset.tone = tone;
  return e;
}
export const badge = (status) =>
  pill(statusLabels[status] || status, statusTone(status));
export function row(...children) {
  const e = node("div", "field-row");
  e.append(...children);
  return e;
}
export function hint(text) {
  return node("p", "hint", text);
}
export function heading(title, description, actions) {
  const e = node("div", "section-heading");
  const t = node("div");
  t.append(node("h2", "", title));
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
  s.append(
    node("span", "", title),
    node(
      "span",
      "advanced-count",
      count ? `已配置 ${count} 项` : "使用默认设置",
    ),
  );
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
    browse = null,
    width = "",
    required = false,
  } = {},
) {
  const wrap = node("div", "field" + (width ? " " + width : ""));
  wrap.dataset.fieldPath = JSON.stringify(path);
  const header = node("div", "field-label");
  const lab = node("label", "", label + (required ? " *" : ""));
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
  if (type === "number") input.step = "any";
  input.addEventListener("input", () => {
    change(
      path,
      type === "number" && input.value !== ""
        ? Number(input.value)
        : input.value,
    );
    wrap.classList.remove("invalid");
    input.removeAttribute("aria-invalid");
    wrap.querySelector(".field-error")?.remove();
  });
  controls.append(input);
  if (type === "password") {
    const reveal = button(
      "显示",
      () => {
        input.type = input.type === "password" ? "text" : "password";
        reveal.textContent = input.type === "password" ? "显示" : "隐藏";
      },
      "icon-button reveal",
    );
    reveal.setAttribute("aria-label", "显示或隐藏" + label);
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
  if (help) wrap.append(hint(help));
  const errors = state.errors.filter(
    (error) => JSON.stringify(error.path) === JSON.stringify(path),
  );
  if (errors.length) {
    wrap.classList.add("invalid");
    input.setAttribute("aria-invalid", "true");
    wrap.append(node("p", "field-error", errors.map(friendlyError).join("；")));
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
  if (id) {
    input.id = id;
    lab.htmlFor = id;
  }
  input.setAttribute("aria-label", label);
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
  e.append(node("span", "empty-icon", "＋"), node("h3", "", title), hint(text));
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
      "↑",
      () => {
        [items[index - 1], items[index]] = [items[index], items[index - 1]];
        dirty(true);
      },
      "icon-button",
    );
    up.disabled = index === 0;
    up.setAttribute("aria-label", `${label}上移第 ${index + 1} 项`);
    const down = button(
      "↓",
      () => {
        [items[index + 1], items[index]] = [items[index], items[index + 1]];
        dirty(true);
      },
      "icon-button",
    );
    down.disabled = index === items.length - 1;
    r.append(
      up,
      down,
      button(
        "×",
        () => {
          items.splice(index, 1);
          dirty(true);
        },
        "icon-button",
      ),
    );
    e.append(r);
  });
  e.append(
    button(
      "＋ 添加一项",
      () => change(path, [...items, ""], true),
      "text-button",
    ),
    hint(help),
  );
  return e;
}
export function envEditor(path) {
  const e = node("div", "env-editor");
  e.append(node("h3", "", "环境变量"));
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
    r.append(
      k,
      v,
      button(
        "×",
        () => {
          delete entries[key];
          dirty(true);
        },
        "icon-button",
      ),
    );
    e.append(r);
    if (typeof val !== "string")
      e.append(hint(`${key} 的原始值不是字符串；修改值后会按字符串保存。`));
  }
  e.append(
    button(
      "＋ 添加变量",
      () => {
        let key = "NEW_VAR";
        while (own(entries, key)) key += "_";
        put(entries, key, "");
        change(path, entries, true);
      },
      "text-button",
    ),
    hint("值按字面字符串传入；测试级变量覆盖系统级变量。"),
  );
  return e;
}
export function table(headers, rows) {
  const e = node("table");
  const thead = node("thead"),
    tr = node("tr");
  for (const title of headers) tr.append(node("th", "", title));
  thead.append(tr);
  e.append(thead);
  const body = node("tbody");
  for (const cells of rows) {
    const row = node("tr");
    for (const cell of cells) {
      const td = node("td");
      td.append(
        cell instanceof Node ? cell : node("span", "", String(cell ?? "—")),
      );
      if (!(cell instanceof Node)) td.title = String(cell ?? "—");
      row.append(td);
    }
    body.append(row);
  }
  e.append(body);
  return e;
}
export const date = (timestamp) =>
  timestamp
    ? new Date(timestamp * 1000).toLocaleString("zh-CN", { hour12: false })
    : "—";

export function friendlyError(error) {
  const messages = {
    missing: "请填写此项",
    string_type: "请输入文本",
    int_type: "请输入整数",
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
  if (error.message?.includes("boot_entry"))
    return "启动项需要是四位十六进制字符串";
  if (error.message?.includes("test names cannot"))
    return "测试名不能为空、包含空白或 NUL，也不能以 - 开头";
  return error.message;
}
