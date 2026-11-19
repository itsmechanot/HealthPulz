"""Measures the analytics speedup with 1, 2, 4, ... worker processes (evidence for the paper).

Usage:
    python -m rhu.history --db var/bench.db --scale 16
    python tools/benchmark_analytics.py --db var/bench.db --workers 1 2 4 8
"""

import argparse
import csv
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rhu.analytics import Analytics


def benchmark(db_path: Path, worker_counts: list[int], repeat: int) -> list[dict]:
    """Takes turns between worker counts, so a busy moment affects all of them alike."""
    pools = {n: Analytics(db_path, workers=n, rewrite_all=True) for n in worker_counts}
    try:
        for analytics in pools.values():
            analytics.run()
        runs = {n: [] for n in worker_counts}
        for _ in range(repeat):
            for n, analytics in pools.items():
                runs[n].append(analytics.run())
    finally:
        for analytics in pools.values():
            analytics.close()
    return [
        {
            "workers": n,
            "cases": runs[n][0].cases,
            "total": statistics.median(r.total_seconds for r in runs[n]),
            "map": statistics.median(r.map_seconds for r in runs[n]),
            "reduce": statistics.median(r.reduce_seconds for r in runs[n]),
            "fastest": min(r.total_seconds for r in runs[n]),
            "slowest": max(r.total_seconds for r in runs[n]),
        }
        for n in worker_counts
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4, 8])
    parser.add_argument("--repeat", type=int, default=7, help="timed runs per worker count (median is reported)")
    parser.add_argument("--csv", type=Path, help="also save the results as CSV")
    args = parser.parse_args()

    if not args.db.exists():
        sys.exit(f"{args.db} not found. Create it first: python -m rhu.history --db {args.db} --scale 16")

    print(f"CPU cores: {os.cpu_count()}   Database: {args.db}")
    results = benchmark(args.db, args.workers, args.repeat)
    base = results[0]["total"]
    for r in results:
        r["speedup"] = base / r["total"]
        r["efficiency"] = r["speedup"] / r["workers"] * results[0]["workers"]

    print(f"Cases analyzed: {results[0]['cases']:,}\n")
    print(f"{'Workers':>7} {'Total (s)':>10} {'Map (s)':>9} {'Reduce (s)':>11} {'Speedup':>8} {'Efficiency':>11} {'Range (s)':>13}")
    for r in results:
        print(
            f"{r['workers']:>7} {r['total']:>10.2f} {r['map']:>9.2f} {r['reduce']:>11.2f}"
            f" {r['speedup']:>7.2f}x {r['efficiency']:>10.0%} {r['fastest']:>6.2f}-{r['slowest']:.2f}"
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
