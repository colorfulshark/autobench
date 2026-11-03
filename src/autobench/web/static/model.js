// Configuration adapters only patch fields touched by the user. UI state never enters YAML.
export const own = (value, key) =>
  value !== null && typeof value === "object" && Object.hasOwn(value, key);
export const copy = (value) => structuredClone(value);
export function put(object, key, value) {
  Object.defineProperty(object, key, {
    value,
    writable: true,
    configurable: true,
    enumerable: true,
  });
}
export function at(document, path) {
  return path.reduce(
    (value, key) => (own(value, key) ? value[key] : undefined),
    document,
  );
}
export function patch(document, path, value) {
  let parent = document;
  path.slice(0, -1).forEach((key, index) => {
    if (
      !own(parent, key) ||
      parent[key] === null ||
      typeof parent[key] !== "object"
    )
      put(parent, key, typeof path[index + 1] === "number" ? [] : {});
    parent = parent[key];
  });
  if (value === undefined) delete parent[path.at(-1)];
  else put(parent, path.at(-1), value);
}
export const sshDefaults = {
  port: 22,
  password: null,
  client_keys: [],
  agent: true,
  verify_host_key: true,
  known_hosts: "~/.ssh/known_hosts",
  host_key_alias: null,
  proxy: null,
};
export const policyDefaults = {
  parallel_machines: 4,
  poll_interval: 10,
  settle_seconds: 60,
  reconnect_timeout: 900,
  boot_timeout: 600,
  connect_timeout: 15,
  state_dir: "~/.local/state/autobench",
  output_dir: "results",
};
export const benchmarkDefaults = {
  type: "unixbench",
  repetitions: 1,
  make_args: [],
  args: [],
  env: {},
  source_subdir: null,
  source_sha256: null,
  build_timeout: 1800,
  timeout: 21600,
};
export const authKeys = ["password", "client_keys", "agent"];
export const connectionKeys = ["host", "port", "username"];
export const trustKeys = ["verify_host_key", "known_hosts", "host_key_alias"];
export function mergedSSH(machine, system = null) {
  return { ...sshDefaults, ...(machine?.ssh || {}), ...(system?.ssh || {}) };
}
export function authMode(ssh) {
  const values = { ...sshDefaults, ...ssh };
  const password = values.password !== null && values.password !== undefined;
  const keys =
    Array.isArray(values.client_keys) && values.client_keys.length > 0;
  if (password && (keys || values.agent)) return "custom";
  if (password) return "password";
  if (keys && values.agent) return "custom";
  if (keys) return "key";
  if (values.agent) return "agent";
  return "custom";
}
export function applyAuth(ssh, mode, remembered = {}) {
  if (mode === "custom") return;
  const password = remembered.password ?? ssh.password ?? "";
  const keys = remembered.client_keys ?? ssh.client_keys ?? [];
  put(ssh, "password", mode === "password" ? password : null);
  put(ssh, "client_keys", mode === "key" ? copy(keys) : []);
  put(ssh, "agent", mode === "agent");
}
export function resetGroup(ssh, keys) {
  for (const key of keys) delete ssh[key];
}
export const overrides = (ssh, keys) => keys.some((key) => own(ssh, key));
export function nextID(items, prefix) {
  let index = 1;
  while (
    items.some((item) => item?.id === prefix + String(index).padStart(2, "0"))
  )
    index++;
  return prefix + String(index).padStart(2, "0");
}
export function newMachine(items = []) {
  return {
    id: nextID(items, "server"),
    ssh: {
      host: "",
      username: "",
      password: "",
      client_keys: [],
      agent: false,
    },
    systems: [],
  };
}
export const newSystem = (items = []) => ({
  id: nextID(items, "linux"),
  boot_entry: "",
});
export const newBenchmark = (items = []) => ({
  id: nextID(items, "unixbench"),
  type: "unixbench",
  source_archive: "",
});
export function newProject() {
  return { version: 1, machines: [newMachine()], benchmarks: [newBenchmark()] };
}
export function counts(document) {
  const machines = Array.isArray(document.machines) ? document.machines : [];
  const benchmarks = Array.isArray(document.benchmarks)
    ? document.benchmarks
    : [];
  const systems = machines.reduce(
    (n, m) => n + (Array.isArray(m?.systems) ? m.systems.length : 0),
    0,
  );
  const jobs = machines.reduce(
    (n, m) =>
      n +
      (m?.systems?.length || 0) *
        benchmarks.reduce(
          (sum, b) => sum + (Number(b?.repetitions ?? 1) || 0),
          0,
        ),
    0,
  );
  return {
    machines: machines.length,
    systems,
    benchmarks: benchmarks.length,
    jobs,
  };
}
export function pathParent(path) {
  return path?.includes("/")
    ? path.slice(0, path.lastIndexOf("/")) || "/"
    : ".";
}
export function configStateDir(document, target, baseDir) {
  const value = document.policy?.state_dir ?? policyDefaults.state_dir;
  if (typeof value !== "string")
    throw new Error("状态目录必须是服务器上的路径字符串。");
  return value.startsWith("/") || value.startsWith("~")
    ? value
    : (target ? pathParent(target) : baseDir) + "/" + value;
}
export function probeSignature(machine, system, target, timeout) {
  return JSON.stringify({
    ssh: mergedSSH(machine, system),
    base: pathParent(target),
    timeout,
  });
}
export const statusLabels = {
  pending: "待执行",
  preparing: "准备中",
  submitting: "提交中",
  running: "运行中",
  switching: "切换系统",
  stabilizing: "系统稳定等待",
  collecting: "收集中",
  succeeded: "成功",
  failed: "失败",
  paused: "已暂停",
  cancel_pending: "等待取消",
  cancelled: "已取消",
  blocked: "已阻塞",
  interrupted: "已中断",
  completed: "已完成",
  stopped: "控制端已停止",
};
export const statusTone = (status) =>
  ["failed", "interrupted"].includes(status)
    ? "error"
    : ["paused", "cancel_pending", "blocked"].includes(status)
      ? "warning"
      : [
            "running",
            "preparing",
            "submitting",
            "switching",
            "collecting",
          ].includes(status)
        ? "active"
        : status === "succeeded" || status === "passed"
          ? "success"
          : "neutral";
