import { state, api } from "./state.js";
import { pathParent } from "./model.js";
import { $, node, button, hint, icon } from "./ui.js";
let resolver = null,
  opener = null,
  kind = "file",
  selected = null,
  current = null,
  request = 0;
export function chooseFile(initial = "", options = {}) {
  const dialog = $("file-dialog");
  opener = document.activeElement;
  kind = options.kind || "file";
  selected = null;
  current = null;
  $("file-title").textContent =
    options.title ||
    (kind === "directory" ? "选择服务器目录" : "选择服务器文件");
  $("file-hidden").checked = initial.includes("/.");
  $("file-path").value = initial || state.info.base_dir;
  $("file-confirm").textContent = kind === "directory" ? "选择目录" : "选择文件";
  dialog.showModal();
  const promise = new Promise((resolve) => {
    resolver = resolve;
  });
  const path = $("file-path").value;
  navigate(path, pathParent(initial) || state.info.base_dir, true);
  return promise;
}
async function navigate(path, fallback, opening = false) {
  const version = ++request;
  const input = $("file-path");
  const originalInput = input.value;
  const list = $("file-list");
  if (list.contains(document.activeElement)) input.focus({ preventScroll: true });
  current = null;
  selected = null;
  $("file-confirm").disabled = true;
  $("file-selected").textContent = "";
  list.replaceChildren(hint("正在加载目录…"));
  list.setAttribute("aria-busy", "true");
  let result;
  try {
    result = await api("/api/files?" + new URLSearchParams({ path }));
  } catch (error) {
    if (version !== request) return;
    if (fallback && fallback !== path && error.status === 404)
      return navigate(fallback, null, opening);
    const message = node("p", "callout error", `无法打开目录。 ${error.message}`);
    message.setAttribute("role", "alert");
    list.replaceChildren(message);
    return;
  } finally {
    if (version === request)
      list.setAttribute("aria-busy", "false");
  }
  if (version !== request) return;
  current = result;
  selected = null;
  // A response can arrive between selecting input text and typing its replacement.
  if ((!opening || document.activeElement !== input) && input.value === originalInput)
    input.value = result.path;
  render();
}
function render() {
  if (!current) return;
  $("file-confirm").disabled = kind !== "directory" && !selected;
  $("file-selected").textContent =
    selected || (kind === "directory" ? current.path : "请选择文件");
  const entries = current.entries.filter(
    (entry) => $("file-hidden").checked || !entry.name.startsWith("."),
  );
  $("file-list").replaceChildren(
    ...entries.map((entry) => {
      const isDirectory = entry.type === "directory";
      const e = button(
        "",
        () => isDirectory ? navigate(entry.path) : select(entry.path),
        "file-entry",
      );
      e.append(
        icon(isDirectory ? "folder" : "file"),
        node("span", "file-entry-name", entry.name),
        node("span", "file-type", isDirectory ? "目录" : "文件"),
      );
      if (!isDirectory) {
        e.disabled = kind === "directory";
        if (!e.disabled) {
          e.dataset.path = entry.path;
          e.setAttribute("aria-pressed", String(entry.path === selected));
          e.addEventListener("dblclick", () => finish(entry.path));
        }
      }
      return e;
    }),
  );
  if (!entries.length)
    $("file-list").append(node("p", "empty",
      kind === "directory" ? "此目录没有子目录或文件。" : "此目录没有可选文件。"));
}
function select(path) {
  selected = path;
  $("file-confirm").disabled = false;
  $("file-selected").textContent = path;
  for (const entry of $("file-list").querySelectorAll("[data-path]"))
    entry.setAttribute("aria-pressed", String(entry.dataset.path === path));
}
function finish(path) {
  const resolve = resolver;
  resolver = null;
  request++;
  $("file-list").setAttribute("aria-busy", "false");
  $("file-dialog").close();
  const menu = opener?.closest("#file-menu");
  const target = menu && !menu.open ? menu.querySelector("summary") : opener;
  target?.focus({ preventScroll: true });
  opener = null;
  if (resolve) resolve(path);
}
export function initFiles() {
  $("file-dialog-symbol").append(icon("folder"));
  $("file-up").append(icon("arrowUp"));
  $("file-up").addEventListener("click", () => {
    if (current) navigate(current.parent);
  });
  $("file-go").addEventListener("click", () =>
    navigate($("file-path").value),
  );
  $("file-path").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.isComposing) {
      event.preventDefault();
      $("file-go").click();
    }
  });
  $("file-hidden").addEventListener("change", render);
  $("file-confirm").addEventListener("click", () =>
    finish(kind === "directory" ? current?.path : selected),
  );
  $("file-cancel").addEventListener("click", () => finish(null));
  $("file-dialog").addEventListener("cancel", (event) => {
    event.preventDefault();
    finish(null);
  });
}
