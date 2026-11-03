import { state, api, waitOperation, show, savedBody, emit } from "./state.js";
import { mergedSSH, probeSignature } from "./model.js";
import { $, node, button, hint, pill, heading, jsonDetails } from "./ui.js";
const records = new WeakMap();
const panelTargets = new WeakMap();
const labels = {
  connection: "SSH 连接",
  system: "系统匹配",
  uefi: "UEFI 启动项",
  runtime: "运行环境",
  dependencies: "基础命令",
  disk: "可用空间",
};
let onUse = () => {},
  onProblem = () => {};
export function configureChecks({ use, problem }) {
  onUse = use;
  onProblem = problem;
}
export function currentRecord(machine, system = null) {
  return records.get(system || machine);
}
export function fresh(machine, system = null) {
  const r = currentRecord(machine, system);
  return (
    !!r &&
    r.epoch === state.epoch &&
    r.signature ===
      probeSignature(
        machine,
        system,
        state.target,
        state.document.policy?.connect_timeout ?? 15,
      )
  );
}
export async function probe(machine, system = null) {
  const key = system || machine;
  if (currentRecord(machine, system)?.running && fresh(machine, system)) return;
  const signature = probeSignature(
      machine,
      system,
      state.target,
      state.document.policy?.connect_timeout ?? 15,
    ),
    epoch = state.epoch;
  const record = { running: true, signature, epoch, result: null, error: null };
  records.set(key, record);
  emit(true);
  try {
    const operation = await api("/api/connections/probe", {
      ssh: mergedSSH(machine, system),
      config_path: state.target || null,
      connect_timeout: state.document.policy?.connect_timeout ?? 15,
    });
    const result = await waitOperation(operation);
    if (records.get(key) !== record) return;
    record.result = result;
    if (epoch !== state.epoch || !fresh(machine, system)) record.stale = true;
    else if (result.probe) show("当前系统信息已读取，请确认后添加。");
    else show(result.checks[0]?.message || "连接未通过。", "error");
  } catch (error) {
    record.error = error.message;
    record.errorFields = error.fields || [];
  } finally {
    record.running = false;
    emit(true);
  }
}
export function checkRows(checks) {
  const e = node("div", "check-list");
  for (const item of checks) {
    const r = node("div", "check-row");
    r.dataset.tone =
      item.status === "passed"
        ? "success"
        : item.status === "unchecked"
          ? "neutral"
          : "error";
    const content = node("div");
    content.append(
      node("strong", "", labels[item.id] || item.label || item.id),
      hint(item.message),
    );
    r.append(
      node(
        "span",
        "check-icon",
        item.status === "passed"
          ? "✓"
          : item.status === "unchecked"
            ? "○"
            : "!",
      ),
      content,
    );
    r.append(
      pill(
        item.status === "passed"
          ? "通过"
          : item.status === "unchecked"
            ? "未检查"
            : "需处理",
        r.dataset.tone,
      ),
    );
    if (item.path)
      r.append(button("前往设置", () => onProblem(item.path), "link-button"));
    e.append(r);
  }
  return e;
}
export function probePanel(machine, system = null) {
  const panel = node("div", "probe-panel");
  panel.dataset.probePanel = "true";
  panelTargets.set(panel, { machine, system });
  const r = currentRecord(machine, system),
    valid = fresh(machine, system);
  const b = button(
    r?.running && valid ? "正在连接…" : "连接并识别当前系统",
    () => probe(machine, system),
    "button secondary",
  );
  b.classList.add("probe-connect");
  const ssh = mergedSSH(machine, system);
  b.disabled = (!!r?.running && valid) || !ssh.host || !ssh.username;
  panel.append(b, hint("只读取当前系统，不提交测试，也不切换操作系统。"));
  if (ssh.verify_host_key === false)
    panel.append(pill("已忽略主机密钥检查", "warning"));
  if (!r) return panel;
  if (!valid) {
    panel.append(
      node(
        "p",
        "callout warning stale-probe",
        "连接信息或配置位置已改变，请重新识别。",
      ),
    );
    return panel;
  }
  if (r.error) {
    panel.append(node("p", "callout error", r.error));
    for (const issue of r.errorFields || [])
      if (issue.path?.length)
        panel.append(
          button("前往设置", () => onProblem(issue.path), "link-button"),
        );
    return panel;
  }
  if (r.result) {
    const p = r.result.probe;
    panel.append(checkRows(r.result.checks));
    if (p) {
      const summary = node("div", "discovered-system");
      summary.append(
        node("strong", "", p.hostname || "当前系统"),
        hint(
          `当前启动项 ${p.efi?.current ? "Boot" + p.efi.current : "未识别"}`,
        ),
      );
      const usable =
        r.result.checks.some(
          (item) => item.id === "uefi" && item.status === "passed",
        ) && !!p.efi?.current;
      const use = button(
        system ? "更新此系统启动项" : "添加当前系统",
        () => {
          if (!fresh(machine, system))
            throw new Error("连接已改变，请重新识别。");
          onUse(machine, p, system);
        },
        "button primary",
      );
      use.disabled = !usable;
      summary.append(use);
      panel.append(summary, jsonDetails("查看原始识别结果", p));
    }
  }
  return panel;
}
export function updateProbePanels() {
  document.querySelectorAll("[data-probe-panel]").forEach((panel) => {
    const { machine, system } = panelTargets.get(panel) || {};
    if (!machine) return;
    const r0 = currentRecord(machine, system),
      ssh = mergedSSH(machine, system);
    const connect = panel.querySelector(".probe-connect");
    if (connect) {
      connect.disabled =
        (!!r0?.running && fresh(machine, system)) || !ssh.host || !ssh.username;
    }
    const r = currentRecord(machine, system);
    if (r && !fresh(machine, system) && !panel.querySelector(".stale-probe")) {
      panel.append(
        node(
          "p",
          "callout warning stale-probe",
          "连接信息或配置位置已改变，请重新识别。",
        ),
      );
      panel
        .querySelectorAll(".discovered-system button")
        .forEach((b) => (b.disabled = true));
    }
  });
}
export async function inspectSaved() {
  const operation = await api("/api/inspections", {
    ...savedBody(),
    kind: "doctor",
    machine: $("inspection-machine").value || null,
  });
  $("inspection-result").replaceChildren(
    node("p", "callout", "正在检查当前启动的系统…"),
  );
  const result = await waitOperation(operation);
  renderInspection(result);
}
export function renderInspection(results) {
  $("inspection-result").replaceChildren();
  for (const item of results) {
    const card = node("section", "panel");
    card.append(heading(item.machine, "检查只针对当前启动的系统。"));
    const p = item.probe;
    const connection = !!p;
    const current = item.systems?.find((s) => s.current);
    const checks = [
      {
        id: "connection",
        status: connection ? "passed" : "failed",
        message: connection
          ? item.verify_host_key === false
            ? "连接成功，已忽略主机密钥校验"
            : "连接成功"
          : (item.errors || []).join("；") || "无法连接",
      },
      {
        id: "system",
        status: !p ? "unchecked" : current ? "passed" : "failed",
        message: current
          ? `当前系统：${current.id}`
          : "当前启动项与配置不匹配或尚未读取",
      },
      {
        id: "uefi",
        status: !p ? "unchecked" : p.efi?.current ? "passed" : "failed",
        message: p?.efi?.current
          ? `BootCurrent ${p.efi.current}`
          : "尚未读取有效启动项",
      },
      {
        id: "runtime",
        status: !p ? "unchecked" : item.errors?.length ? "failed" : "passed",
        message: item.errors?.length
          ? item.errors.join("；")
          : "当前系统运行依赖通过",
      },
    ];
    card.append(checkRows(checks));
    for (const system of item.systems || [])
      card.append(
        node(
          "p",
          "system-check-summary",
          `${system.id} · ${system.current ? "当前系统，已核对启动项与依赖" : "未启动，启动项与依赖尚未检查"}`,
        ),
      );
    card.append(jsonDetails("技术详情与环境覆盖", item));
    $("inspection-result").append(card);
  }
}
export function renderChecksPage() {
  const machineSelect = $("inspection-machine"),
    old = machineSelect.value;
  machineSelect.replaceChildren(new Option("全部机器", ""));
  for (const m of state.document.machines || [])
    if (m && typeof m.id === "string")
      machineSelect.add(new Option(m.id, m.id));
  machineSelect.value = old;
  $("inspection-config").textContent = state.path || "当前配置尚未保存";
  $("doctor").disabled =
    state.dirty || !state.path || state.target !== state.path;
}
