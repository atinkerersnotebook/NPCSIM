import { escape } from "./html.ts";

export function sectionTabPanel(id: string, label: string, className = ""): string {
  return `id="${id}" class="dashboard-panel${className ? ` ${className}` : ""}" role="tabpanel" aria-labelledby="tab-${id}" data-section-tab="${escape(label)}" tabindex="0"`;
}

export function sectionTabs(nav: HTMLElement, content: HTMLElement, onSelect?: (phase: "before" | "after") => void) {
  let active: string | undefined;
  let tabIds = "";
  const panels = () => [...content.querySelectorAll<HTMLElement>("[data-section-tab]")];
  const tab = (id: string) => nav.querySelector<HTMLButtonElement>(`#tab-${id}`);
  const sync = (navigating = false) => {
    const sections = panels();
    const requested = location.hash.slice(1);
    const next = sections.find(panel => panel.id === requested)?.id ?? sections[0]?.id;
    const changed = next !== active;
    const switched = navigating && changed;
    if (switched) onSelect?.("before");
    active = next;
    const focused = nav.contains(document.activeElement);
    const ids = sections.map(panel => panel.id).join(" ");
    if (ids !== tabIds) {
      nav.innerHTML = sections.map(panel => `<button type="button" role="tab" id="tab-${panel.id}" aria-controls="${panel.id}"></button>`).join("");
      tabIds = ids;
    }
    nav.hidden = !sections.length;
    for (const panel of sections) {
      const selected = panel.id === active;
      const button = tab(panel.id)!;
      if (button.textContent !== panel.dataset.sectionTab) button.textContent = panel.dataset.sectionTab!;
      panel.hidden = !selected;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    }
    if (focused && active && document.activeElement !== tab(active)) tab(active)?.focus({ preventScroll: true });
    if (changed && (navigating || requested === active)) window.scrollTo({ top: 0 });
    if (switched) onSelect?.("after");
  };
  const select = (id: string, focusPanel = false) => {
    const panel = panels().find(section => section.id === id);
    if (!panel) return;
    if (location.hash !== `#${id}`) history.pushState(null, "", `#${id}`);
    if (id !== active) sync(true);
    if (focusPanel) panel.focus({ preventScroll: true });
  };
  nav.addEventListener("click", event => {
    const button = (event.target as Element).closest<HTMLButtonElement>("[role=tab]");
    if (button) select(button.getAttribute("aria-controls")!);
  });
  nav.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const sections = panels();
    const current = sections.findIndex(panel => panel.id === active);
    if (current < 0) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? sections.length - 1 :
      (current + (event.key === "ArrowRight" ? 1 : -1) + sections.length) % sections.length;
    select(sections[next].id);
    tab(sections[next].id)?.focus();
  });
  content.addEventListener("click", event => {
    if (!(event instanceof MouseEvent) || event.button || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const link = (event.target as Element).closest<HTMLAnchorElement>('a[href^="#"]');
    const id = link?.hash.slice(1);
    if (!id || !panels().some(panel => panel.id === id)) return;
    event.preventDefault();
    select(id, true);
  });
  window.addEventListener("hashchange", () => sync(true));
  return { sync, select };
}
