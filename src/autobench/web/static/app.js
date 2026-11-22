import {
  state,
  reviewStep,
  api,
  subscribe,
  show,
  createProject,
  saveStartupProject,
  load,
  save,
  validate,
  switchMode,
  sourceChanged,
  discard,
  targetChanged,
  currentText,
  accept,
} from "./state.js";
import { pathParent } from "./model.js";
import { $, node, button, perform, friendlyError, jsonDetails } from "./ui.js";
import { initFiles, chooseFile } from "./files.js";
import {
  renderConfig,
  updateConfig,
  stepGate,
  nextStep,
  goToStep,
  setStepPicker,
  focusError,
  errorLabel,
} from "./config-view.js";
import {
  closePreview,
  initRuns,
  renderRuns,
  refreshRuns,
  startRun,
} from "./runs.js";
import { renderPlan } from "./config-plan.js";
let ready = false,
  pathAction = "save";
const titles = { config: "测试配置", runs: "运行记录" };
function setPage(page) {
  if (!Object.hasOwn(titles, page)) page = "config";
  const changed = state.page !== page;
  state.page = page;
  sessionStorage.setItem("autobench-page", page);
  for (const key of Object.keys(titles)) {
    $("page-" + key).hidden = key !== page;
  }
  $("header-config").hidden = page !== "config";
  if (changed) {
    $("file-menu").open = false;
    setStepPicker(false);
    closePreview();
  }
  document.querySelectorAll("[data-page]").forEach((e) => {
    const selected = e.dataset.page === page;
    e.classList.toggle("active", selected);
    e.setAttribute("aria-selected", String(selected));
    e.tabIndex = selected ? 0 : -1;
  });
  if (changed) window.scrollTo(0, 0);
  if (!ready) return;
  if (page === "runs") {
    renderRuns();
    refreshRuns().catch((error) => show(error.message, "error"));
  }
}
function chrome() {
  const saved = isSaved();
  const name = state.target?.split("/").at(-1) || "未命名配置";
  $("file-name").textContent = name;
  $("active-config").textContent = name;
  $("file-location").textContent = state.target || "尚未选择保存位置";
  $("file-name").title = $("file-location").textContent;
  $("file-location").title = $("file-location").textContent;
  $("save-state").textContent = saved ? "已保存" : "未保存";
  $("save-state").title = $("save-state").textContent;
  $("save-state").dataset.tone = saved ? "success" : "warning";
  $("file-menu").dataset.saved = String(saved);
  $("active-config").title = $("file-location").textContent;
  $("mode-toggle").setAttribute("aria-checked", String(state.mode === "yaml"));
  $("config-layout").hidden = state.mode !== "form";
  const reviewing = state.mode === "yaml" || state.step === reviewStep;
  const { index: stepLimit } = stepGate();
  $("config-plan").hidden = reviewing;
  $("config-layout").classList.toggle("review-layout", reviewing);
  if (!reviewing) renderPlan(stepLimit);
  $("prev-step").hidden = state.step === 0 || state.mode === "yaml";
  const checked = isChecked();
  $("primary-action").textContent =
    state.step < reviewStep && state.mode === "form"
      ? "下一步 →"
      : checked
        ? "开始测试 →"
        : saved
          ? "检查配置"
          : "保存并检查";
  $("yaml-meta").textContent =
    `${$("yaml-editor").value.split("\n").length} 行 · UTF-8`;
  $("config-errors").hidden = !state.errors.length;
  $("config-errors").replaceChildren();
  if (state.errors.length) {
    $("config-errors").append(
      node("strong", "", `有 ${state.errors.length} 项需要处理`),
    );
    for (const error of state.errors)
      $("config-errors").append(
        button(
          `${errorLabel(error)}：${friendlyError(error)}`,
          () => focusError(error),
          "error-link",
        ),
      );
  }
  if (state.errors.length)
    $("config-errors").append(jsonDetails("原始错误详情", state.errors));
  $("validation-result").hidden = !checked || !reviewing;
  if (checked)
    $("validation-result").textContent =
      "配置、源码包及本地 SSH 文件检查通过，可以开始测试。";
  updateConfig(stepLimit);
}
function render(layout) {
  if (!ready) return;
  const active = document.activeElement;
  const focus = active?.id;
  let selection;
  try {
    selection = [active.selectionStart, active.selectionEnd];
  } catch {}
  if (layout) renderConfig();
  chrome();
  if (layout && focus) {
    const next = $(focus);
    if (next && next !== document.activeElement) {
      next.focus({ preventScroll: true });
      if (selection && selection[0] !== null)
        try {
          next.setSelectionRange(...selection);
        } catch {}
    }
  }
}
function openPath(action = "save") {
  pathAction = action;
  const source = state.path || state.target;
  $("save-path").value =
    action === "save-as"
      ? (source ? pathParent(source) : state.info.base_dir) + "/"
      : state.target || state.info.base_dir + "/autobench.yaml";
  $("path-title").textContent =
    action === "save-as"
      ? "另存为 YAML"
      : action === "location"
        ? "更改配置保存位置"
        : "保存配置";
  $("path-confirm").textContent =
    action === "location"
      ? "使用此位置"
      : action === "check"
        ? "保存并检查"
        : "保存配置";
  $("path-error").hidden = true;
  $("path-dialog").showModal();
}
function isSaved() {
  return Boolean(state.path && !state.dirty && state.target === state.path);
}
function isChecked() {
  return isSaved() && state.validation?.version === state.version;
}
async function saveDraft() {
  if (isSaved()) {
    show("草稿已保存，可稍后继续完善。");
    return;
  }
  if (!state.target) {
    openPath();
    return;
  }
  if (await save()) show("草稿已保存，可稍后继续完善。");
}
async function saveAndCheck() {
  if (!state.target) {
    openPath("check");
    return;
  }
  try {
    if (isSaved() || (await save())) await validate();
  } finally {
    $("config-errors").querySelector("button")?.focus();
  }
}
async function primary() {
  if (state.step < reviewStep && state.mode === "form") {
    nextStep();
    return;
  }
  if (isChecked()) {
    const run = await startRun();
    setPage("runs");
    $("back-to-runs").focus({ preventScroll: true });
    show(`测试已启动：${run.run_id}`);
  } else await saveAndCheck();
}
function bind(id, fn) {
  $(id).addEventListener("click", () => perform($(id), fn));
}
function showStartupError(message) {
  const error = $("startup-error");
  error.textContent = message;
  error.hidden = !message;
}
function selectStartupProject(initialPath) {
  const directory = $("startup-directory"),
    filename = $("startup-filename"),
    form = $("startup-form"),
    fields = $("startup-fields"),
    submit = $("startup-save");
  directory.value = pathParent(initialPath);
  filename.value = initialPath.split("/").at(-1);
  fields.disabled = false;
  directory.focus();
  fields.addEventListener("input", () => showStartupError(""));
  bind("startup-browse", async () => {
    const folder = await chooseFile(directory.value, {
      kind: "directory",
      title: "选择配置保存目录",
    });
    if (!folder) {
      directory.focus();
      return;
    }
    directory.value = folder;
    showStartupError("");
    filename.focus();
  });
  return new Promise((resolve) => {
    const saveSelection = async (path, existing = false) => {
      showStartupError("");
      fields.disabled = true;
      form.setAttribute("aria-busy", "true");
      submit.textContent = "正在保存…";
      try {
        resolve(await saveStartupProject(path, { existing }));
      } catch (error) {
        fields.disabled = false;
        form.removeAttribute("aria-busy");
        submit.textContent = "保存并进入";
        showStartupError(error.message);
        filename.focus();
      }
    };
    $("startup-open").addEventListener("click", async () => {
      const path = await chooseFile(directory.value, {
        title: "打开已有 YAML",
      });
      if (!path) return;
      directory.value = pathParent(path);
      filename.value = path.split("/").at(-1);
      await saveSelection(path, true);
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (fields.disabled) return;
      const folder = directory.value,
        name = filename.value;
      if (!folder.trim()) {
        showStartupError("请选择或填写配置保存目录。");
        directory.focus();
        return;
      }
      if (!name.trim() || name.includes("/") || name === "." || name === "..") {
        showStartupError("请填写配置文件名，路径填写在保存目录中。");
        filename.focus();
        return;
      }
      await saveSelection(`${folder.replace(/\/+$/, "")}/${name}`);
    });
  });
}
function initWorkspace() {
  const tabs = [...document.querySelectorAll("[data-page]")];
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => setPage(tab.dataset.page));
    tab.addEventListener("keydown", (event) => {
      let next;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      else if (event.key === "ArrowLeft")
        next = (index + tabs.length - 1) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      else return;
      event.preventDefault();
      setPage(tabs[next].dataset.page);
      tabs[next].focus({ preventScroll: true });
    });
  });
  bind("new-config", async () => {
    if (!discard()) return;
    await createProject();
    setPage("config");
    $("file-menu").open = false;
  });
  bind("open-config", async () => {
    if (!discard()) return;
    const path = await chooseFile(state.path || state.info.base_dir, {
      title: "打开服务器上的 YAML 配置",
    });
    if (path) {
      await load(path);
      setPage("config");
    }
    $("file-menu").open = false;
  });
  document.querySelector(".menu-items").addEventListener("click", (event) => {
    if (event.target.closest("button")) $("file-menu").open = false;
  });
  document.addEventListener("click", (event) => {
    if (!$("file-menu").contains(event.target)) $("file-menu").open = false;
  });
  const picker = $("step-picker");
  picker.addEventListener("click", () => {
    setStepPicker(picker.getAttribute("aria-expanded") !== "true");
  });
  const dismissStepPicker = (event) => {
    if (
      !picker.contains(event.target) &&
      !$("wizard-steps").contains(event.target)
    )
      setStepPicker(false);
  };
  document.addEventListener("click", dismissStepPicker);
  document.addEventListener("focusin", dismissStepPicker);
  document.addEventListener("keydown", (event) => {
    if (
      ready &&
      state.page === "config" &&
      (event.ctrlKey || event.metaKey) &&
      !event.altKey &&
      !event.shiftKey &&
      event.key.toLowerCase() === "s" &&
      !document.querySelector("dialog[open]")
    ) {
      event.preventDefault();
      if (!event.repeat && !$("primary-action").disabled)
        $("secondary-action").click();
      return;
    }
    if (event.key !== "Escape") return;
    if (picker.getAttribute("aria-expanded") === "true") {
      event.preventDefault();
      setStepPicker(false, true);
    } else if ($("file-menu").open) {
      event.preventDefault();
      $("file-menu").open = false;
      $("file-menu").querySelector("summary").focus();
    }
  });
  $("import-config").addEventListener("click", () => $("import").click());
  bind("save-as", () => openPath("save-as"));
  bind("change-config-path", () => openPath("location"));
  bind("export", async () => {
    const text = await currentText(),
      url = URL.createObjectURL(
        new Blob([text], { type: "text/yaml;charset=utf-8" }),
      );
    const link = node("a");
    link.href = url;
    link.download = state.target?.split("/").at(-1) || "autobench.yaml";
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  $("import").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file || !discard()) {
      event.target.value = "";
      return;
    }
    const version = state.version;
    try {
      const text = await file.text();
      let result;
      try {
        result = await api("/api/config/parse", { text });
      } catch (error) {
        result = { document: null, parse_error: error.message };
      }
      if (version !== state.version)
        throw new Error("导入期间有新修改，请重试。");
      accept({ text, ...result }, { draft: true });
      setPage("config");
      show("已导入 YAML 内容，请确认服务器上的保存位置。");
    } catch (error) {
      show(error.message, "error");
    } finally {
      event.target.value = "";
      $("file-menu").open = false;
    }
  });
  bind("mode-toggle", () =>
    switchMode(state.mode === "yaml" ? "form" : "yaml"),
  );
  $("yaml-editor").addEventListener("input", () =>
    sourceChanged($("yaml-editor").value),
  );
  $("yaml-editor").addEventListener("keydown", (event) => {
    if (event.key === "]" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      const e = event.target;
      e.setRangeText("  ", e.selectionStart, e.selectionEnd, "end");
      e.dispatchEvent(new Event("input"));
    }
  });
  bind("prev-step", () => goToStep(state.step - 1, true));
  bind("primary-action", primary);
  bind("secondary-action", saveDraft);
  bind("path-browse", async () => {
    const folder = await chooseFile(
      pathParent($("save-path").value) || state.info.base_dir,
      { kind: "directory", title: "选择 YAML 保存目录" },
    );
    if (folder) {
      $("save-path").value =
        folder +
        "/" +
        ($("save-path").value.split("/").at(-1) || "autobench.yaml");
      $("path-error").hidden = true;
    }
  });
  $("save-path").addEventListener("input", () => {
    $("path-error").hidden = true;
  });
  $("path-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (event.submitter?.value === "cancel") {
      $("path-dialog").close();
      return;
    }
    await perform($("path-confirm"), async () => {
      const path = $("save-path").value.trim();
      if (!path) throw new Error("请输入服务器上的文件路径。");
      if (pathAction === "location") {
        targetChanged(path);
        $("path-dialog").close();
        return;
      }
      let saved;
      try {
        saved = await save(path);
      } catch (error) {
        $("path-error").textContent = error.message;
        $("path-error").hidden = false;
        return;
      }
      if (saved) {
        $("path-dialog").close();
        if (pathAction === "check") await saveAndCheck();
      }
    });
  });
  window.addEventListener("beforeunload", (event) => {
    if (state.dirty && state.touched) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
}
async function restoreProject(path) {
  if (!path) {
    await createProject();
    return;
  }
  try {
    await load(path);
  } catch (error) {
    await createProject();
    show(error.message, "error");
  }
}
async function boot() {
  const restoring =
    performance.getEntriesByType("navigation")[0]?.type === "reload" &&
    history.state?.autobenchConfigConfirmed === true;
  const dialog = $("startup-dialog");
  if (!restoring) {
    dialog.addEventListener("cancel", (event) => event.preventDefault());
    dialog.showModal();
  }
  state.info = await api("/api/info");
  $("version").textContent = `v${state.info.version}`;
  $("server-address").textContent = window.location.host;
  initFiles();
  const rememberedPath = sessionStorage.getItem("autobench-config-path");
  if (restoring) {
    await restoreProject(rememberedPath || state.info.config);
  } else {
    const saved = await selectStartupProject(
      state.info.config ||
        rememberedPath ||
        state.info.base_dir + "/autobench.yaml",
    );
    accept(saved);
    history.replaceState(
      { ...history.state, autobenchConfigConfirmed: true },
      "",
    );
  }
  initWorkspace();
  await initRuns();
  subscribe(render);
  ready = true;
  render(true);
  $("app-shell").inert = false;
  setPage(restoring ? sessionStorage.getItem("autobench-page") : "config");
  if (!restoring) {
    dialog.close();
    $("tab-config").focus({ preventScroll: true });
  }
}
boot().catch((error) => {
  const message = "初始化失败：" + error.message;
  if ($("startup-dialog").open) showStartupError(message);
  else show(message, "error");
});
