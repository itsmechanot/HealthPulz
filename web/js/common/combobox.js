// Searchable dropdown: type part of a name to filter the list.
// Keyboard: ↑/↓ to move, Enter to choose, Esc to close.
// Matching ignores case and accents, so "capinahan" finds "Capiñahan".

export function createCombobox({ input, listbox, options, labelOf, onSelect }) {
  let matches = [];
  let active = -1;

  const normalize = (text) => text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

  function render() {
    const query = normalize(input.value.trim());
    matches = options.filter((option) => normalize(labelOf(option)).includes(query));
    active = matches.length ? 0 : -1;
    listbox.replaceChildren(
      ...(matches.length
        ? matches.map((option, i) => {
            const li = document.createElement("li");
            li.id = `${listbox.id}-${i}`;
            li.setAttribute("role", "option");
            li.textContent = labelOf(option);
            li.addEventListener("mousedown", (event) => {
              event.preventDefault(); // keep focus in the input
              choose(i);
            });
            return li;
          })
        : [Object.assign(document.createElement("li"), { className: "no-match", textContent: "No match found" })]),
    );
    highlight();
    open(true);
  }

  function highlight() {
    [...listbox.querySelectorAll('[role="option"]')].forEach((li, i) => li.setAttribute("aria-selected", i === active));
    const current = listbox.children[active];
    input.setAttribute("aria-activedescendant", current?.id ?? "");
    current?.scrollIntoView({ block: "nearest" });
  }

  function open(isOpen) {
    listbox.hidden = !isOpen;
    input.setAttribute("aria-expanded", isOpen);
  }

  function choose(i) {
    const option = matches[i];
    if (!option) return;
    input.value = labelOf(option);
    open(false);
    onSelect(option);
  }

  input.addEventListener("input", () => {
    onSelect(null); // typing again clears the previous choice
    render();
  });
  input.addEventListener("focus", render);
  input.addEventListener("blur", () => open(false));
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (listbox.hidden) return render();
      const step = event.key === "ArrowDown" ? 1 : -1;
      active = Math.max(0, Math.min(matches.length - 1, active + step));
      highlight();
    } else if (event.key === "Enter" && !listbox.hidden) {
      event.preventDefault();
      choose(active);
    } else if (event.key === "Escape") {
      open(false);
    }
  });
}
