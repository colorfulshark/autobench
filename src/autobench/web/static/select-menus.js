import { node } from "./ui.js";

export function initSelectMenus() {
  if (CSS.supports("appearance: base-select") && CSS.supports("selector(select::picker(select))"))
    return;

  const menu = node("div", "select-menu");
  menu.id = "select-menu";
  menu.setAttribute("role", "listbox");
  menu.hidden = true;
  menu.inert = true;
  document.body.append(menu);

  let select = null;
  let activeIndex = -1;
  let typed = "";
  let typedAt = 0;

  function close() {
    if (!select) return;
    for (const attr of ["aria-expanded", "aria-controls", "aria-activedescendant"])
      select.removeAttribute(attr);
    select = null;
    activeIndex = -1;
    typed = "";
    menu.inert = true;
    menu.hidden = true;
  }

  function highlight(index) {
    if (index < 0) return;
    menu.children[activeIndex]?.classList.remove("active");
    activeIndex = index;
    const option = menu.children[index];
    option.classList.add("active");
    select.setAttribute("aria-activedescendant", option.id);
    const optionRect = option.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();
    if (optionRect.top < menuRect.top) menu.scrollTop += optionRect.top - menuRect.top;
    else if (optionRect.bottom > menuRect.bottom) menu.scrollTop += optionRect.bottom - menuRect.bottom;
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
    const viewport = document.documentElement.clientWidth;
    const width = Math.min(rect.width, viewport - 16);
    menu.style.left = `${Math.max(8, Math.min(rect.left, viewport - width - 8))}px`;
    menu.style.width = `${width}px`;
    menu.style.removeProperty("max-height");
    menu.hidden = false;
    const styles = getComputedStyle(menu);
    const gap = Number.parseFloat(styles.getPropertyValue("--select-menu-gap"));
    const below = innerHeight - rect.bottom - gap;
    const above = rect.top - gap;
    const placeBelow = menu.offsetHeight <= below || below >= above;
    const available = Math.max(0, placeBelow ? below : above);
    menu.dataset.placement = placeBelow ? "below" : "above";
    menu.style.maxHeight = `${Math.min(Number.parseFloat(styles.maxHeight), available)}px`;
    menu.style.top = placeBelow ? `${rect.bottom + gap}px` : "auto";
    menu.style.bottom = placeBelow ? "auto" : `${innerHeight - rect.top + gap}px`;
    const label = select.getAttribute("aria-label") || select.labels?.[0]?.textContent;
    if (label) menu.setAttribute("aria-label", label);
    else menu.removeAttribute("aria-label");
    select.setAttribute("aria-controls", menu.id);
    select.setAttribute("aria-expanded", "true");
    menu.inert = false;
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
    if (!target || event.metaKey || event.ctrlKey || event.isComposing) return;
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
    } else if (key.length === 1 && !event.altKey) {
      event.preventDefault();
      const now = performance.now();
      typed = (now - typedAt < 600 ? typed : "") + key.toLocaleLowerCase();
      typedAt = now;
      const indices = enabledIndices();
      const start = Math.max(0, indices.indexOf(activeIndex));
      const label = (index) => menu.children[index].textContent.trim().toLocaleLowerCase();
      const repeated = [...typed].every((char) => char === typed[0]);
      const offset = repeated ? start + 1 : start;
      const match = [...indices.slice(offset), ...indices.slice(0, offset)].find(
        (index) => label(index).startsWith(repeated ? typed[0] : typed),
      );
      if (match !== undefined) highlight(match);
    }
  });
  window.addEventListener("resize", close);
  document.addEventListener("scroll", (event) => {
    if (!menu.contains(event.target)) close();
  }, { capture: true, passive: true });
}
