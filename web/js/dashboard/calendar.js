// Map calendar: dots on days with cases; picking a day shows it on both maps.

import { el } from "../common/dom.js";

const WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]; // weeks start on Monday, as in the analytics

/** host: the map's corner. getDay() → "YYYY-MM-DD" or null. onPick(day or null). loadMonth("YYYY-MM") → { days }. */
export function createDayPicker(host, { getDay, onPick, loadMonth }) {
  const button = el("button", { type: "button", className: "cal-btn" });
  button.setAttribute("aria-haspopup", "dialog");
  button.setAttribute("aria-expanded", "false");
  const pop = el("div", { className: "cal-pop", hidden: true });
  pop.setAttribute("role", "dialog");
  pop.setAttribute("aria-label", "Choose a day to show on the map");
  host.append(button, pop);

  let month = null;
  let drawing = 0;

  button.addEventListener("click", (event) => {
    event.stopPropagation();
    if (pop.hidden) open();
    else close();
  });
  document.addEventListener("click", (event) => !pop.hidden && !host.contains(event.target) && close());
  document.addEventListener("keydown", (event) => event.key === "Escape" && !pop.hidden && close());

  function open() {
    const shown = getDay() ? fromIso(getDay()) : new Date();
    month = new Date(shown.getFullYear(), shown.getMonth(), 1);
    pop.hidden = false;
    button.setAttribute("aria-expanded", "true");
    draw();
  }

  function close() {
    pop.hidden = true;
    button.setAttribute("aria-expanded", "false");
  }

  async function draw() {
    const token = ++drawing;
    const today = toIso(new Date());
    const thisMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
    const title = month.toLocaleDateString(undefined, { month: "long", year: "numeric" });

    const nav = (label, step, disabled) => {
      const b = el("button", { type: "button", className: "cal-nav", textContent: label, disabled });
      b.setAttribute("aria-label", step < 0 ? "Previous month" : "Next month");
      b.addEventListener("click", () => {
        month = new Date(month.getFullYear(), month.getMonth() + step, 1);
        draw();
      });
      return b;
    };

    const cells = WEEKDAYS.map((d) => el("span", { className: "cal-wd", textContent: d }));
    const blanks = (month.getDay() + 6) % 7;
    for (let i = 0; i < blanks; i++) cells.push(el("span"));
    const dayButtons = new Map();
    const daysInMonth = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
    for (let n = 1; n <= daysInMonth; n++) {
      const iso = toIso(new Date(month.getFullYear(), month.getMonth(), n));
      const b = el("button", { type: "button", className: `cal-day${iso === today ? " today" : ""}`, textContent: String(n), disabled: iso > today });
      b.setAttribute("aria-pressed", String(iso === getDay()));
      b.setAttribute("aria-label", fromIso(iso).toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", year: "numeric" }));
      b.addEventListener("click", () => onPick(iso));
      dayButtons.set(iso, b);
      cells.push(b);
    }

    const back = el("button", { type: "button", className: "link-btn small", textContent: "Back to this week", disabled: !getDay() });
    back.addEventListener("click", () => onPick(null));
    const todayBtn = el("button", { type: "button", className: "link-btn small", textContent: "Today" });
    todayBtn.addEventListener("click", () => onPick(today));

    pop.replaceChildren(
      el("div", { className: "cal-head" }, nav("‹", -1, false), el("span", { textContent: title }), nav("›", 1, month >= thisMonth)),
      el("div", { className: "cal-grid" }, ...cells),
      el("div", { className: "cal-foot" }, todayBtn, back),
      el("p", { className: "cal-note", textContent: "● days with cases · by checkup date" }),
    );

    try {
      const { days } = await loadMonth(`${month.getFullYear()}-${String(month.getMonth() + 1).padStart(2, "0")}`);
      if (token !== drawing) return;
      for (const [iso, count] of Object.entries(days)) {
        const b = dayButtons.get(iso);
        if (!b) continue;
        b.append(el("span", { className: "dot" }));
        b.setAttribute("aria-label", `${b.getAttribute("aria-label")}, ${count} case${count === 1 ? "" : "s"}`);
        b.title = `${count} case${count === 1 ? "" : "s"}`;
      }
    } catch {
    }
  }

  /** Call after the chosen day changes (here or on the other map). */
  function refresh() {
    const day = getDay();
    button.innerHTML = '<svg class="icon" aria-hidden="true"><use href="/img/icons.svg#calendar"/></svg>';
    button.append(el("span", { textContent: day ? fromIso(day).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "Pick a day" }));
    button.classList.toggle("active", Boolean(day));
    button.setAttribute("aria-label", day ? `Showing ${button.textContent}. Change the day` : "Pick a day to show on the map");
    if (!pop.hidden) draw();
  }

  refresh();
  return { refresh, close };
}

/** "YYYY-MM-DD" in local time (toISOString would shift it to UTC). */
export function toIso(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function fromIso(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}
