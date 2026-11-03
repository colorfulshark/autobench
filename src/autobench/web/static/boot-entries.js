import { state, api, waitOperation, change, emit } from "./state.js";
import { mergedSSH, connectionTestSignature } from "./model.js";
import {
  node,
  button,
  inputField,
  statusIndicator,
  updateStatusIndicator,
} from "./ui.js";

const records = new WeakMap();
const targets = new WeakMap();
function fresh(machine, system) {
  const record = records.get(system);
  if (!record || record.invalidated) return false;
  if (
    record.epoch !== state.epoch ||
    record.signature !==
      connectionTestSignature(
        machine,
        system,
        state.target,
        state.document.policy?.connect_timeout ?? 15,
      )
  )
    record.invalidated = true;
  return !record.invalidated;
}
async function identify(machine, system) {
  const previous = records.get(system);
  if (previous?.running && fresh(machine, system)) return;
  const record = {
    running: true,
    signature: connectionTestSignature(
      machine,
      system,
      state.target,
      state.document.policy?.connect_timeout ?? 15,
    ),
    epoch: state.epoch,
    entries: fresh(machine, system) ? previous?.entries : null,
    error: null,
  };
  records.set(system, record);
  emit(true);
  try {
    const operation = await api("/api/systems/boot-entries", {
      ssh: mergedSSH(machine, system),
      config_path: state.target || null,
      connect_timeout: state.document.policy?.connect_timeout ?? 15,
    });
    const result = await waitOperation(operation);
    if (!result?.entries?.length)
      throw new Error(
        "未识别到 UEFI 启动项，请检查 efibootmgr 与非交互 sudo 权限。",
      );
    if (records.get(system) === record && fresh(machine, system))
      record.entries = result.entries;
  } catch (error) {
    if (records.get(system) === record && fresh(machine, system))
      record.error = error.message;
  } finally {
    record.running = false;
    if (records.get(system) === record) emit(true);
  }
}
export function bootEntryField(machine, system, path) {
  const field = inputField("启动项", path, {
    placeholder: "例如 0001",
    required: true,
  });
  field.classList.add("boot-entry-field");
  targets.set(field, { machine, system, path });
  const controls = field.querySelector(".input-controls");
  controls.classList.add("boot-entry-controls");
  const record = records.get(system);
  if (fresh(machine, system) && record.entries?.length) {
    const input = controls.querySelector("input");
    const select = node("select");
    select.id = input.id;
    if (input.hasAttribute("aria-invalid"))
      select.setAttribute("aria-invalid", "true");
    const value = String(system.boot_entry ?? "");
    const selected = record.entries.find(
      (entry) => entry.id === value.toUpperCase(),
    );
    if (!selected) {
      const option = new Option(
        value ? `Boot${value}（当前填写值）` : "请选择启动项",
        value,
      );
      option.disabled = true;
      select.add(option);
    }
    for (const entry of record.entries)
      select.add(
        new Option(
          `Boot${entry.id}${entry.label ? " · " + entry.label : ""}${entry.active ? "" : "（未激活）"}`,
          entry.id,
        ),
      );
    select.value = selected?.id || value;
    select.addEventListener("change", () => {
      change(path, select.value);
      field.classList.remove("invalid");
      select.removeAttribute("aria-invalid");
      field.querySelector(".field-error")?.remove();
    });
    input.replaceWith(select);
  }
  controls.append(
    button(
      "自动识别",
      () => identify(machine, system),
      "button secondary boot-entry-identify",
    ),
    statusIndicator("boot-entry-status"),
  );
  updateField(field);
  return field;
}
function updateField(field) {
  const { machine, system } = targets.get(field);
  const record = records.get(system),
    valid = fresh(machine, system);
  const ssh = mergedSSH(machine, system);
  field.querySelector(".boot-entry-identify").disabled =
    (!!record?.running && valid) || !ssh.host || !ssh.username;
  let phase = "idle",
    message = "";
  if (record && !valid) {
    phase = "stale";
    message = "连接信息或配置位置已改变，请重新识别。";
  } else if (record?.running) {
    phase = "running";
    message = "正在识别启动项";
  } else if (record?.error) {
    phase = "error";
    message = record.error;
  } else if (record?.entries?.length) {
    phase = "success";
    message = "启动项识别成功";
  }
  updateStatusIndicator(
    field.querySelector(".boot-entry-status"),
    phase,
    message,
  );
}
export function updateBootEntryFields() {
  document.querySelectorAll(".boot-entry-field").forEach((field) => {
    const { machine, system, path } = targets.get(field);
    if (!fresh(machine, system) && field.querySelector("select"))
      field.replaceWith(bootEntryField(machine, system, path));
    else updateField(field);
  });
}
