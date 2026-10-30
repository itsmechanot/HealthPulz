"""Synthetic case generation (rules: docs/synthetic-data.md).

Python twin of web/js/node/generator.js; both read rhu/data/diseases.json.
All randomness comes from the `rng` passed in, so a fixed seed gives the
same data every time (reproducible results for the paper).
"""

import math
import random
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta

PUROKS = 7
NEIGHBOR_SPILLOVER = 1.5


@dataclass(frozen=True)
class Outbreak:
    barangay_id: str
    disease_code: str
    start: date  # Monday of the first outbreak week
    weeks: int
    multiplier: float

    def covers(self, week_start: date) -> bool:
        return self.start <= week_start < self.start + timedelta(weeks=self.weeks)


def iso_week_mondays(start: date, end: date) -> list[date]:
    """Mondays of every ISO week from the week containing `start` up to (not including) the week of `end`."""
    monday = start - timedelta(days=start.weekday())
    last = end - timedelta(days=end.weekday())
    weeks = []
    while monday < last:
        weeks.append(monday)
        monday += timedelta(weeks=1)
    return weeks


def plan_outbreaks(
    rng: random.Random, barangays: list[dict], diseases: list[dict], weeks: list[date], count: int
) -> list[Outbreak]:
    # Single-case-alert diseases (measles) already alert on any case, so they don't need injected outbreaks.
    candidates = [d for d in diseases if not d["single_case_alert"]]
    return [
        Outbreak(
            barangay_id=rng.choice(barangays)["barangay_id"],
            disease_code=rng.choice(candidates)["disease_code"],
            start=rng.choice(weeks),
            weeks=rng.randint(2, 4),
            multiplier=round(rng.uniform(3, 5), 1),
        )
        for _ in range(count)
    ]


def generate_cases(
    rng: random.Random,
    barangays: list[dict],
    diseases: list[dict],
    weeks: list[date],
    outbreaks: list[Outbreak],
    *,
    latest: date,
    scale: float = 1,
) -> Iterator[tuple[str, dict]]:
    """Yields (barangay_id, case) for every barangay × disease × week.

    `scale` multiplies every rate. It's used to make large datasets for benchmarks.
    """
    neighbors = {b["barangay_id"]: set(b["neighbors"]) for b in barangays}
    for week_start in weeks:
        # Thursday decides the month, the same way ISO 8601 decides which year a week belongs to.
        month = (week_start + timedelta(days=3)).month
        for barangay in barangays:
            for disease in diseases:
                rate = (
                    disease["base_rate"]
                    * (barangay["population"] / 10_000)
                    * disease["season"][month - 1]
                    * rng.uniform(0.8, 1.2)
                    * scale
                    * _outbreak_multiplier(outbreaks, barangay["barangay_id"], disease["disease_code"], week_start, neighbors)
                )
                for _ in range(poisson(rng, rate)):
                    yield barangay["barangay_id"], make_case(rng, disease, week_start, latest)


def _outbreak_multiplier(
    outbreaks: list[Outbreak], barangay_id: str, disease_code: str, week_start: date, neighbors: dict[str, set[str]]
) -> float:
    multiplier = 1.0
    for outbreak in outbreaks:
        if outbreak.disease_code != disease_code or not outbreak.covers(week_start):
            continue
        if outbreak.barangay_id == barangay_id:
            multiplier = max(multiplier, outbreak.multiplier)
        elif barangay_id in neighbors[outbreak.barangay_id]:
            multiplier = max(multiplier, NEIGHBOR_SPILLOVER)
    return multiplier


def make_case(rng: random.Random, disease: dict, week_start: date, latest: date) -> dict:
    onset = week_start + timedelta(days=rng.randint(0, 6))
    consult = min(onset + timedelta(days=rng.randint(0, 3)), latest)
    band = rng.choices(disease["age_profile"], weights=[b["weight"] for b in disease["age_profile"]])[0]
    return {
        "record_id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
        "disease_code": disease["disease_code"],
        "onset_date": onset.isoformat(),
        "consult_date": consult.isoformat(),
        "age_years": rng.randint(band["min"], band["max"]),
        "sex": "M" if rng.random() < disease["male_ratio"] else "F",
        "purok": rng.randint(1, PUROKS),
        "case_status": "SUSPECTED" if rng.random() < 0.7 else "CONFIRMED",
    }


def poisson(rng: random.Random, rate: float) -> int:
    """Poisson sample. Knuth's method for small rates; normal approximation for large
    ones (Knuth is slow there and exp(-rate) underflows)."""
    if rate <= 0:
        return 0
    if rate > 30:
        return max(0, round(rng.gauss(rate, math.sqrt(rate))))
    limit = math.exp(-rate)
    k, p = 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1
