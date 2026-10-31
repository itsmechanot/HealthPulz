# HealthPulz — Parallel Analytics

**Status:** Draft v1 · living document, update as development goes on
**Code:** [`rhu/analytics.py`](../rhu/analytics.py) · **Benchmark:** [`tools/benchmark_analytics.py`](../tools/benchmark_analytics.py)

## What it computes

For every **barangay × disease × ISO week**, from the first case up to the current week:

| Output (table `weekly_stats`) | Meaning |
|---|---|
| `case_count` | Cases whose onset date falls in that week |
| `alert_threshold` | mean + 2·SD of the baseline |
| `epidemic_threshold` | mean + 3·SD of the baseline |

**Baseline:** the case counts for the same ISO week **±1 week** in up to **5 previous years**: 3 weeks × each past year, so 9 values with 3 years of history. This follows the idea of CDC's "historical limits" method. The ±1 window makes the baseline steadier than using a single week. A threshold needs at least **2 previous years**; otherwise it is empty (NULL).

> **TODO (docs team):** cite the DOH/PIDSR threshold method and the CDC historical limits method, and note where ours differs.

## How it runs in parallel (MapReduce)

```
                 ┌─ worker process 1: Brgy. Agpangi    ─┐
cases table ──►  ├─ worker process 2: Brgy. Anislagan  ─┤ ──► main process merges rows ──► weekly_stats
  (read-only)    ├─ ...                                 ─┤       (one transaction, single writer)
                 └─ worker process N: Brgy. Villa Consuelo┘
        MAP: count cases per week + compute thresholds          REDUCE: merge + write
```

- **Partitioning:** one task per barangay (26 tasks). Workers take tasks one at a time (`chunksize=1`), so a worker that finishes a small barangay immediately takes the next. This is **dynamic load balancing**: barangays differ a lot in size (Caraycaray 6,800 vs Libtong 1,550).
- **Processes, not threads:** the map step is CPU-bound Python code. Python's GIL lets only one *thread* run Python code at a time, so threads would not speed it up. Separate *processes* each have their own interpreter and run truly in parallel on different CPU cores.
- **Drill data is skipped:** cases from batches marked `drill` (stress tests) are not read.
- **No shared memory:** each worker opens its own read-only database connection and returns its results as a message (the rows). Only the main process writes.
- **The pool is created once** when the server starts and reused, because starting processes is slow (about a second on Windows).

### When it runs
The server re-runs the analytics automatically every 10 s **if cases changed** (new batches stored, or history re-imported), then checks for outbreak alerts (see [protocol.md](protocol.md#alerts)). It can also be triggered with `POST /api/analytics/run`. `GET /api/analytics` and the System Monitor feed show the last run: worker count, cases read, map time and reduce time.

## Benchmark results

Reference run: 12-core laptop, 928,576 cases (`python -m rhu.history --scale 50`), median of 3 runs per setting.

| Workers | Total (s) | Map (s) | Reduce (s) | Speedup | Efficiency |
|---|---|---|---|---|---|
| 1 | 2.23 | 2.07 | 0.14 | 1.00× | 100% |
| 2 | 1.22 | 1.11 | 0.10 | 1.83× | 91% |
| 4 | 0.85 | 0.74 | 0.10 | 2.63× | 66% |
| 8 | 0.63 | 0.53 | 0.10 | 3.55× | 44% |

**Why efficiency drops as workers increase** (talking points for the defense):
1. **Amdahl's law:** the reduce step (writing ~31,000 rows) is sequential and takes ~0.1 s no matter how many workers there are. As the map step gets faster, this fixed part becomes a larger share of the total.
2. **Load imbalance:** with 26 uneven barangays and 8 workers, the run can't finish before the largest barangay is done, so some workers sit idle at the end.
3. **Overhead:** sending tasks and results between processes, and all workers reading the same database file at once.

To reproduce:
```powershell
python -m rhu.history --db var/bench.db --scale 50
python tools/benchmark_analytics.py --db var/bench.db --workers 1 2 4 8 --csv var/bench.csv
```

## Detection quality (seed 42, 3 years of history)

| Rule | Injected outbreak weeks flagged | False alarms |
|---|---|---|
| count > alert threshold | 58% | 9.0% of normal weeks (~12 per week across Naval) |
| count > alert threshold **and count ≥ 3** | 58% (≥75% of outbreaks flagged in at least one week) | 2.8% of normal weeks (~3.6 per week across Naval) |

The minimum of 3 cases cuts false alarms by about 70% without missing any more outbreak weeks in this run (both rules flag 19 of 33). Figures are from the history with real barangay neighbors (outbreaks spill over ×1.5 into neighboring barangays). This trade-off between sensitivity and false alarms is normal for threshold-based surveillance.

> **Note:** the map step counts cases record by record in Python. SQL's `GROUP BY` could count faster, but the per-record map step is kept on purpose to show data parallelism.
