// Shared app shell: phone menu, hash-based screens, toasts.

import { logOut } from "./api.js";
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

/** The sidebar's Log out button, guarded by the shared confirmation modal. */
export function setupLogout(options = {}) {
  const button = document.getElementById("logout-btn");
  button.addEventListener("click", async () => {
    button.disabled = true;
    await confirmAction({
      title: "Sign out?",
      message: typeof options.message === "function" ? options.message() : options.message || "Are you sure you want to sign out of your HealthPulz account?",
      type: "warning",
      icon: "users",
      confirmLabel: "Yes, Sign Out",
      loadingLabel: "Signing out…",
      successTitle: null,
      action: logOut,
    });
    button.disabled = false;
  });
}

/** Confirms an operation, runs it once, and reports its result. */
export function confirmAction({
  title = "Confirm action",
  message,
  type = "info",
  icon: iconName,
  cancelLabel = "Cancel",
  confirmLabel = "Confirm",
  loadingLabel = "Working…",
  successTitle = "Action completed",
  successMessage = "Your changes were saved.",
  action,
}) {
  return new Promise((resolve) => {
    const dialog = el("dialog", { className: `confirm-modal confirm-modal-${type}` });
    dialog.setAttribute("aria-labelledby", "confirm-modal-title");
    dialog.setAttribute("aria-describedby", "confirm-modal-message");
    dialog.setAttribute("role", "alertdialog");
    const symbol = icon(iconName || ({ info: "pulse", warning: "alert", danger: "alert" }[type] || "pulse"));
    const heading = el("h2", { id: "confirm-modal-title", textContent: title });
    const description = el("p", { id: "confirm-modal-message", className: "confirm-modal-message", textContent: message });
    const error = el("p", { className: "confirm-modal-error", role: "alert", hidden: true });
    const cancel = el("button", { type: "button", className: "btn btn-ghost", textContent: cancelLabel });
    const confirm = el("button", { type: "button", className: `btn ${type === "danger" ? "btn-danger-solid" : "btn-primary"}`, textContent: confirmLabel });
    const finish = (value) => { dialog.close(); dialog.remove(); resolve(value); };
    cancel.addEventListener("click", () => finish(false));
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); if (!confirm.disabled) finish(false); });
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog || confirm.disabled) return;
      const bounds = dialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) finish(false);
    });
    confirm.addEventListener("click", async () => {
      if (confirm.disabled) return;
      confirm.disabled = true;
      cancel.disabled = true;
      confirm.textContent = loadingLabel;
      confirm.dataset.loading = "true";
      error.hidden = true;
      try {
        await action?.();
        finish(true);
        if (successTitle) toast({ title: successTitle, body: successMessage, level: "OK" });
      } catch (cause) {
        error.textContent = cause?.message || "The action could not be completed. Try again.";
        error.hidden = false;
        confirm.disabled = false;
        cancel.disabled = false;
        confirm.textContent = confirmLabel;
        delete confirm.dataset.loading;
        toast({ title: "Action failed", body: error.textContent, level: "EPIDEMIC" });
      }
    });
    dialog.append(el("div", { className: "confirm-modal-icon" }, symbol), el("div", { className: "confirm-modal-copy" }, heading, description, error), el("div", { className: "confirm-modal-actions" }, cancel, confirm));
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove(), { once: true });
    dialog.showModal();
    confirm.focus();
  });
}

/** Hash routing (#/name/param), so reload and Back work. onChange(name, param) runs on each change. */
export function createRouter(defaultScreen, onChange, { beforeNavigate } = {}) {
  const sections = [...document.querySelectorAll("[data-screen]")];
  let committedHash = location.hash;
  const parse = () => {
    const [name, ...rest] = location.hash.replace(/^#\/?/, "").split("/");
    return sections.some((s) => s.dataset.screen === name) ? { name, param: rest.join("/") || null } : { name: defaultScreen, param: null };
  };
  const show = () => {
    const { name, param } = parse();
    let navName = name;
    for (const section of sections) {
      section.hidden = section.dataset.screen !== name;
      if (!section.hidden) navName = section.dataset.parent ?? name;
    }
    document.querySelectorAll("[data-nav]").forEach((link) => {
      if (link.dataset.nav === navName) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    window.scrollTo(0, 0);
    onChange(name, param);
  };
  window.addEventListener("hashchange", async () => {
    const requestedHash = location.hash;
    if (beforeNavigate && !(await beforeNavigate())) {
      history.replaceState(null, "", `${location.pathname}${location.search}${committedHash}`);
      return;
    }
    committedHash = requestedHash;
    show();
  });
  return {
    current: () => parse().name,
    go(name, param = null) {
      const target = param ? `#/${name}/${param}` : `#/${name}`;
      if (location.hash === target) show();
      else location.hash = target;
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
    icon(level === "OK" ? "pulse" : "alert"),
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
