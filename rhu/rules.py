"""Shared rules files (diseases, barangays).

The same files are served to the BHS nodes through GET /api/config, so the
Python and JavaScript data generators always use identical rules.
"""

import json
from functools import lru_cache

from rhu.config import DATA_DIR


def _load(filename: str) -> list[dict]:
    with open(DATA_DIR / filename, encoding="utf-8") as f:
        return json.load(f)


@lru_cache
def diseases() -> list[dict]:
    return _load("diseases.json")


@lru_cache
def barangays() -> list[dict]:
    return _load("barangays.json")


@lru_cache
def barangays_by_id() -> dict[str, dict]:
    return {b["barangay_id"]: b for b in barangays()}
