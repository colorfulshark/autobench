import { state, api, waitOperation, change, emit } from "./state.js";
import { mergedSSH, connectionTestSignature } from "./model.js";
import {
  node,
  icon,
  button,
  inputField,
  statusIndicator,
  updateStatusIndicator,
} from "./ui.js";

const records = new WeakMap();
const targets = new WeakMap();
function detectedGrub(machine, system) {
  return fresh(machine, system) ? records.get(system).grub ?? null : null;
}
function applyDetectedGrub(machine, system) {
  const value = detectedGrub(machine, system)?.default_entry;
  const record = records.get(system);
  if (
    !value || state.mode !== "form" || system.grub_entry === value ||
    (system.grub_entry !== undefined && system.grub_entry !== record?.automatic)
  )
    return;
  const mi = state.document.machines?.indexOf(machine) ?? -1;
  const si = machine.systems.indexOf(system);
  if (mi < 0 || si < 0) return;
  record.automatic = value;
  change(["machines", mi, "systems", si, "grub_entry"], value);
}
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
    grub: fresh(machine, system) ? previous?.grub : null,
    automatic: previous?.automatic,
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
    if (records.get(system) === record && fresh(machine, system)) {
      record.entries = result.entries;
      record.grub = result.grub ?? null;
      record.manual = false;
      applyDetectedGrub(machine, system);
    }
  } catch (error) {
    if (records.get(system) === record && fresh(machine, system))
      record.error = error.message;
  } finally {
    record.running = false;
    if (records.get(system) === record) emit(true);
  }
}
export function bootEntryField(machine, system, path) {
  const field = inputField("UEFI 启动项", path, {
    placeholder: "例如 0001",
    required: true,
    leadingIcon: "play",
  });
  field.classList.add("boot-entry-field");
  targets.set(field, { machine, system, path });
  const controls = field.querySelector(".input-controls");
  controls.classList.add("boot-entry-controls");
  const record = records.get(system);
  if (fresh(machine, system) && record.entries?.length) {
    const input = controls.querySelector("input");
    const select = node("select");
    if (
      CSS.supports("appearance: base-select") &&
      CSS.supports("selector(select::picker(select))")
    ) {
      const trigger = node("button");
      trigger.append(node("selectedcontent"));
      select.append(trigger);
    }
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
    select.addEventListener("change", () => change(path, select.value, true));
    input.replaceWith(select);
  }
  const identifyButton = button(
    "自动识别",
    () => identify(machine, system),
    "button secondary boot-entry-identify system-action",
  );
  identifyButton.prepend(icon("scan"));
  controls.append(identifyButton, statusIndicator("boot-entry-status"));
  updateField(field);
  return field;
}
export function grubEntryField(machine, system, path) {
  const record = records.get(system);
  const menu = detectedGrub(machine, system);
  const value = system.grub_entry ?? "";
  const options = menu?.entries ?? [];
  let help = "可选。留空时使用 UEFI 入口原有的启动行为。";
  if (menu?.error) help = menu.error;
  else if (menu && !menu.default_entry) help = "未能确认当前系统的默认项，请选择或手动填写。";
  else if (menu?.boot_entry && menu.boot_entry !== String(system.boot_entry ?? "").toUpperCase())
    help = `此菜单识别自 Boot${menu.boot_entry}，运行时会校验所选 UEFI 入口。`;
  const field = inputField("GRUB 启动项", path, {
    placeholder: "不指定",
    nullable: true,
    emptyValue: null,
    help,
  });
  field.classList.add("grub-entry-field");
  targets.set(field, { machine, system, path, menu });
  const controls = field.querySelector(".input-controls");
  const input = controls.querySelector("input");
  controls.querySelector(".link-button").addEventListener("click", () => {
    if (record) record.automatic = undefined;
  });
  input.addEventListener("input", () => {
    if (record) record.automatic = undefined;
  });
  if (!options.length) return field;
  if (!record.manual) {
    const select = node("select");
    select.id = input.id;
    for (const attr of ["aria-invalid", "aria-describedby"])
      if (input.hasAttribute(attr)) select.setAttribute(attr, input.getAttribute(attr));
    select.add(new Option("不指定", ""));
    if (value && !options.some((entry) => entry.id === value))
      select.add(new Option(`${value}（手动填写）`, value));
    for (const entry of options) {
      const option = new Option(entry.label, entry.id);
      option.disabled = !entry.selectable;
      select.add(option);
    }
    select.value = value;
    select.addEventListener("change", () => {
      record.automatic = undefined;
      change(path, select.value || null, true);
    });
    input.replaceWith(select);
    controls.querySelector(".link-button").remove();
  }
  controls.append(
    button(record.manual ? "选择已识别项" : "手动填写", () => {
      record.manual = !record.manual;
      emit(true);
    }, "link-button"),
  );
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
  document.querySelectorAll(".grub-entry-field").forEach((field) => {
    const { machine, system, path, menu } = targets.get(field);
    if (menu !== detectedGrub(machine, system))
      field.replaceWith(grubEntryField(machine, system, path));
  });
}
