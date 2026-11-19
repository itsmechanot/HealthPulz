"""Builds the offline map of a municipality and its barangays' neighbor lists from official PSGC
boundaries (faeldon/philippines-json-maps, MIT license). Any municipality: give its PSGC code.
    https://raw.githubusercontent.com/faeldon/philippines-json-maps/master/latest/geojson/provdists/medres/municities-provdist-<PROVINCE PSGC>.0.01.json
    https://raw.githubusercontent.com/faeldon/philippines-json-maps/master/latest/geojson/municities/medres/bgysubmuns-municity-<MUNICIPALITY PSGC>.0.01.json

Usage:  python tools/build_map.py --province biliran.json --municipality naval.json
        python tools/build_map.py --province biliran.json --municipality caibiran.json --psgc 807804000 --new
"""

import argparse
import json
import math
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BARANGAYS_FILE = ROOT / "rhu" / "data" / "barangays.json"
MUNICIPALITY_FILE = ROOT / "rhu" / "data" / "municipality.json"
MAP_FILE = ROOT / "web" / "data" / "map.json"
PLACEHOLDER_POPULATION = 1000
MAX_BARANGAYS = 99
WIDTH = 1000
PADDING = 0.12
ATTRIBUTION = "Boundaries: PSA PSGC 3Q 2026 via faeldon/philippines-json-maps (MIT)"


def polygons(geometry: dict) -> list[list[list[list[float]]]]:
    """Every polygon of a Polygon/MultiPolygon as a list of rings."""
    return [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]


class Projection:
    """Equirectangular projection centered on the municipality (accurate enough for one town)."""

    def __init__(self, features: list[dict]) -> None:
        lons = [p[0] for f in features for poly in polygons(f["geometry"]) for ring in poly for p in ring]
        lats = [p[1] for f in features for poly in polygons(f["geometry"]) for ring in poly for p in ring]
        self.x_scale = math.cos(math.radians((min(lats) + max(lats)) / 2))
        width, height = (max(lons) - min(lons)) * self.x_scale, max(lats) - min(lats)
        self.lon0 = min(lons) - width * PADDING / self.x_scale
        self.lat0 = max(lats) + height * PADDING
        self.k = WIDTH / (width * (1 + 2 * PADDING))
        self.height = round(height * (1 + 2 * PADDING) * self.k)

    def __call__(self, lon: float, lat: float) -> tuple[float, float]:
        return (lon - self.lon0) * self.x_scale * self.k, (self.lat0 - lat) * self.k


def svg_path(geometry: dict, project: Projection) -> str:
    parts = []
    for poly in polygons(geometry):
        for ring in poly:
            points = [project(lon, lat) for lon, lat in ring]
            parts.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in points) + "Z")
    return "".join(parts)


def label_point(geometry: dict, project: Projection) -> list[float]:
    """Area centroid of the largest polygon (so islands don't pull the label into the sea)."""
    best, best_area = None, -1.0
    for poly in polygons(geometry):
        ring = [project(lon, lat) for lon, lat in poly[0]]
        area = cx = cy = 0.0
        for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
            cross = x1 * y2 - x2 * y1
            area += cross
            cx += (x1 + x2) * cross
            cy += (y1 + y2) * cross
        if abs(area) > best_area:
            best_area, best = abs(area), [round(cx / (3 * area), 1), round(cy / (3 * area), 1)]
    return best


def touching(a: dict, b: dict, tolerance: float = 0.0004) -> bool:
    """Two boundaries touch if they share points or have points within ~40 m."""
    points_a = {(round(x, 6), round(y, 6)) for poly in polygons(a) for ring in poly for x, y in ring}
    points_b = {(round(x, 6), round(y, 6)) for poly in polygons(b) for ring in poly for x, y in ring}
    if len(points_a & points_b) >= 2:
        return True
    close = sum(
        1 for xa, ya in points_a if any(abs(xa - xb) < tolerance and abs(ya - yb) < tolerance for xb, yb in points_b)
    )
    return close >= 2


