// App shell behavior shared by the dashboard, node and monitor pages:
// the slide-out menu on phones, hash-based screens, and toast notifications.

import { el } from "./dom.js";

/** Opens/closes the sidebar on narrow screens. */
export function setupMenu() {
  const app = document.querySelector(".app");
  const toggle = (open) => {
    app.classList.toggle("nav-open", open);
    document.querySelector(".menu-btn")?.setAttribute("aria-expanded", open);
  };
  document.querySelector(".menu-btn")?.addEventListener("click", () => toggle(!app.classList.contains("nav-open")));
  document.querySelector(".scrim")?.addEventListener("click", () => toggle(false));
  document.querySelectorAll(".sidebar a").forEach((a) => a.addEventListener("click", () => toggle(false)));
  document.addEventListener("keydown", (e) => e.key === "Escape" && toggle(false));
}

/**
 * Screens are <section data-screen="name">; links use href="#/name" and data-nav="name".
 * The URL hash keeps the current screen, so reload and the Back button work. The "/"
 * keeps the hash from matching an element id (the browser would scroll to it).
 * onChange(name) runs whenever the screen changes.
 */
export function createRouter(defaultScreen, onChange) {
  const screens = [...document.querySelectorAll("[data-screen]")].map((s) => s.dataset.screen);
  const current = () => {
    const name = location.hash.replace(/^#\/?/, "");
    return screens.includes(name) ? name : defaultScreen;
  };
  const show = () => {
    const name = current();
    document.querySelectorAll("[data-screen]").forEach((s) => (s.hidden = s.dataset.screen !== name));
    document.querySelectorAll("[data-nav]").forEach((link) => {
      if (link.dataset.nav === name) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    window.scrollTo(0, 0);
    onChange(name);
  };
  window.addEventListener("hashchange", show);
  return {
    current,
    go(name) {
      if (current() === name) show();
      else location.hash = `/${name}`;
    },
    start: show,
  };
}

/** Shows a small notification for a few seconds. Returns the element. */
export function toast({ title, body, level = "ALERT", actionLabel, onAction, durationMs = 8000 }) {
  let container = document.querySelector(".toasts");
  if (!container) {
    container = el("div", { className: "toasts", role: "status" });
    container.setAttribute("aria-live", "assertive");
    document.body.append(container);
  }
  const close = el("button", { type: "button", className: "link-btn small", textContent: "Dismiss" });
  const item = el(
    "div",
    { className: "toast", dataset: { level } },
    icon("alert"),
    el("div", { className: "grow" }, el("strong", { textContent: title }), el("span", { className: "muted", textContent: body })),
  );
  if (actionLabel) {
    const action = el("button", { type: "button", className: "btn btn-primary btn-small", textContent: actionLabel });
    action.addEventListener("click", () => {
      item.remove();
      onAction?.();
    });
    item.append(action);
  }
  item.append(close);
  close.addEventListener("click", () => item.remove());
  container.prepend(item);
  setTimeout(() => item.remove(), durationMs);
  return item;
}

/** <svg class="icon"><use href="/img/icons.svg#name"></svg> */
export function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `/img/icons.svg#${name}`);
  svg.append(use);
  return svg;
}
