// Minimal SVG line chart (no external library, so pages work on a LAN without internet).

const SVG_NS = "http://www.w3.org/2000/svg";

/**
 * Draws one or more series into an <svg>. Values are plotted left to right,
 * newest on the right; the y-axis starts at 0 and scales to the largest value.
 * series: [{ values: number[], className: string }]
 * Returns the y-axis maximum, so the caller can label the scale.
 */
export function lineChart(svg, series, { slots }) {
  const height = 100;
  const max = Math.max(1, ...series.flatMap((s) => s.values)) * 1.1;
  svg.setAttribute("viewBox", `0 0 ${slots - 1} ${height}`);
  svg.setAttribute("preserveAspectRatio", "none");

  const paths = series.map(({ values, className }) => {
    const offset = slots - values.length; // right-align: the newest value is at the right edge
    const points = values.map((v, i) => `${offset + i},${(height - (v / max) * height).toFixed(2)}`);
    const path = document.createElementNS(SVG_NS, "polyline");
    path.setAttribute("points", points.join(" "));
    path.setAttribute("class", className);
    path.setAttribute("vector-effect", "non-scaling-stroke");
    return path;
  });
  svg.replaceChildren(...paths);
  return max / 1.1;
}
