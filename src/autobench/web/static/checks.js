import { state, api, waitOperation, emit } from "./state.js";
import { mergedSSH, connectionTestSignature } from "./model.js";
import {
  node,
  button,
  hint,
  pill,
  statusIndicator,
  updateStatusIndicator,
} from "./ui.js";
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
function currentRecord(machine, system = null) {
  return records.get(system || machine);
}
function fresh(machine, system = null) {
  const r = currentRecord(machine, system);
  if (!r || r.invalidated) return false;
  if (
    r.epoch !== state.epoch ||
    r.signature !==
      connectionTestSignature(
        machine,
        system,
        state.target,
        state.document.policy?.connect_timeout ?? 15,
      )
  )
    r.invalidated = true;
  return !r.invalidated;
}
async function testConnection(machine, system = null) {
  const key = system || machine;
  if (currentRecord(machine, system)?.running && fresh(machine, system)) return;
  const record = {
    running: true,
    signature: connectionTestSignature(
      machine,
      system,
      state.target,
      state.document.policy?.connect_timeout ?? 15,
    ),
    epoch: state.epoch,
    result: null,
    error: null,
  };
  records.set(key, record);
  emit(true);
  try {
    const operation = await api("/api/connections/probe", {
      ssh: mergedSSH(machine, system),
      config_path: state.target || null,
      connect_timeout: state.document.policy?.connect_timeout ?? 15,
    });
    const result = await waitOperation(operation);
    if (records.get(key) === record && fresh(machine, system))
      record.result = result;
  } catch (error) {
    if (records.get(key) === record && fresh(machine, system))
      record.error = error.message;
  } finally {
    record.running = false;
    if (records.get(key) === record) emit(true);
  }
}
export function connectionTestPanel(machine, system = null) {
  const panel = node("div", "connection-test-panel");
  panelTargets.set(panel, { machine, system });
  const actions = node("div", "connection-test-actions");
  const connect = button(
    "连接测试",
    () => testConnection(machine, system),
    "button secondary",
  );
  connect.classList.add("connection-test-button");
  const status = statusIndicator("connection-test-status");
  actions.append(connect, status);
  panel.append(
    actions,
    hint("检查连接及运行环境，不提交测试，也不切换操作系统。"),
  );
  const ssh = mergedSSH(machine, system);
  if (ssh.verify_host_key === false)
    panel.append(pill("已忽略主机密钥检查", "warning"));
  updateConnectionTestPanel(panel);
  return panel;
}
function updateConnectionTestPanel(panel) {
  const { machine, system } = panelTargets.get(panel);
  const r = currentRecord(machine, system),
    valid = fresh(machine, system);
  const ssh = mergedSSH(machine, system);
  const running = !!r?.running && valid;
  panel.querySelector(".connection-test-button").disabled =
    running || !ssh.host || !ssh.username;
  const status = panel.querySelector(".connection-test-status");
  let phase = "idle",
    message = "";
  if (r && !valid) {
    phase = "stale";
    message = "连接信息或配置位置已改变，请重新测试。";
  } else if (running) {
    phase = "running";
    message = "正在测试连接";
  } else if (r?.error) {
    phase = "error";
    message = r.error;
  } else if (r?.result) {
    const checks = r.result.checks || [];
    const failed = checks.filter((item) => item.status !== "passed");
    phase = checks.length && !failed.length ? "success" : "error";
    message =
      phase === "success"
        ? "连接测试通过"
        : failed
            .map(
              (item) =>
                `${labels[item.id] || item.label || item.id}：${item.message}`,
            )
            .join("；") || "未获取到检查结果，请重试。";
  }
  updateStatusIndicator(status, phase, message);
}
export function updateConnectionTestPanels() {
  document
    .querySelectorAll(".connection-test-panel")
    .forEach(updateConnectionTestPanel);
}
