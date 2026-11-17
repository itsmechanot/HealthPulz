// The municipality's names (rhu/data/municipality.json, from /api/config), so no page has them
// built in. The HTML keeps Naval's names as the text shown before the config arrives.

let place = { name: "Naval", province: "Biliran", rhu_name: "RHU Naval" };

/** "Cases in {town}, {province}" → "Cases in Naval, Biliran". Keys: {town} {province} {rhu}; {TOWN} {PROVINCE} in capitals. */
export function fillPlace(template) {
  return template
    .replaceAll("{town}", place.name).replaceAll("{province}", place.province).replaceAll("{rhu}", place.rhu_name)
    .replaceAll("{TOWN}", place.name.toUpperCase()).replaceAll("{PROVINCE}", place.province.toUpperCase());
}

/** Call once the config has loaded: fills every data-place (text) and data-place-placeholder on the page. */
export function applyPlace(municipality) {
  if (municipality) place = municipality;
  document.querySelectorAll("[data-place]").forEach((node) => (node.textContent = fillPlace(node.dataset.place)));
  document.querySelectorAll("[data-place-placeholder]").forEach((node) => (node.placeholder = fillPlace(node.dataset.placePlaceholder)));
  return place;
}
