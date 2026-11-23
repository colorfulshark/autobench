import { node } from "./ui.js";

export function initSelectMenus() {
  if (CSS.supports("appearance: base-select") && CSS.supports("selector(select::picker(select))"))
    return;

  const menu = node("div", "select-menu");
  menu.id = "select-menu";
  menu.setAttribute("role", "listbox");
  menu.hidden = true;
  document.body.append(menu);

  let select = null;
  let activeIndex = -1;

  function close() {
    if (!select) return;
    for (const attr of ["aria-expanded", "aria-controls", "aria-activedescendant"])
      select.removeAttribute(attr);
    select = null;
    activeIndex = -1;
    menu.hidden = true;
  }

  function highlight(index) {
    if (index < 0) return;
    menu.children[activeIndex]?.classList.remove("active");
    activeIndex = index;
    const option = menu.children[index];
    option.classList.add("active");
    select.setAttribute("aria-activedescendant", option.id);
    option.scrollIntoView({ block: "nearest" });
  }

  function enabledIndices() {
    return [...menu.children].flatMap((item, index) =>
      item.getAttribute("aria-disabled") === "true" ? [] : [index],
    );
  }

  function toggle(target) {
    if (target === select) {
      close();
      return;
    }
    close();
    select = target;
    select.focus({ preventScroll: true });
    menu.replaceChildren(...[...select.options].map((option, index) => {
      const item = node("div", "select-menu-option", option.label);
      item.id = `${menu.id}-${index}`;
      item.dataset.index = index;
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", String(option.selected));
      item.setAttribute("aria-disabled", String(option.matches(":disabled")));
      return item;
    }));

    const rect = select.getBoundingClientRect();
    const below = innerHeight - rect.bottom - 4;
    const above = rect.top - 4;
    menu.style.left = `${rect.left}px`;
    menu.style.width = `${rect.width}px`;
    menu.hidden = false;
    const placeBelow = below >= Math.min(280, menu.scrollHeight) || below >= above;
    menu.style.maxHeight = `${Math.min(280, Math.max(0, placeBelow ? below : above))}px`;
    menu.style.top = placeBelow ? `${rect.bottom + 4}px` : "auto";
    menu.style.bottom = placeBelow ? "auto" : `${innerHeight - rect.top + 4}px`;
    select.setAttribute("aria-controls", menu.id);
    select.setAttribute("aria-expanded", "true");
    const indices = enabledIndices();
    highlight(indices.includes(select.selectedIndex) ? select.selectedIndex : (indices[0] ?? -1));
  }

  function choose(index) {
    if (!select || menu.children[index]?.getAttribute("aria-disabled") !== "false") return;
    const target = select;
    close();
    if (target.selectedIndex === index) return;
    target.selectedIndex = index;
    target.dispatchEvent(new Event("input", { bubbles: true }));
    target.dispatchEvent(new Event("change", { bubbles: true }));
  }

  const targetSelect = (target) => target.closest?.("select:not(:disabled, [multiple], [size])");
  document.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    const target = targetSelect(event.target);
    if (target) {
      event.preventDefault();
      toggle(target);
    } else if (!menu.contains(event.target)) close();
  });
  document.addEventListener("click", (event) => {
    const target = targetSelect(event.target);
    if (!target) return;
    event.preventDefault();
    if (event.detail === 0) toggle(target);
  });
  menu.addEventListener("mousedown", (event) => event.preventDefault());
  menu.addEventListener("click", (event) => {
    const option = event.target.closest("[data-index]");
    if (option) choose(Number(option.dataset.index));
  });
  document.addEventListener("focusout", (event) => {
    if (event.target === select) close();
  });
  document.addEventListener("change", (event) => {
    if (event.target === select) close();
  });

  document.addEventListener("keydown", (event) => {
    const target = targetSelect(event.target);
    if (!target || event.metaKey || event.ctrlKey) return;
    const key = event.key;
    const opening = ["Enter", " ", "ArrowDown", "ArrowUp"].includes(key);
    if (!select) {
      if (opening) {
        event.preventDefault();
        toggle(target);
      }
      return;
    }
    if (key === "Tab") return close();
    if (key === "Escape" || key === "Enter" || key === " ") {
      event.preventDefault();
      if (key === "Escape") close();
      else choose(activeIndex);
    } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(key)) {
      event.preventDefault();
      const indices = enabledIndices();
      const offset = indices.indexOf(activeIndex) + (key === "ArrowDown" ? 1 : -1);
      const index = key === "Home" ? 0 : key === "End" ? indices.length - 1 : offset;
      highlight(indices[index] ?? activeIndex);
    }
  });
  window.addEventListener("resize", close);
  document.addEventListener("scroll", (event) => {
    if (!menu.contains(event.target)) close();
  }, { capture: true, passive: true });
}
