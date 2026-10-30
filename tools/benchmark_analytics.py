"""Measures the speedup of the analytics process pool (evidence for the paper).

Runs the full analytics with 1, 2, 4, ... worker processes on the same data and
reports the median time, speedup (T1 / Tn) and efficiency (speedup / n). The
map step runs in parallel; the reduce step (writing weekly_stats) is sequential,
so it limits the overall speedup (Amdahl's law).

Usage:
    python -m rhu.history --db var/bench.db --scale 50     # once: ~930,000 cases
    python tools/benchmark_analytics.py --db var/bench.db --workers 1 2 4 8 --csv var/bench.csv
"""

import argparse
import csv
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # allow `python tools/...` from the project root

from rhu.analytics import Analytics  # noqa: E402


def benchmark(db_path: Path, workers: int, repeat: int) -> dict:
    analytics = Analytics(db_path, workers=workers)
    try:
        analytics.run()  # warm-up: starts the worker processes and fills the OS file cache
        runs = [analytics.run() for _ in range(repeat)]
    finally:
        analytics.close()
    return {
        "workers": workers,
        "cases": runs[0].cases,
        "total": statistics.median(r.total_seconds for r in runs),
        "map": statistics.median(r.map_seconds for r in runs),
        "reduce": statistics.median(r.reduce_seconds for r in runs),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4, 8])
    parser.add_argument("--repeat", type=int, default=3, help="timed runs per worker count (median is reported)")
    parser.add_argument("--csv", type=Path, help="also save the results as CSV")
    args = parser.parse_args()

    if not args.db.exists():
        sys.exit(f"{args.db} not found. Create it first: python -m rhu.history --db {args.db} --scale 50")

    print(f"CPU cores: {os.cpu_count()}   Database: {args.db}")
    results = [benchmark(args.db, n, args.repeat) for n in args.workers]
    base = results[0]["total"]
    for r in results:
        r["speedup"] = base / r["total"]
        r["efficiency"] = r["speedup"] / r["workers"] * results[0]["workers"]

    print(f"Cases analyzed: {results[0]['cases']:,}\n")
    print(f"{'Workers':>7} {'Total (s)':>10} {'Map (s)':>9} {'Reduce (s)':>11} {'Speedup':>8} {'Efficiency':>11}")
    for r in results:
        print(
            f"{r['workers']:>7} {r['total']:>10.2f} {r['map']:>9.2f} {r['reduce']:>11.2f}"
            f" {r['speedup']:>7.2f}x {r['efficiency']:>10.0%}"
        )

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(results[0]))
            writer.writeheader()
            writer.writerows(results)
        print(f"\nSaved to {args.csv}")


if __name__ == "__main__":
    main()
