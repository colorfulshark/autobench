import { state, api } from "./state.js";
import { pathParent } from "./model.js";
import { $, node, button, hint } from "./ui.js";
let resolver = null,
  kind = "file",
  selected = null,
  current = null,
  request = 0;
export function chooseFile(initial = "", options = {}) {
  kind = options.kind || "file";
  selected = null;
  current = null;
  $("file-title").textContent =
    options.title ||
    (kind === "directory" ? "选择服务器目录" : "选择服务器文件");
  $("file-help").textContent = "这里浏览的是运行 AutoBench Web 的服务器文件。";
  $("file-hidden").checked = initial.includes("/.");
  $("file-selected").textContent = "";
  $("file-confirm").disabled = kind !== "directory";
  $("file-dialog").showModal();
  const promise = new Promise((resolve) => {
    resolver = resolve;
  });
  navigate(initial || state.info.base_dir)
    .catch(() => navigate(pathParent(initial) || state.info.base_dir))
    .catch((error) => {
      $("file-list").replaceChildren(hint(error.message));
    });
  return promise;
}
async function navigate(path) {
  const version = ++request;
  current = null;
  selected = null;
  $("file-confirm").disabled = true;
  $("file-selected").textContent = "";
  $("file-list").replaceChildren(hint("正在加载目录…"));
  $("file-list").setAttribute("aria-busy", "true");
  let result;
  try {
    result = await api("/api/files?" + new URLSearchParams({ path }));
  } catch (error) {
    if (version !== request) return;
    throw error;
  } finally {
    if (version === request)
      $("file-list").setAttribute("aria-busy", "false");
  }
  if (version !== request) return;
  current = result;
  selected = null;
  $("file-path").value = result.path;
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
      const e = button(
        "",
        () =>
          entry.type === "directory"
            ? navigate(entry.path)
            : select(entry.path),
        "file-entry",
      );
      e.append(
        node("span", "file-icon", entry.type === "directory" ? "▱" : "▤"),
        node("span", "", entry.name),
        node("span", "file-type", entry.type === "directory" ? "目录" : "文件"),
      );
      e.classList.toggle("selected", entry.path === selected);
      if (entry.type === "file") {
        e.disabled = kind === "directory";
        e.addEventListener("dblclick", () => finish(entry.path));
      }
      return e;
    }),
  );
  if (!entries.length)
    $("file-list").append(node("p", "empty", "此目录没有可选文件。"));
}
function select(path) {
  selected = path;
  render();
}
function finish(path) {
  const resolve = resolver;
  resolver = null;
  request++;
  $("file-list").setAttribute("aria-busy", "false");
  $("file-dialog").close();
  if (resolve) resolve(path);
}
export function initFiles() {
  $("file-up").addEventListener("click", () => {
    if (current)
      navigate(current.parent).catch((error) => {
        $("file-list").replaceChildren(hint(error.message));
      });
  });
  $("file-go").addEventListener("click", () =>
    navigate($("file-path").value).catch((error) => {
      $("file-list").replaceChildren(hint(error.message));
    }),
  );
  $("file-path").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      $("file-go").click();
    }
  });
  $("file-hidden").addEventListener("change", render);
  $("file-confirm").addEventListener("click", () =>
    finish(kind === "directory" ? current?.path : selected),
  );
  $("file-cancel").addEventListener("click", () => finish(null));
  $("file-dialog").addEventListener("cancel", () => {
    finish(null);
  });
}
