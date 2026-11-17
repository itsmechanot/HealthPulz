// The RHU's disease groups (rhu/data/diseases.json): how they are labeled and listed.

import { el } from "./dom.js";

export const GROUP_LABELS = {
  common: "Commonly reported conditions",
  urgent: "Urgent: a single case raises an alert",
  monitored: "Also monitored",
};

/** <optgroup>s of the tracked diseases, in the RHU's order, for a disease dropdown. */
export function diseaseOptions(diseases) {
  return Object.entries(GROUP_LABELS).map(([group, label]) =>
    el(
      "optgroup",
      { label },
      ...diseases.filter((d) => d.group === group).map((d) => el("option", { value: d.disease_code, textContent: d.name })),
    ),
  );
}
