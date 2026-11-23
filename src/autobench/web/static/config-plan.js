import { configSteps, state } from "./state.js";
import { counts } from "./model.js";
import { $, node, button, icon } from "./ui.js";
import { canGoToStep, goToStep } from "./config-view.js";

// The preview and the review screen use the same configuration and task count.
// Nothing in this view is written back to the YAML document.
export function renderPlan(stepLimit) {
  const root = $("config-plan");
  root.replaceChildren();
  if (!state.document || typeof state.document !== "object") return;
  const list = (value) => (Array.isArray(value) ? value : []);
  const machines = list(state.document.machines).filter(
    (value) => value && typeof value === "object",
  );
  const benchmarks = list(state.document.benchmarks).filter(
    (value) => value && typeof value === "object",
  );
  const totals = counts(state.document);
  const header = node("div", "plan-heading");
  const title = node("h2", "icon-heading", "测试计划");
  title.prepend(icon("calendar"));
  const layers = node("span", "plan-layers");
  layers.setAttribute("aria-hidden", "true");
  layers.append(node("span"), node("span"), node("span"));
  header.append(title, layers);
  const figure = node("div", "plan-figure");
  figure.append(
    node("strong", "", totals.jobs.toLocaleString("zh-CN")),
    node("span", "", "个测试任务"),
  );
  const scope = node(
    "p",
    "plan-scope",
    `${totals.machines} 台机器 · ${totals.systems} 个系统 · ${totals.benchmarks} 项测试`,
  );
  header.append(figure, scope);
  root.append(header);
  const editStep = (label, index) => {
    const edit = button(label, () => goToStep(index, true), "text-button");
    edit.disabled = !canGoToStep(index, stepLimit);
    if (edit.disabled) edit.title = `请先完成「${configSteps[stepLimit]}」`;
    return edit;
  };
  const sequence = node("section", "plan-section");
  const sequenceTitle = node("h3", "icon-heading", "系统执行顺序");
  sequenceTitle.prepend(icon("network"));
  sequence.append(sequenceTitle);
  for (const machine of machines.slice(0, 3)) {
    const group = node("div", "plan-machine");
    group.append(
      node("div", "plan-machine-name", machine.id || "未命名机器"),
    );
    const systems = list(machine.systems).filter(
      (value) => value && typeof value === "object",
    );
    if (!systems.length) group.append(node("p", "plan-empty", "尚未添加系统"));
    else {
      const route = node("ol", "plan-route");
      for (const system of systems.slice(0, 4)) {
        const item = node("li");
        item.append(
          node("span", "", system.id || "未命名系统"),
          node("code", system.grub_entry ? "plan-boot-target" : "", system.grub_entry
            ? `${system.boot_entry || "未设启动项"} / GRUB ${system.grub_entry}`
            : system.boot_entry || "未设启动项"),
        );
        route.append(item);
      }
      group.append(route);
      if (systems.length > 4)
        group.append(
          node("p", "secondary", `另有 ${systems.length - 4} 个系统`),
        );
    }
    sequence.append(group);
  }
  if (!machines.length)
    sequence.append(node("p", "plan-empty", "尚未添加机器"));
  if (machines.length > 3)
    sequence.append(
      node("p", "secondary", `另有 ${machines.length - 3} 台机器`),
    );
  sequence.append(editStep("编辑系统 →", 1));
  root.append(sequence);

  const tests = node("section", "plan-section");
  const testsTitle = node("h3", "icon-heading", "测试与轮次");
  testsTitle.prepend(icon("layers"));
  tests.append(testsTitle);
  for (const benchmark of benchmarks.slice(0, 3)) {
    const item = node("div", "plan-test");
    const rounds = node("span", "plan-test-rounds");
    rounds.append(
      node("strong", "", benchmark.repetitions ?? 1),
      node("span", "", "轮正式"),
    );
    item.append(
      node("span", "plan-test-name", benchmark.id || "未命名测试"),
      rounds,
      node(
        "span",
        "plan-test-warmup",
        (benchmark.warmup ?? true) ? "另预热 1 轮 · 不保存结果" : "预热已关闭",
      ),
    );
    tests.append(item);
  }
  if (!benchmarks.length) tests.append(node("p", "plan-empty", "尚未设置测试"));
  if (benchmarks.length > 3)
    tests.append(
      node("p", "secondary", `另有 ${benchmarks.length - 3} 项测试`),
    );
  tests.append(editStep("编辑测试 →", 2));
  root.append(tests);
}
