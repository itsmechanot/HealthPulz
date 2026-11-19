// Text always goes through textContent, so server data is never parsed as HTML.

/** el("div", { className: "x", textContent: "hi" }, child1, child2) */
export function el(tag, props = {}, ...children) {
  const { dataset, ...rest } = props;
  const attributes = Object.entries(rest).filter(([name]) => name.includes("-"));
  const properties = Object.fromEntries(Object.entries(rest).filter(([name]) => !name.includes("-")));
  const node = Object.assign(document.createElement(tag), properties);
  for (const [name, value] of attributes) if (value != null && value !== false) node.setAttribute(name, value === true ? "" : String(value));
  Object.assign(node.dataset, dataset);
  node.append(...children.filter((child) => child != null && child !== false));
  return node;
}
