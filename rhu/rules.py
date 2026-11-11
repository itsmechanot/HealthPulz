"""Rules files (diseases, barangays), also served to the browsers through GET /api/config."""

import json
from functools import lru_cache

from rhu.config import DATA_DIR


def _load(filename: str) -> list[dict]:
    with open(DATA_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


@lru_cache
def diseases() -> list[dict]:
    return _load("diseases.json")


def tracked_diseases() -> list[dict]:
    """Diseases under outbreak surveillance ("Other consultations" are counted but never alert)."""
    return [d for d in diseases() if d["tracked"]]


@lru_cache
def barangays() -> list[dict]:
    return _load("barangays.json")


@lru_cache
def barangays_by_id() -> dict[str, dict]:
    return {b["barangay_id"]: b for b in barangays()}
