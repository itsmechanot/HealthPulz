// Text always goes through textContent, so server data is never parsed as HTML.

/** el("div", { className: "x", textContent: "hi" }, child1, child2) */
export function el(tag, props = {}, ...children) {
  const { dataset, ...rest } = props;
  const node = Object.assign(document.createElement(tag), rest);
  Object.assign(node.dataset, dataset);
  node.append(...children.filter((child) => child != null && child !== false));
  return node;
}
