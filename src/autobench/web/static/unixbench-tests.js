import { state, change } from "./state.js";
import { at } from "./model.js";
import { node, button, hint, toggle, friendlyError } from "./ui.js";

const groups = [
  [
    "系统测试项",
    [
      ["dhry2reg", "整数运算（Dhrystone）"],
      ["whetstone-double", "双精度浮点运算（Whetstone）"],
      ["execl", "程序替换吞吐量"],
      ["fstime", "文件复制（1024 字节缓冲区）"],
      ["fstime-r", "文件读取（1024 字节缓冲区）"],
      ["fstime-w", "文件写入（1024 字节缓冲区）"],
      ["fsbuffer", "文件复制（256 字节缓冲区）"],
      ["fsbuffer-r", "文件读取（256 字节缓冲区）"],
      ["fsbuffer-w", "文件写入（256 字节缓冲区）"],
      ["fsdisk", "文件复制（4096 字节缓冲区）"],
      ["fsdisk-r", "文件读取（4096 字节缓冲区）"],
      ["fsdisk-w", "文件写入（4096 字节缓冲区）"],
      ["pipe", "管道吞吐量"],
      ["context1", "管道上下文切换"],
      ["spawn", "进程创建"],
      ["syscall", "系统调用开销"],
      ["shell1", "Shell 脚本（1 并发）"],
      ["shell8", "Shell 脚本（8 并发）"],
      ["shell16", "Shell 脚本（16 并发）"],
    ],
  ],
  [
    "测试组合",
    [
      ["shell", "shell1、shell8、shell16"],
      ["fs", "全部文件读、写、复制测试"],
      ["system", "系统测试组（含 shell16）"],
      ["index", "标准完整测试（默认）"],
    ],
  ],
];

export function unixbenchTestsEditor(path) {
  const box = node("section", "unixbench-tests field");
  box.dataset.fieldPath = JSON.stringify(path);
  box.append(node("h3", "", "测试项"));
  box.append(
    hint("未指定测试名时运行标准 index 测试。shell 包含 shell1、shell8、shell16。"),
    hint("运行参数 args 中已有的测试名仍会参与运行；此处选择会追加到 args 之后。清空选择仅清空 tests。"),
  );
  const raw = at(state.document, path);
  const errors = state.errors.filter((error) =>
    path.every((part, i) => error.path?.[i] === part),
  );
  if (errors.length) {
    box.classList.add("invalid");
    box.append(node("p", "field-error", errors.map(friendlyError).join("；")));
  }
  if (raw !== undefined && !Array.isArray(raw)) {
    box.append(hint("当前 tests 不是列表，请在 YAML 中修正。"));
    return box;
  }
  const selected = raw || [];
  for (const [title, options] of groups) {
    const group = node("fieldset", "unixbench-test-group");
    group.append(node("legend", "", title));
    const grid = node("div", "unixbench-test-grid");
    for (const [name, description] of options)
      grid.append(
        toggle(`${name} · ${description}`, selected.includes(name), (checked) =>
          change(
            path,
            checked ? [...selected, name] : selected.filter((item) => item !== name),
            true,
          ),
        ),
      );
    group.append(grid);
    box.append(group);
  }
  if (selected.length) {
    const list = node("ul", "unixbench-selected");
    list.setAttribute("aria-label", "已选测试项");
    selected.forEach((name, index) => {
      const item = node("li");
      item.dataset.fieldPath = JSON.stringify([...path, index]);
      const remove = button(
        "×",
        () => change(path, selected.filter((_, i) => i !== index), true),
        "icon-button",
      );
      remove.setAttribute("aria-label", `移除测试项 ${name}`);
      item.append(node("span", "", String(name)), remove);
      list.append(item);
    });
    box.append(list);
    box.append(button("清空选择", () => change(path, [], true), "link-button"));
  }
  const custom = node("div", "field");
  const id = "custom-" + path.join("-");
  const label = node("label", "field-label", "其他测试名");
  label.htmlFor = id;
  const controls = node("div", "input-controls");
  const input = node("input");
  input.id = id;
  input.placeholder = "例如 fstime-r、hanoi";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.addEventListener("input", () => input.setCustomValidity(""));
  const add = () => {
    const name = input.value;
    if (!name || name.startsWith("-") || /[\s\u0000]/u.test(name)) {
      input.setCustomValidity("请输入一个测试名，不能包含空白或 NUL，也不能以 - 开头。");
      input.reportValidity();
      return;
    }
    if (!selected.includes(name)) change(path, [...selected, name], true);
    else input.value = "";
  };
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      add();
    }
  });
  controls.append(input, button("添加测试项", add));
  custom.append(label, controls, hint("每次添加一个名称；具体支持的测试名由源码包内的 UnixBench 决定。"));
  box.append(custom);
  return box;
}
