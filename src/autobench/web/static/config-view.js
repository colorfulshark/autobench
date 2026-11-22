import {
  state,
  configSteps,
  reviewStep,
  change,
  dirty,
  emit,
  switchMode,
} from "./state.js";
import {
  at,
  own,
  put,
  copy,
  mergedSSH,
  authMode,
  applyAuth,
  authKeys,
  connectionKeys,
  trustKeys,
  overrides,
  resetGroup,
  newMachine,
  newSystem,
  newBenchmark,
  counts,
  policyDefaults,
  pathParent,
} from "./model.js";
import {
  $,
  node,
  icon,
  button,
  row,
  hint,
  heading,
  advanced,
  inputField,
  select,
  toggle,
  empty,
  listEditor,
  envEditor,
  table,
  friendlyError,
} from "./ui.js";
import { bootEntryField, updateBootEntryFields } from "./boot-entries.js";
import { chooseFile } from "./files.js";
import { connectionTestPanel, updateConnectionTestPanels } from "./checks.js";
import { unixbenchTestsEditor } from "./unixbench-tests.js";
const modes = new WeakMap(),
  credentials = new WeakMap();
const asList = (value) => (Array.isArray(value) ? value : []);
const machines = () => asList(state.document.machines);
const benchmarks = () => asList(state.document.benchmarks);
function navigate({
  machine = state.machine,
  system = state.system,
  benchmark = state.benchmark,
} = {}) {
  Object.assign(state, { machine, system, benchmark });
  emit(true);
}
function mutateGroup(path, keys) {
  const object = at(state.document, path);
  if (object && typeof object === "object") {
    resetGroup(object, keys);
    dirty(true);
  }
}
function fileStart(current) {
  return current?.startsWith("/") || current?.startsWith("~")
    ? current
    : (state.target ? pathParent(state.target) : state.info.base_dir) +
        "/" +
        (current || "");
}
async function browse(path, current, kind = "file") {
  const chosen = await chooseFile(fileStart(current), { kind });
  if (chosen) change(path, chosen, true);
}
function field(label, path, options = {}) {
  return inputField(label, path, {
    ...options,
    browse: options.file
      ? (p, v) => browse(p, v, options.file === true ? "file" : options.file)
      : null,
  });
}
function authEditor(path, effective, machineLayout = false) {
  const raw = at(state.document, path) || {};
  const mode = modes.get(raw) || authMode(effective);
  const box = node("section", "auth-editor");
  box.dataset.mode = mode;
  const switchAuth = (selected) => {
    const existing = at(state.document, path) || {};
    if (selected === "custom") {
      credentials.delete(existing);
      modes.set(existing, "custom");
      dirty(true);
      return;
    }
    const remembered = credentials.get(existing) || {};
    const password = own(existing, "password")
      ? existing.password
      : effective.password;
    const keys = own(existing, "client_keys")
      ? existing.client_keys
      : effective.client_keys;
    if (password !== null && password !== undefined)
      remembered.password = password;
    if (Array.isArray(keys) && keys.length) remembered.client_keys = copy(keys);
    credentials.set(existing, remembered);
    applyAuth(existing, selected, remembered);
    modes.set(existing, selected);
    change(path, existing, true);
  };
  box.append(
    select(
      "认证方式",
      [
        ["password", "密码认证"],
        ["key", "密钥认证"],
        ["custom", "自定义认证（高级）"],
      ],
      mode === "agent" ? "key" : mode,
      switchAuth,
      "auth-" + path.join("-"),
    ),
  );
  if (mode === "key" || mode === "agent")
    box.append(
      select(
        "密钥来源",
        [
          ["key", "服务器上的私钥文件"],
          ["agent", "服务器的 SSH Agent"],
        ],
        mode,
        switchAuth,
        "key-source-" + path.join("-"),
      ),
    );
  if (mode === "password")
    box.append(
      field("登录密码", [...path, "password"], {
        type: "password",
        fallback: effective.password ?? "",
        placeholder: machineLayout ? "请输入密码" : "",
        leadingIcon: machineLayout ? "lock" : "",
        required: true,
        help:
          !machineLayout && effective.password === "" && !!state.path
            ? "当前值为空密码；如非预期，请填写密码。"
            : "",
      }),
    );
  else if (mode === "key") {
    const keys = asList(raw.client_keys ?? effective.client_keys);
    const keyField = field("私钥文件", [...path, "client_keys", 0], {
      fallback: keys[0] || "",
      file: true,
      required: true,
      help: machineLayout
        ? ""
        : "选择 Web 服务器上的私钥文件。加密私钥可改用 SSH Agent。",
    });
    keyField.classList.add("span-full");
    box.append(keyField);
    box.append(
      advanced(
        "更多密钥",
        [[...path, "client_keys"]],
        (content) =>
          content.append(
            listEditor("密钥列表", [...path, "client_keys"], {
              browse: (current) => chooseFile(fileStart(current)),
              help: machineLayout ? "" : "支持多个密钥文件，顺序保持不变。",
            }),
          ),
        "keys-" + path.join("-"),
      ),
    );
  } else if (mode === "agent") {
    if (!machineLayout)
      box.append(
        node(
          "p",
          "callout",
          "使用 Web 服务所在服务器的 ssh-agent。请先在该服务器加载私钥。",
        ),
      );
  } else {
    if (!machineLayout)
      box.append(
        node(
          "p",
          "callout",
          "保留当前认证组合。修改下面的选项，或切换到一种单独的认证方式。",
        ),
      );
    box.append(
      field("密码（可选）", [...path, "password"], {
        fallback: effective.password ?? "",
        type: "password",
        reset: true,
        nullable: true,
      }),
    );
    box.append(
      listEditor("密钥列表", [...path, "client_keys"], {
        browse: (current) => chooseFile(fileStart(current)),
        help: machineLayout ? "" : "空列表表示不使用密钥文件。",
      }),
    );
    box.append(
      toggle("同时使用 SSH Agent", raw.agent ?? effective.agent, (checked) =>
        change([...path, "agent"], checked, true),
      ),
    );
  }
  return box;
}
function connectionFields(path, effective, machineLayout = false) {
  return row(
    field("主机地址", [...path, "host"], {
      fallback: effective.host || "",
      placeholder: "IP 地址或域名",
      required: true,
      width: "host-field",
      leadingIcon: machineLayout ? "globe" : "",
    }),
    field("端口", [...path, "port"], {
      fallback: effective.port ?? 22,
      type: "number",
      width: "port-field",
    }),
    field("用户名", [...path, "username"], {
      fallback: effective.username || "",
      placeholder: "例如 bench",
      required: true,
      width: "user-field",
      leadingIcon: machineLayout ? "user" : "",
    }),
  );
}
function proxyEditor(path, inherited = false) {
  const object = at(state.document, path) || {},
    has = own(object, "proxy"),
    proxy = object.proxy;
  const mode =
    inherited && !has
      ? "inherit"
      : proxy && typeof proxy === "object"
        ? "proxy"
        : "direct";
  const options = inherited
    ? [
        ["inherit", "沿用机器代理"],
        ["direct", "直连（不使用代理）"],
        ["proxy", "指定 SOCKS5 代理"],
      ]
    : [
        ["direct", "直连（不使用代理）"],
        ["proxy", "使用 SOCKS5 代理"],
      ];
  const e = node("section", "proxy-editor");
  const modeField = select(
    "连接代理",
    options,
    mode,
    (selected) =>
      change(
        [...path, "proxy"],
        selected === "inherit"
          ? undefined
          : selected === "direct"
            ? null
            : { host: "", port: 1080 },
        true,
      ),
    "proxy-" + path.join("-"),
  );
  if (!inherited) {
    const controls = node("div", "input-controls with-input-icon");
    controls.append(
      icon("chain", "icon input-icon"),
      modeField.querySelector("select"),
    );
    modeField.append(controls);
  }
  e.append(modeField);
  if (mode === "proxy") {
    e.append(
      row(
        field("代理地址", [...path, "proxy", "host"], { required: true }),
        field("端口", [...path, "proxy", "port"], {
          fallback: 1080,
          type: "number",
          width: "port-field",
        }),
      ),
    );
    const auth =
      (proxy.username !== undefined && proxy.username !== null) ||
      (proxy.password !== undefined && proxy.password !== null);
    e.append(
      toggle("代理需要用户名和密码", auth, (enabled) => {
        if (enabled) {
          change([...path, "proxy", "username"], "");
          change([...path, "proxy", "password"], "", true);
        } else {
          change([...path, "proxy", "username"], undefined);
          change([...path, "proxy", "password"], undefined, true);
        }
      }),
    );
    if (auth)
      e.append(
        row(
          field("代理用户名", [...path, "proxy", "username"]),
          field("代理密码", [...path, "proxy", "password"], {
            type: "password",
          }),
        ),
      );
  }
  return e;
}
function systemTrustFields(path, effective) {
  const e = node("div");
  const raw = at(state.document, path) || {};
  const checkPath = [...path, "verify_host_key"];
  const check = select(
    "主机密钥检查",
    [
      ["inherit", "沿用机器设置"],
      ["verify", "启用校验"],
      ["ignore", "忽略校验"],
    ],
    own(raw, "verify_host_key")
      ? raw.verify_host_key
        ? "verify"
        : "ignore"
      : "inherit",
    (mode) =>
      change(
        checkPath,
        mode === "inherit" ? undefined : mode === "verify",
        true,
      ),
    "trust-" + path.join("-"),
  );
  check.dataset.fieldPath = JSON.stringify(checkPath);
  e.append(check);
  if (effective.verify_host_key === false) {
    e.append(
      hint(
        "已忽略 SSH 服务器主机密钥检查；密码、私钥和 SSH Agent 认证照常进行。原有主机密钥文件和别名设置已保留。",
      ),
    );
    return e;
  }
  e.append(
    row(
      field("主机密钥文件", [...path, "known_hosts"], {
        fallback: effective.known_hosts ?? "~/.ssh/known_hosts",
        file: true,
        reset: true,
        help: "主机密钥需要预先核验并登记；不会自动接受未知密钥。",
      }),
      field("主机别名", [...path, "host_key_alias"], {
        fallback: effective.host_key_alias ?? "",
        reset: true,
        nullable: true,
        help: "清除会写入 null；恢复默认会重新继承。",
      }),
    ),
  );
  return e;
}
function machineSection(title, symbol, content) {
  const section = node("section", "machine-connection-section");
  const label = node("h2", "machine-connection-title");
  label.append(icon(symbol), node("span", "", title));
  section.append(label, content);
  return section;
}
function machineTrustCheck(ssh, effective) {
  const box = node("div", "machine-trust-check");
  const path = [...ssh, "verify_host_key"];
  box.dataset.fieldPath = JSON.stringify(path);
  const check = toggle(
    "忽略主机密钥检查",
    effective.verify_host_key === false,
    (ignore) => change(path, !ignore, true),
  );
  check.querySelector("input").id = "trust-" + ssh.join("-");
  box.append(check);
  return box;
}
function machineHostKeys(ssh, effective) {
  const knownHosts = field("主机密钥文件", [...ssh, "known_hosts"], {
    fallback: effective.known_hosts ?? "~/.ssh/known_hosts",
    file: true,
    reset: true,
    leadingIcon: "file",
    width: "machine-known-hosts",
  });
  const alias = field("主机别名", [...ssh, "host_key_alias"], {
    fallback: effective.host_key_alias ?? "",
    placeholder: "可选",
    reset: true,
    leadingIcon: "tag",
    width: "machine-host-alias",
  });
  const info = node("span", "machine-alias-info");
  info.title = "可选，用于区分同一地址的多个系统";
  info.setAttribute("aria-hidden", "true");
  info.append(icon("info"));
  alias.querySelector("label").append(info);
  const trust = node("fieldset", "machine-host-keys");
  trust.disabled = effective.verify_host_key === false;
  if (trust.disabled) {
    // Keep caret browsing from placing a caret in the disabled fields.
    trust.addEventListener("pointerdown", (event) => event.preventDefault());
  }
  trust.append(knownHosts, alias);
  return trust;
}
function machineEditor(machine, index) {
  const panel = node("div", "panel machine-connection");
  const path = ["machines", index],
    ssh = [...path, "ssh"],
    effective = mergedSSH(machine);
  const connection = connectionFields(ssh, effective, true);
  connection.classList.add("machine-connection-fields");
  connection.append(connectionTestPanel(machine, null, { compact: true }));
  const authentication = node("div", "machine-authentication");
  authentication.append(
    authEditor(ssh, effective, true),
    machineTrustCheck(ssh, effective),
  );
  panel.append(
    machineSection("连接信息", "chain", connection),
    machineSection("身份认证", "shield", authentication),
    machineSection("主机密钥校验", "key", machineHostKeys(ssh, effective)),
    machineSection("代理设置", "network", proxyEditor(ssh)),
    machineSection(
      "远端环境",
      "folder",
      field("远端工作目录", [...path, "remote_dir"], {
        fallback: ".local/state/autobench",
        reset: true,
        leadingIcon: "folder",
        width: "machine-remote-directory",
      }),
    ),
  );
  return panel;
}
function addMachine() {
  if (state.step !== 0) return;
  const items = machines();
  const machine = newMachine(items);
  change(["machines"], [...items, machine]);
  state.machine = items.length;
  state.system = null;
  emit(true);
}
function focusObject(kind, index) {
  const picker = $("config-body").querySelector(`.${kind}-picker`);
  (
    picker?.querySelectorAll(".chip-button")[index] ||
    picker?.querySelector(".text-button")
  )?.focus();
}
function moveMachine(machine, target) {
  if (state.step !== 0) return;
  const list = machines();
  const index = list.indexOf(machine),
    selected = list[state.machine];
  if (index < 0 || target < 0 || target >= list.length || index === target)
    return;
  list.splice(index, 1);
  list.splice(target, 0, machine);
  state.machine = list.indexOf(selected);
  dirty(true);
  focusObject("machine", target);
}
function removeMachine(machine) {
  if (state.step !== 0) return;
  const list = machines();
  const index = list.indexOf(machine),
    selected = list[state.machine];
  if (index < 0) return;
  list.splice(index, 1);
  if (selected === machine) {
    state.machine = Math.max(0, index - 1);
    state.system = null;
  } else state.machine = list.indexOf(selected);
  dirty(true);
  focusObject("machine", state.machine);
}
function addBenchmark() {
  if (state.step !== 2) return;
  const list = benchmarks();
  change(["benchmarks"], [...list, newBenchmark(list)]);
  state.benchmark = list.length;
  emit(true);
}
function moveBenchmark(benchmark, target) {
  if (state.step !== 2) return;
  const list = benchmarks();
  const index = list.indexOf(benchmark),
    selected = list[state.benchmark];
  if (index < 0 || target < 0 || target >= list.length || index === target)
    return;
  list.splice(index, 1);
  list.splice(target, 0, benchmark);
  state.benchmark = list.indexOf(selected);
  dirty(true);
  focusObject("benchmark", target);
}
function removeBenchmark(benchmark) {
  if (state.step !== 2) return;
  const list = benchmarks();
  const index = list.indexOf(benchmark),
    selected = list[state.benchmark];
  if (index < 0) return;
  list.splice(index, 1);
  state.benchmark =
    selected === benchmark ? Math.max(0, index - 1) : list.indexOf(selected);
  dirty(true);
  focusObject("benchmark", state.benchmark);
}
function addSystem(machine, index) {
  const list = asList(machine.systems),
    system = newSystem(list);
  change(["machines", index, "systems"], [...list, system]);
  state.system = list.length;
  state.machine = index;
  emit(true);
}
function editObjectId(label, items, item, path, trigger) {
  const dialog = $("id-dialog"),
    form = $("id-form"),
    input = $("id-value");
  $("id-title").textContent = `修改${label}标识`;
  $("id-label").textContent = `${label}标识`;
  input.value = item.id ?? "";
  input.setCustomValidity("");
  input.oninput = () => input.setCustomValidity("");
  $("id-cancel").onclick = () => dialog.close();
  dialog.addEventListener(
    "close",
    () => {
      if (!dialog.open) form.onsubmit = null;
    },
    { once: true },
  );
  form.onsubmit = (event) => {
    event.preventDefault();
    const id = input.value;
    if (id === item.id || at(state.document, path) !== item) {
      dialog.close();
      return;
    }
    if (items.some((other) => other !== item && other.id === id)) {
      input.setCustomValidity(`此${label}标识已存在。`);
      input.reportValidity();
      return;
    }
    dialog.close();
    change([...path, "id"], id, true);
    $(trigger.id)?.focus({ preventScroll: true });
  };
  dialog.showModal();
  input.select();
}
function objectActions({
  kind,
  label,
  name,
  items,
  item,
  path,
  onRemove,
  removeTitle = `删除${label} ${name}`,
}) {
  const inline = kind === "machine";
  const removeStyle = inline
    ? "machine-tab-action"
    : "object-corner object-remove";
  const editStyle = inline ? "machine-tab-action" : "object-corner object-edit";
  const remove = button("", onRemove, `${removeStyle} ${kind}-remove`);
  remove.setAttribute("aria-label", `删除${label} ${name}`);
  remove.title = removeTitle;
  remove.append(icon("error"));
  const edit = button("", null, `${editStyle} ${kind}-edit`);
  edit.id = "edit-id-" + path.join("-");
  edit.setAttribute("aria-label", `修改${label}标识 ${name}`);
  edit.setAttribute("aria-haspopup", "dialog");
  edit.title = `修改${label}标识`;
  edit.append(icon("edit"));
  edit.addEventListener("click", () =>
    editObjectId(label, items, item, path, edit),
  );
  return inline ? [edit, remove] : [remove, edit];
}
function systemEditor(machine, mi, system, si) {
  const panel = node("section", "panel");
  const path = ["machines", mi, "systems", si],
    ssh = [...path, "ssh"];
  const effective = mergedSSH(machine, system),
    raw = system.ssh || {};
  panel.append(heading("系统详情"));
  const fields = row(
    field("系统标识", [...path, "id"], {
      required: true,
      help: "使用字母、数字、下划线或连字符，例如 ubuntu。",
    }),
    bootEntryField(machine, system, [...path, "boot_entry"]),
  );
  panel.append(fields);
  panel.append(
    node(
      "div",
      "inherit-summary",
      `默认沿用 ${machine.id || "机器"} 的连接：${effective.username || "用户名未填"}@${effective.host || "地址未填"}:${effective.port}`,
    ),
  );
  const customConnection = overrides(raw, connectionKeys),
    customAuth = overrides(raw, authKeys);
  panel.append(
    toggle("单独设置此系统的连接地址或用户名", customConnection, (enabled) =>
      enabled
        ? change([...ssh, "host"], effective.host || "", true)
        : mutateGroup(ssh, connectionKeys),
    ),
  );
  if (customConnection)
    panel.append(
      connectionFields(ssh, effective),
      hint("未改动的字段继续沿用机器设置。"),
    );
  panel.append(
    toggle("单独设置此系统的认证方式", customAuth, (enabled) => {
      if (!enabled) {
        mutateGroup(ssh, authKeys);
        return;
      }
      const object = copy(raw);
      for (const key of authKeys) put(object, key, copy(effective[key]));
      change(ssh, object, true);
    }),
  );
  if (customAuth) panel.append(authEditor(ssh, effective));
  panel.append(
    advanced(
      "系统高级设置",
      [
        ...trustKeys.map((key) => [...ssh, key]),
        [...ssh, "proxy"],
        [...path, "env"],
      ],
      (content) => {
        content.append(
          proxyEditor(ssh, true),
          systemTrustFields(ssh, effective),
          envEditor([...path, "env"]),
        );
        const env = system.env || {};
        if (Object.keys(env).length) {
          content.append(hint("合并结果预览（测试级变量优先）"));
          for (const b of benchmarks())
            content.append(
              node(
                "pre",
                "code-block",
                `${b.id}\n` + JSON.stringify({ ...env, ...b.env }, null, 2),
              ),
            );
        }
      },
      "system-advanced-" + mi + "-" + si,
    ),
  );
  return panel;
}
function systemsBoard(machine, index) {
  const e = node("section", "systems-board");
  const actions = node("div", "actions");
  actions.append(
    button("＋ 手动添加系统", () => addSystem(machine, index), "text-button"),
  );
  e.append(heading("参与测试的系统", "", actions));
  const systems = asList(machine.systems);
  if (!systems.length)
    e.append(empty("尚未添加系统", "请手动添加系统并填写对应的 UEFI 启动项。"));
  systems.forEach((system, si) => {
    const r = node("div", "system-item");
    const name = system.id || `系统 ${si + 1}`;
    r.append(
      button(
        name,
        () => navigate({ machine: index, system: si }),
        "object-title",
      ),
      node(
        "span",
        /^[a-f\d]{4}$/i.test(system.boot_entry || "")
          ? "mono muted"
          : "mono danger",
        !system.boot_entry
          ? "待填写启动项"
          : /^[a-f\d]{4}$/i.test(system.boot_entry)
            ? "Boot" + system.boot_entry
            : "启动项无效：" + system.boot_entry,
      ),
    );
    const up = button(
      "↑",
      () => {
        [systems[si - 1], systems[si]] = [systems[si], systems[si - 1]];
        state.system = null;
        dirty(true);
      },
      "icon-button",
    );
    up.disabled = si === 0;
    up.setAttribute("aria-label", `上移系统 ${system.id}`);
    const down = button(
      "↓",
      () => {
        [systems[si + 1], systems[si]] = [systems[si], systems[si + 1]];
        state.system = null;
        dirty(true);
      },
      "icon-button",
    );
    down.disabled = si === systems.length - 1;
    down.setAttribute("aria-label", `下移系统 ${system.id}`);
    r.append(
      up,
      down,
      ...objectActions({
        kind: "system",
        label: "系统",
        name,
        items: systems,
        item: system,
        path: ["machines", index, "systems", si],
        onRemove: () => {
          systems.splice(si, 1);
          state.system = null;
          dirty(true);
          const board = $("config-body").querySelector(".systems-board");
          (
            board.querySelectorAll(".object-title")[Math.max(0, si - 1)] ||
            board.querySelector(".text-button")
          )?.focus();
        },
      }),
    );
    e.append(r);
  });
  return e;
}
function benchmarkEditor(b, index) {
  const panel = node("section", "panel");
  const path = ["benchmarks", index];
  panel.append(heading("UnixBench 测试"));
  panel.append(
    row(
      field("服务器源码包", [...path, "source_archive"], {
        file: true,
        required: true,
        placeholder: "tar / tar.gz 文件路径",
        help: "相对路径以 YAML 所在目录为准。",
        width: "grow",
      }),
    ),
  );
  const rounds = node("div", "benchmark-rounds");
  const warmup = node("div", "field warmup-field");
  warmup.dataset.fieldPath = JSON.stringify([...path, "warmup"]);
  const control = node("label", "warmup-setting");
  const title = node("label", "field-label", "预热");
  const input = node("input");
  input.type = "checkbox";
  input.checked = b.warmup ?? true;
  input.addEventListener("change", () => {
    change([...path, "warmup"], input.checked);
    warmup.classList.remove("invalid");
    input.removeAttribute("aria-invalid");
    warmup.querySelector(".field-error")?.remove();
  });
  input.id = `input-benchmarks-${index}-warmup`;
  title.id = input.id + "-label";
  title.htmlFor = input.id;
  input.setAttribute("aria-labelledby", title.id);
  const description = hint("先完整运行 1 轮，预热结果不保存。");
  description.id = input.id + "-help";
  input.setAttribute("aria-describedby", description.id);
  control.append(input);
  warmup.append(title, control, description);
  const errors = state.errors.filter(
    (error) => JSON.stringify(error.path) === warmup.dataset.fieldPath,
  );
  if (errors.length) {
    warmup.classList.add("invalid");
    input.setAttribute("aria-invalid", "true");
    warmup.append(
      node("p", "field-error", errors.map(friendlyError).join("；")),
    );
  }
  rounds.append(
    field("正式轮数", [...path, "repetitions"], {
      fallback: 1,
      type: "number",
      help: "仅保存正式结果。",
    }),
    warmup,
    node("p", "hint rounds-note", "预热与每轮正式测试前，均等待系统稳定。"),
  );
  panel.append(rounds);
  panel.append(unixbenchTestsEditor([...path, "tests"]));
  panel.append(
    advanced(
      "高级测试设置",
      [
        "id",
        "type",
        "source_subdir",
        "source_sha256",
        "make_args",
        "args",
        "env",
        "build_timeout",
        "timeout",
      ].map((key) => [...path, key]),
      (content) => {
        content.append(
          row(
            field("测试标识", [...path, "id"]),
            field("测试类型", [...path, "type"], {
              fallback: "unixbench",
              help: "当前支持 unixbench。",
            }),
          ),
          row(
            field("源码子目录", [...path, "source_subdir"], {
              reset: true,
              nullable: true,
              help: "默认自动识别唯一的 UnixBench 目录。",
            }),
            field("源码 SHA256", [...path, "source_sha256"], {
              reset: true,
              nullable: true,
            }),
          ),
          row(
            field("编译超时（秒）", [...path, "build_timeout"], {
              fallback: 1800,
              type: "number",
              reset: true,
            }),
            field("运行超时（秒）", [...path, "timeout"], {
              fallback: 21600,
              type: "number",
              reset: true,
            }),
          ),
          listEditor("编译参数", [...path, "make_args"]),
          listEditor("运行参数", [...path, "args"]),
          envEditor([...path, "env"]),
        );
      },
      "benchmark-advanced-" + index,
    ),
  );
  return panel;
}
function policyEditor() {
  const panel = node("section", "panel");
  panel.append(heading("运行设置"));
  panel.append(
    row(
      field("结果输出目录", ["policy", "output_dir"], {
        fallback: "results",
        file: "directory",
      }),
      field("并行机器数", ["policy", "parallel_machines"], {
        fallback: 4,
        type: "number",
        width: "short-field",
      }),
    ),
  );
  panel.append(
    advanced(
      "高级调度设置",
      [
        "state_dir",
        "poll_interval",
        "settle_seconds",
        "reconnect_timeout",
        "boot_timeout",
        "connect_timeout",
      ].map((key) => ["policy", key]),
      (content) => {
        content.append(
          field("状态目录", ["policy", "state_dir"], {
            fallback: policyDefaults.state_dir,
            file: "directory",
            reset: true,
            help: "恢复运行时须使用创建该运行时的状态目录。",
          }),
        );
        for (const [a, b] of [
          ["poll_interval", "settle_seconds"],
          ["reconnect_timeout", "boot_timeout"],
          ["connect_timeout", null],
        ]) {
          const names = {
            poll_interval: "轮询间隔",
            settle_seconds: "系统稳定等待",
            reconnect_timeout: "重连超时",
            boot_timeout: "启动超时",
            connect_timeout: "SSH 连接超时",
          };
          const fields = [a, b].filter(Boolean).map((key) =>
            field(names[key] + "（秒）", ["policy", key], {
              fallback: policyDefaults[key],
              type: "number",
              reset: true,
            }),
          );
          content.append(row(...fields));
        }
        content.append(
          field("配置版本", ["version"], {
            fallback: 1,
            type: "number",
            help: "当前支持版本 1。",
          }),
        );
      },
      "policy-advanced",
    ),
  );
  return panel;
}
export function review() {
  const panel = node("section", "panel review-panel");
  const n = counts(state.document);
  panel.append(
    heading(
      "检查测试计划",
      "检查配置时不会连接或重启机器。开始测试后，调度器会按计划切换系统。",
    ),
  );
  const stats = node("div", "stat-grid");
  for (const [label, count] of [
    ["机器", n.machines],
    ["系统", n.systems],
    ["测试", n.benchmarks],
    ["任务", n.jobs],
  ]) {
    const item = node("div", "stat");
    item.append(node("strong", "", count), node("span", "", label));
    stats.append(item);
  }
  panel.append(stats);
  const rows = [];
  for (const m of machines())
    for (const s of asList(m.systems))
      for (const b of benchmarks()) {
        const scope = node("span", "review-scope");
        scope.append(
          node("span", "mono", m.id),
          node("span", "secondary", s.id),
        );
        rows.push([
          scope,
          b.id,
          `${b.repetitions ?? 1} 轮`,
          (b.warmup ?? true) ? "1 轮预热" : "关闭",
        ]);
      }
  const wrap = node("div", "table-scroll");
  const plan = table(["机器 / 系统", "测试", "正式轮数", "预热"], rows);
  plan.classList.add("review-plan-table");
  wrap.append(plan);
  panel.append(
    rows.length ? wrap : hint("配置机器、系统和测试后，此处会列出测试任务。"),
  );
  panel.append(
    node(
      "div",
      "review-paths",
      `结果目录 ${state.document.policy?.output_dir ?? "results"}`,
    ),
  );
  return panel;
}
function objectTabs({
  kind,
  label,
  collection,
  items,
  selected,
  editable,
  onSelect,
  onMove,
  onRemove,
  onAdd,
  removeTitle = (name) => `删除${label} ${name}`,
}) {
  const e = node("div", `machine-tabs ${kind}-picker`);
  let draggedItem = null;
  const clearDropTargets = () => {
    e.querySelectorAll(".drop-target").forEach((tab) =>
      tab.classList.remove("drop-target"),
    );
  };
  items.forEach((item, index) => {
    const tab = node("div", `object-tab ${kind}-tab`);
    const name = item.id || `${label} ${index + 1}`;
    const b = button(
      name,
      () => {
        onSelect(index);
        emit(true);
      },
      "chip-button",
    );
    b.classList.toggle("active", index === selected);
    b.setAttribute("aria-pressed", String(index === selected));
    if (kind === "machine" && editable) b.prepend(icon("database"));
    tab.append(b);
    if (editable) {
      b.draggable = true;
      b.title = "拖动调整顺序；Alt + ← / → 前后移动";
      b.setAttribute("aria-keyshortcuts", "Alt+ArrowLeft Alt+ArrowRight");
      b.addEventListener("keydown", (event) => {
        if (!event.altKey || !["ArrowLeft", "ArrowRight"].includes(event.key))
          return;
        event.preventDefault();
        onMove(
          item,
          items.indexOf(item) + (event.key === "ArrowLeft" ? -1 : 1),
        );
      });
      b.addEventListener("dragstart", (event) => {
        draggedItem = item;
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", name);
        tab.classList.add("dragging");
      });
      b.addEventListener("dragend", () => {
        draggedItem = null;
        tab.classList.remove("dragging");
        clearDropTargets();
      });
      tab.addEventListener("dragover", (event) => {
        if (!draggedItem || draggedItem === item) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        clearDropTargets();
        tab.classList.add("drop-target");
      });
      tab.addEventListener("dragleave", (event) => {
        if (!tab.contains(event.relatedTarget))
          tab.classList.remove("drop-target");
      });
      tab.addEventListener("drop", (event) => {
        if (!draggedItem) return;
        event.preventDefault();
        const source = draggedItem;
        draggedItem = null;
        clearDropTargets();
        onMove(source, items.indexOf(item));
      });
      tab.append(
        ...objectActions({
          kind,
          label,
          name,
          items,
          item,
          path: [collection, index],
          onRemove: () => onRemove(item),
          removeTitle: removeTitle(name),
        }),
      );
    }
    e.append(tab);
  });
  if (editable) e.append(button(`＋ 添加${label}`, onAdd, "text-button"));
  return e;
}
function machineTabs() {
  const tabs = objectTabs({
    kind: "machine",
    label: "机器",
    collection: "machines",
    items: machines(),
    selected: state.machine,
    editable: state.step === 0,
    onSelect: (index) => {
      state.machine = index;
      state.system = null;
    },
    onMove: moveMachine,
    onRemove: removeMachine,
    onAdd: addMachine,
    removeTitle: (name) => `删除机器 ${name} 及其系统配置`,
  });
  if (state.step === 0) tabs.classList.add("machine-connection-tabs");
  return tabs;
}
function benchmarkTabs() {
  return objectTabs({
    kind: "benchmark",
    label: "测试",
    collection: "benchmarks",
    items: benchmarks(),
    selected: state.benchmark,
    editable: state.step === 2,
    onSelect: (index) => {
      state.benchmark = index;
    },
    onMove: moveBenchmark,
    onRemove: removeBenchmark,
    onAdd: addBenchmark,
  });
}
export function renderConfig() {
  const container = $("config-body");
  container.replaceChildren();
  const sourceMode = state.mode === "yaml";
  const actions = $(sourceMode ? "source-actions" : "wizard-actions");
  const primary = $("primary-action");
  if (primary.parentElement !== actions) actions.append(primary);
  const picker = $("step-picker");
  const stepName = configSteps[state.step];
  const progress = `${state.step + 1}/${configSteps.length}`;
  $("wizard-steps").hidden = sourceMode;
  picker.hidden = sourceMode;
  picker.setAttribute("aria-label", `配置步骤 ${progress} · ${stepName}`);
  $("step-picker-label").textContent = stepName;
  $("step-picker-count").textContent = progress;
  if (sourceMode) setStepPicker(false);
  $("wizard-options").replaceChildren(
    ...configSteps.map((text, index) => {
      const b = button("", () => goToStep(index), "wizard-step");
      b.id = `wizard-step-${index}`;
      if (index === state.step) b.setAttribute("aria-current", "step");
      const number = node("span", "step-number", index + 1);
      b.append(number, node("span", "", text));
      return b;
    }),
  );
  $("yaml-panel").hidden = !sourceMode;
  container.hidden = sourceMode;
  if (sourceMode) return;
  if (
    !state.document ||
    typeof state.document !== "object" ||
    Array.isArray(state.document)
  ) {
    container.append(empty("配置结构无法显示", "请在 YAML 中修正顶层结构。"));
    return;
  }
  const isObject = (value) =>
    value !== null && typeof value === "object" && !Array.isArray(value);
  const malformed =
    ["machines", "benchmarks"].some(
      (key) =>
        own(state.document, key) &&
        (!Array.isArray(state.document[key]) ||
          state.document[key].some((item) => !isObject(item))),
    ) ||
    machines().some(
      (m) =>
        own(m, "systems") &&
        (!Array.isArray(m.systems) || m.systems.some((s) => !isObject(s))),
    );
  if (malformed) {
    container.append(
      empty(
        "配置结构需要修正",
        "机器、系统和测试应为对象列表；原始内容已完整保留，请切换 YAML 修正。",
      ),
    );
    return;
  }
  state.machine = Math.max(0, Math.min(state.machine, machines().length - 1));
  state.benchmark = Math.max(
    0,
    Math.min(state.benchmark, benchmarks().length - 1),
  );
  const m = machines()[state.machine];
  if (state.step === 0 || state.step === 1) container.append(machineTabs());
  if (state.step === 0)
    container.append(
      m
        ? machineEditor(m, state.machine)
        : empty(
            "添加第一台机器",
            "填写连接信息后可测试连接及运行环境。",
            button("添加机器", addMachine, "button primary"),
          ),
    );
  if (state.step === 1) {
    if (m) {
      if (state.system !== null && asList(m.systems)[state.system]) {
        container.append(
          button(
            "← 返回系统列表",
            () => {
              state.system = null;
              emit(true);
            },
            "text-button",
          ),
          systemEditor(m, state.machine, m.systems[state.system], state.system),
        );
      } else {
        const p = node("section", "panel");
        p.append(systemsBoard(m, state.machine), connectionTestPanel(m));
        container.append(p);
      }
    } else container.append(empty("请先添加机器", "返回上一步填写连接信息。"));
  }
  if (state.step === 2) {
    container.append(benchmarkTabs());
    const b = benchmarks()[state.benchmark];
    if (b) container.append(benchmarkEditor(b, state.benchmark));
    else
      container.append(empty("尚未添加测试", "点击添加测试来配置 UnixBench。"));
  }
  if (state.step === 3) container.append(policyEditor());
  if (state.step === reviewStep) container.append(review());
}

