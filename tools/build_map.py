"""Builds the offline Naval map and barangay neighbor lists from PSGC boundaries
(faeldon/philippines-json-maps, MIT license):
    https://raw.githubusercontent.com/faeldon/philippines-json-maps/master/latest/geojson/provdists/medres/municities-provdist-807800000.0.01.json
    https://raw.githubusercontent.com/faeldon/philippines-json-maps/master/latest/geojson/municities/medres/bgysubmuns-municity-807808000.0.01.json

Usage:  python tools/build_map.py --province biliran.json --municipality naval.json
"""

import argparse
import json
import math
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BARANGAYS_FILE = ROOT / "rhu" / "data" / "barangays.json"
MAP_FILE = ROOT / "web" / "data" / "naval-map.json"
NAVAL_PSGC = 807808000
WIDTH = 1000  # SVG units; the height follows from the shape of the framed area
PADDING = 0.12  # extra space around Naval, so neighboring towns show at the edges
ATTRIBUTION = "Boundaries: PSA PSGC 3Q 2026 via faeldon/philippines-json-maps (MIT)"


def polygons(geometry: dict) -> list[list[list[list[float]]]]:
    """Every polygon of a Polygon/MultiPolygon as a list of rings."""
    return [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]


class Projection:
    """Equirectangular projection centered on Naval (accurate enough for one town)."""

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--province", type=Path, required=True, help="Biliran municipalities GeoJSON")
    parser.add_argument("--municipality", type=Path, required=True, help="Naval barangays GeoJSON")
    args = parser.parse_args()

    towns = json.loads(args.province.read_text(encoding="utf-8"))["features"]
    barangay_shapes = {
        f["properties"]["adm4_psgc"]: f for f in json.loads(args.municipality.read_text(encoding="utf-8"))["features"]
    }
    barangays = json.loads(BARANGAYS_FILE.read_text(encoding="utf-8"))
    missing = [b["name"] for b in barangays if b["psgc"] not in barangay_shapes]
    if missing:
        raise SystemExit(f"No boundary for: {', '.join(missing)}")

    naval = next(f for f in towns if f["properties"]["adm3_psgc"] == NAVAL_PSGC)
    project = Projection([naval])

    # Neighbors: barangays whose boundaries touch.
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
        "naval": {"path": svg_path(naval["geometry"], project), "label": label_point(naval["geometry"], project)},
        "towns": [
            {"name": f["properties"]["adm3_en"], "path": svg_path(f["geometry"], project),
             "label": label_point(f["geometry"], project)}
            for f in towns if f["properties"]["adm3_psgc"] != NAVAL_PSGC
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
    MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    MAP_FILE.write_text(json.dumps(map_data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # One barangay per line, like the hand-written file.
    lines = [json.dumps(b, ensure_ascii=False).replace('{"', '{ "').replace("]}", "] }") for b in barangays]
    BARANGAYS_FILE.write_text("[\n  " + ",\n  ".join(lines) + "\n]\n", encoding="utf-8")

    print(f"Map: {MAP_FILE.relative_to(ROOT)} ({MAP_FILE.stat().st_size / 1024:.0f} KB, {WIDTH}x{project.height})")
    for b in barangays:
        names = ", ".join(by_psgc_name(barangays, n) for n in b["neighbors"]) or "(none: island or no shared border)"
        print(f"  {b['name']:<32} {names}")


def by_psgc_name(barangays: list[dict], barangay_id: str) -> str:
    return next(b["name"] for b in barangays if b["barangay_id"] == barangay_id)


if __name__ == "__main__":
    main()
