"""Imports synthetic past data so the outbreak thresholds have a baseline.

Usage:
    python -m rhu.history                  # 3 past years + this year up to last week
    python -m rhu.history --years 5 --seed 7
    python -m rhu.history --scale 50       # large dataset for the speedup benchmark

History is stored under one HISTORY node per barangay (e.g. 'BRGY-08-HIST').
Running it again replaces the previous history; live data from nodes is never touched.
"""

import argparse
import random
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from itertools import batched
from pathlib import Path

from rhu import config, db, rules
from rhu.synthetic import Outbreak, generate_cases, iso_week_mondays, plan_outbreaks

INSERT_CHUNK = 10_000


@dataclass
class ImportResult:
    cases: int
    weeks: list[date]
    outbreaks: list[Outbreak]


def history_node_id(barangay_id: str) -> str:
    return f"{barangay_id}-HIST"


def import_history(
    db_path: Path,
    *,
    years: int = 3,
    seed: int = 42,
    scale: float = 1,
    outbreaks_per_year: int = 4,
    today: date | None = None,
) -> ImportResult:
    today = today or date.today()
    rng = random.Random(seed)
    barangays, diseases = rules.barangays(), rules.tracked_diseases()
    # From ISO week 1 of `years` years ago up to the end of last week; the current week is left for live data.
    weeks = iso_week_mondays(date.fromisocalendar(today.year - years, 1, 1), today)
    outbreaks = plan_outbreaks(rng, barangays, diseases, weeks, outbreaks_per_year * (years + 1))
    batch_ids = {
        b["barangay_id"]: str(uuid.uuid5(uuid.NAMESPACE_URL, f"healthpulz/history/{b['barangay_id']}"))
        for b in barangays
    }

    db.init_db(db_path)
    with closing(db.connect(db_path)) as conn, conn:
        _delete_history(conn)
        now = db.utc_now()
        for b in barangays:
            conn.execute(
                "INSERT INTO nodes (node_id, barangay_id, registered_at, status, kind) VALUES (?, ?, ?, 'OFFLINE', 'HISTORY')",
                (history_node_id(b["barangay_id"]), b["barangay_id"], now),
            )
            conn.execute(
                """
                INSERT INTO batches (batch_id, node_id, record_count, sent_at, received_at, stored_at, status)
                VALUES (?, ?, 0, ?, ?, ?, 'STORED')
                """,
                (batch_ids[b["barangay_id"]], history_node_id(b["barangay_id"]), now, now, now),
            )

        total = 0
        cases = generate_cases(rng, barangays, diseases, weeks, outbreaks, latest=today, scale=scale)
        for chunk in batched(cases, INSERT_CHUNK):
            conn.executemany(
                """
                INSERT INTO cases (
                  record_id, batch_id, node_id, barangay_id, disease_code, onset_date,
                  consult_date, age_years, sex, purok, case_status, received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        c["record_id"], batch_ids[barangay_id], history_node_id(barangay_id), barangay_id,
                        c["disease_code"], c["onset_date"], c["consult_date"], c["age_years"], c["sex"],
                        c["purok"], c["case_status"], now,
                    )
                    for barangay_id, c in chunk
                ],
            )
            total += len(chunk)

        conn.execute(
            """
            UPDATE batches
            SET record_count = (SELECT COUNT(*) FROM cases WHERE cases.batch_id = batches.batch_id),
                accepted = (SELECT COUNT(*) FROM cases WHERE cases.batch_id = batches.batch_id),
                duplicates = 0,
                rejected = 0
            WHERE node_id IN (SELECT node_id FROM nodes WHERE kind = 'HISTORY')
            """
        )
    return ImportResult(cases=total, weeks=weeks, outbreaks=outbreaks)


def _delete_history(conn) -> None:
    history_nodes = "SELECT node_id FROM nodes WHERE kind = 'HISTORY'"
    conn.execute(f"DELETE FROM cases WHERE node_id IN ({history_nodes})")
    conn.execute(f"DELETE FROM batches WHERE node_id IN ({history_nodes})")
    conn.execute("DELETE FROM nodes WHERE kind = 'HISTORY'")


def _iso_label(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=config.DB_PATH, help=f"database file (default: {config.DB_PATH})")
    parser.add_argument("--years", type=int, default=3, help="full past years to generate (default: 3)")
    parser.add_argument("--seed", type=int, default=42, help="random seed, for reproducible data (default: 42)")
    parser.add_argument("--scale", type=float, default=1, help="multiply all rates, for benchmarks (default: 1)")
    args = parser.parse_args()

    result = import_history(args.db, years=args.years, seed=args.seed, scale=args.scale)

    names = {b["barangay_id"]: b["name"] for b in rules.barangays()}
    diseases = {d["disease_code"]: d["name"] for d in rules.diseases()}
    print(f"History imported into {args.db}")
    print(f"  Weeks:  {_iso_label(result.weeks[0])} to {_iso_label(result.weeks[-1])} ({len(result.weeks)} weeks)")
    print(f"  Cases:  {result.cases:,}")
    print("  Injected outbreaks:")
    for o in sorted(result.outbreaks, key=lambda o: o.start):
        print(f"    {_iso_label(o.start)}  {diseases[o.disease_code]:<24} {names[o.barangay_id]:<30} {o.weeks} weeks  x{o.multiplier}")


if __name__ == "__main__":
    main()