export function setStepPicker(open, restoreFocus = false) {
  const picker = $("step-picker");
  if (picker.getAttribute("aria-expanded") === String(open)) return;
  picker.setAttribute("aria-expanded", String(open));
  if (open) $("wizard-steps").querySelector("[aria-current=step]")?.focus();
  if (restoreFocus) picker.focus({ preventScroll: true });
}

export function stepGate() {
  for (let index = 0; index < reviewStep; index++) {
    const problems = stepProblems(index);
    if (problems.length) return { index, problems };
  }
  return { index: reviewStep, problems: [] };
}

export function canGoToStep(index, limit) {
  return index <= state.step || index <= (limit ?? stepGate().index);
}

export function goToStep(index, focusContent = false) {
  if (!canGoToStep(index)) return;
  if (index !== state.step || state.system !== null) {
    state.step = index;
    state.system = null;
    emit(true);
  }
  window.scrollTo(0, 0);
  setStepPicker(false, !focusContent);
  if (focusContent) {
    const title = $("config-body").querySelector("h2, h3");
    if (title) {
      title.tabIndex = -1;
      title.focus({ preventScroll: true });
    }
  }
}

export function nextStep() {
  const { index, problems } = stepGate();
  if (canGoToStep(state.step + 1, index)) {
    goToStep(state.step + 1, true);
    return;
  }
  if (index !== state.step) state.system = null;
  state.step = index;
  state.errors = problems;
  emit(true);
  setStepPicker(false);
  window.scrollTo(0, 0);
  $("config-errors").querySelector("button")?.focus();
}

