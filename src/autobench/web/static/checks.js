import { state, api, waitOperation, emit } from "./state.js";
import { mergedSSH, connectionTestSignature } from "./model.js";
import { node, icon, button, statusIndicator, updateStatusIndicator } from "./ui.js";
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
function fresh(machine) {
  const r = records.get(machine);
  if (!r || r.invalidated) return false;
  if (
    r.epoch !== state.epoch ||
    r.signature !==
      connectionTestSignature(
        machine,
        null,
        state.target,
        state.document.policy?.connect_timeout ?? 15,
      )
  )
    r.invalidated = true;
  return !r.invalidated;
}
async function testConnection(machine) {
  if (records.get(machine)?.running && fresh(machine)) return;
  const record = {
    running: true,
    signature: connectionTestSignature(
      machine,
      null,
      state.target,
      state.document.policy?.connect_timeout ?? 15,
    ),
    epoch: state.epoch,
    result: null,
    error: null,
  };
  records.set(machine, record);
  emit(true);
  try {
    const operation = await api("/api/connections/probe", {
      ssh: mergedSSH(machine),
      config_path: state.target || null,
      connect_timeout: state.document.policy?.connect_timeout ?? 15,
    });
    const result = await waitOperation(operation);
    if (records.get(machine) === record && fresh(machine))
      record.result = result;
  } catch (error) {
    if (records.get(machine) === record && fresh(machine))
      record.error = error.message;
  } finally {
    record.running = false;
    if (records.get(machine) === record) emit(true);
  }
}
export function connectionTestPanel(machine) {
  const panel = node("div", "connection-test-panel");
  panelTargets.set(panel, machine);
  const actions = node("div", "connection-test-actions");
  const connect = button(
    "连接测试",
    () => testConnection(machine),
    "button secondary",
  );
  connect.classList.add("connection-test-button");
  connect.prepend(icon("play"));
  connect.title = "检查连接及运行环境，不提交测试，也不切换操作系统。";
  const status = statusIndicator("connection-test-status");
  actions.append(connect, status);
  panel.append(actions);
  updateConnectionTestPanel(panel);
  return panel;
}
function updateConnectionTestPanel(panel) {
  const machine = panelTargets.get(panel);
  const r = records.get(machine),
    valid = fresh(machine);
  const ssh = mergedSSH(machine);
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
  updateStatusIndicator(
    status,
    phase,
    message,
    phase === "stale" ? "info" : undefined,
  );
  status.title = message;
}
export function updateConnectionTestPanels() {
  document
    .querySelectorAll(".connection-test-panel")
    .forEach(updateConnectionTestPanel);
}