def new_barangay_list(shapes: dict[int, dict], old: list[dict]) -> list[dict]:
    """A new municipality: one entry per boundary, IDs BRGY-01, BRGY-02, … in name order."""
    if len(shapes) > MAX_BARANGAYS:
        raise SystemExit(f"{len(shapes)} barangays; IDs allow at most {MAX_BARANGAYS}")
    known = {b["psgc"]: b["population"] for b in old}
    ordered = sorted(shapes.values(), key=lambda f: f["properties"]["adm4_en"])
    return [
        {
            "barangay_id": f"BRGY-{n:02d}",
            "psgc": f["properties"]["adm4_psgc"],
            "name": f["properties"]["adm4_en"],
            "population": known.get(f["properties"]["adm4_psgc"], PLACEHOLDER_POPULATION),
            "neighbors": [],
        }
        for n, f in enumerate(ordered, start=1)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--province", type=Path, required=True, help="the province's municipalities GeoJSON (provdists/…)")
    parser.add_argument("--municipality", type=Path, required=True, help="the municipality's barangays GeoJSON (municities/…)")
    parser.add_argument("--psgc", type=int, help="the municipality's PSGC code (default: the one in rhu/data/municipality.json)")
    parser.add_argument("--new", action="store_true", help="a new municipality: make the barangay list from the boundaries")
    parser.add_argument("--barangays", type=Path, default=BARANGAYS_FILE, help="barangay list to read and update")
    parser.add_argument("--map-out", type=Path, default=MAP_FILE, help="where to write the map")
    args = parser.parse_args()

    psgc = args.psgc or json.loads(MUNICIPALITY_FILE.read_text(encoding="utf-8"))["psgc"]
    towns = json.loads(args.province.read_text(encoding="utf-8"))["features"]
    town = next((f for f in towns if f["properties"]["adm3_psgc"] == psgc), None)
    if town is None:
        listed = ", ".join(f"{f['properties']['adm3_en']} ({f['properties']['adm3_psgc']})" for f in towns)
        raise SystemExit(f"No municipality with PSGC {psgc} in {args.province}. It has: {listed}")
    barangay_shapes = {
        f["properties"]["adm4_psgc"]: f for f in json.loads(args.municipality.read_text(encoding="utf-8"))["features"]
    }
    old = json.loads(args.barangays.read_text(encoding="utf-8")) if args.barangays.exists() else []
    barangays = new_barangay_list(barangay_shapes, old) if args.new else old
    missing = [b["name"] for b in barangays if b["psgc"] not in barangay_shapes]
    if missing:
        raise SystemExit(f"No boundary for: {', '.join(missing)}. A new municipality needs --new.")

    project = Projection([town])

    by_psgc = {b["psgc"]: b for b in barangays}
    for b in barangays:
        b["neighbors"] = []
    for code_a, code_b in combinations(by_psgc, 2):
        if touching(barangay_shapes[code_a]["geometry"], barangay_shapes[code_b]["geometry"]):
            by_psgc[code_a]["neighbors"].append(by_psgc[code_b]["barangay_id"])
            by_psgc[code_b]["neighbors"].append(by_psgc[code_a]["barangay_id"])
    for b in barangays:
        b["neighbors"].sort()

    map_data = {
        "attribution": ATTRIBUTION,
        "width": WIDTH,
        "height": project.height,
        "municipality": {
            "name": town["properties"]["adm3_en"],
            "path": svg_path(town["geometry"], project),
            "label": label_point(town["geometry"], project),
        },
        "towns": [
            {"name": f["properties"]["adm3_en"], "path": svg_path(f["geometry"], project),
             "label": label_point(f["geometry"], project)}
            for f in towns if f["properties"]["adm3_psgc"] != psgc
        ],
        "barangays": [
            {
                "barangay_id": b["barangay_id"],
                "psgc": b["psgc"],
                "name": b["name"],
                "path": svg_path(barangay_shapes[b["psgc"]]["geometry"], project),
                "center": label_point(barangay_shapes[b["psgc"]]["geometry"], project),
                "area_km2": round(barangay_shapes[b["psgc"]]["properties"]["area_km2"], 2),
            }
            for b in barangays
        ],
    }
    args.map_out.parent.mkdir(parents=True, exist_ok=True)
    args.map_out.write_text(json.dumps(map_data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    lines = [json.dumps(b, ensure_ascii=False).replace('{"', '{ "').replace("]}", "] }") for b in barangays]
    args.barangays.write_text("[\n  " + ",\n  ".join(lines) + "\n]\n", encoding="utf-8")

    print(f"Map of {map_data['municipality']['name']}: {args.map_out} ({args.map_out.stat().st_size / 1024:.0f} KB, {WIDTH}x{project.height})")
    print(f"Barangays: {args.barangays} ({len(barangays)})")
    for b in barangays:
        names = ", ".join(by_psgc_name(barangays, n) for n in b["neighbors"]) or "(none: island or no shared border)"
        print(f"  {b['barangay_id']}  {b['name']:<32} {names}")
    placeholders = [b["name"] for b in barangays if b["population"] == PLACEHOLDER_POPULATION]
    if placeholders:
        print(f"\nSet the real population of {len(placeholders)} barangay(s) in {args.barangays} (now {PLACEHOLDER_POPULATION}).")


def by_psgc_name(barangays: list[dict], barangay_id: str) -> str:
    return next(b["name"] for b in barangays if b["barangay_id"] == barangay_id)


if __name__ == "__main__":
    main()