function stepProblems(step) {
  const problems = [];
  if (step === 0) {
    if (!machines().length)
      problems.push({ path: ["machines"], message: "请添加至少一台机器。" });
    machines().forEach((m, i) => {
      if (!m?.ssh?.host)
        problems.push({
          path: ["machines", i, "ssh", "host"],
          message: "请填写主机地址。",
        });
      if (!m?.ssh?.username)
        problems.push({
          path: ["machines", i, "ssh", "username"],
          message: "请填写登录用户名。",
        });
    });
  }
  if (step === 1)
    machines().forEach((m, i) => {
      if (!asList(m.systems).length)
        problems.push({
          path: ["machines", i, "systems"],
          message: `请为 ${m.id} 添加至少一个系统。`,
        });
      asList(m.systems).forEach((s, j) => {
        if (!/^[a-f\d]{4}$/i.test(s?.boot_entry || ""))
          problems.push({
            path: ["machines", i, "systems", j, "boot_entry"],
            message: "启动项应为四位十六进制字符串。",
          });
      });
    });
  if (step === 2) {
    if (!benchmarks().length)
      problems.push({ path: ["benchmarks"], message: "请添加至少一项测试。" });
    benchmarks().forEach((b, i) => {
      if (!b?.source_archive)
        problems.push({
          path: ["benchmarks", i, "source_archive"],
          message: "请选择服务器上的源码包。",
        });
    });
  }
  return problems;
}
const fieldNames = {
  machines: "机器",
  systems: "系统",
  benchmarks: "测试",
  policy: "运行设置",
  ssh: "连接",
  host: "地址",
  username: "用户名",
  password: "密码",
  client_keys: "私钥",
  known_hosts: "主机校验",
  verify_host_key: "主机密钥检查",
  id: "标识",
  boot_entry: "启动项",
  source_archive: "源码包",
  tests: "测试项",
  repetitions: "正式轮数",
  warmup: "预热",
};
export function errorLabel(error) {
  return error.path?.length
    ? error.path
        .map((part) =>
          typeof part === "number"
            ? String(part + 1)
            : fieldNames[part] || part,
        )
        .join(" / ")
    : "配置检查";
}
export async function focusError(error) {
  if (
    error.code === "extra_forbidden" ||
    (error.path?.length &&
      !["machines", "benchmarks", "policy", "version"].includes(error.path[0]))
  ) {
    await switchMode("yaml");
    $("yaml-editor").focus();
    return;
  }
  if (state.mode === "yaml") {
    const text = $("yaml-editor");
    text.focus();
    if (error.line) {
      const start =
        text.value
          .split("\n")
          .slice(0, error.line - 1)
          .join("\n").length + (error.line > 1 ? 1 : 0);
      text.setSelectionRange(start, start);
    }
    return;
  }
  const path = error.path || [];
  if (path[0] === "machines") {
    state.step = path[2] === "systems" ? 1 : 0;
    state.machine = Number(path[1]) || 0;
    state.system =
      path[2] === "systems" && path[3] !== undefined
        ? Number(path[3]) || 0
        : null;
  } else if (path[0] === "benchmarks") {
    state.step = 2;
    state.benchmark = Number(path[1]) || 0;
  } else if (path[0] === "policy" || path[0] === "version") {
    state.step = 3;
  }
  emit(true);
  requestAnimationFrame(() => {
    const field = [...document.querySelectorAll("[data-field-path]")].find(
      (e) => e.dataset.fieldPath === JSON.stringify(path),
    );
    if (field) {
      for (let p = field.parentElement; p; p = p.parentElement)
        if (p.tagName === "DETAILS") p.open = true;
      field.scrollIntoView({ block: "center", behavior: "smooth" });
      field.querySelector("input,textarea,select")?.focus();
    } else if (
      path[0] === "machines" &&
      path.length === 3 &&
      path[2] === "id"
    ) {
      $(`edit-id-machines-${state.machine}`)?.focus();
    }
  });
}
export function updateConfig(limit) {
  configSteps.forEach((_, index) => {
    const control = $(`wizard-step-${index}`);
    control.disabled = !canGoToStep(index, limit);
    control.title = control.disabled ? `请先完成「${configSteps[limit]}」` : "";
  });
  updateConnectionTestPanels();
  updateBootEntryFields();
}
