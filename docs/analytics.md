# HealthPulz — Parallel Analytics

**Status:** Draft v1 · living document, update as development goes on
**Code:** [`rhu/analytics.py`](../rhu/analytics.py) · **Benchmark:** [`tools/benchmark_analytics.py`](../tools/benchmark_analytics.py)

## What it computes

For every **barangay × disease × ISO week**, from the first case up to the current week:

| Output (table `weekly_stats`) | Meaning |
|---|---|
| `case_count` | Cases whose **checkup date** falls in that week, so what a BHU sends today shows in this week's numbers |
| `alert_threshold` | mean + 2·SD of the baseline |
| `epidemic_threshold` | mean + 3·SD of the baseline |

**Baseline:** the case counts for the same ISO week **±1 week** in up to **5 previous years**: 3 weeks × each past year, so 9 values with 3 years of history. This follows the idea of CDC's "historical limits" method. The ±1 window makes the baseline steadier than using a single week. A threshold needs at least **2 previous years**; otherwise it is empty (NULL).

> **TODO (docs team):** cite the DOH/PIDSR threshold method and the CDC historical limits method, and note where ours differs.

**Thresholds apply only to the 9 contagious diseases.** Non-communicable illnesses and Other consultations are counted but can't raise alerts, and for the 6 urgent diseases any single case is already an URGENT alert.

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

Reference run: 12-core laptop, 905,409 cases (`python -m rhu.history --scale 16`). Runs take turns between worker counts and report the median of 7, so a busy moment on the machine affects all of them alike.

| Workers | Total (s) | Map (s) | Reduce (s) | Speedup | Efficiency | Fastest–slowest (s) |
|---|---|---|---|---|---|---|
| 1 | 9.52 | 8.53 | 0.99 | 1.00× | 100% | 3.23–11.02 |
| 2 | 5.64 | 4.59 | 1.15 | 1.69× | 84% | 2.30–6.05 |
| 4 | 3.21 | 1.93 | 1.26 | 2.96× | 74% | 2.10–3.31 |
| 8 | 2.13 | 1.05 | 1.11 | 4.46× | 56% | 1.04–2.46 |

A second pass gave 2.02×, 3.73× and 4.66×.

> **How far to trust these:** the laptop was running other programs, and the 1-worker time varied a lot. Comparing the *fastest* runs instead gives about 3× at 8 workers, so read this as **about 3–4.5×**, not an exact figure. **Before quoting numbers in the paper, re-run the benchmark with other apps closed.**

**Why efficiency drops as workers increase** (talking points for the defense):
1. **Amdahl's law:** the reduce step (writing ~104,000 rows) is sequential and takes about 1 s no matter how many workers there are. As the map step gets faster, this fixed part becomes a larger share of the total.
2. **Load imbalance:** with 26 uneven barangays and 8 workers, the run can't finish before the largest barangay is done, so some workers sit idle at the end.
3. **Overhead:** sending tasks and results between processes, and all workers reading the same database file at once.

**Why a covering index matters:** the workers read only one index (`idx_cases_weekly`), which holds every column they need. Without it, each case meant a jump to its table row. That work is limited by the disk, not the CPU, so adding processes barely helped: in one run, 4 workers were no faster than 1.

To reproduce:
```powershell
python -m rhu.history --db var/bench.db --scale 16
python tools/benchmark_analytics.py --db var/bench.db --workers 1 2 4 8 --csv var/bench.csv
```

## Detection quality (seed 42, 3 years of history, the 9 diseases with thresholds)

| Rule | Injected outbreak weeks flagged | False alarms |
|---|---|---|
| count > alert threshold | 56% (19 of 34) | 8.5% of normal weeks (~20 per week across Naval) |
| count > alert threshold **and count ≥ 3** | 35% (12 of 34); 7 of 11 checkable outbreaks in at least one week | 2.8% of normal weeks (~6.6 per week across Naval) |

The minimum of 3 cases cuts false alarms by about two-thirds, at the cost of missing small outbreaks, especially of rare diseases and in small barangays. This trade-off is normal for threshold-based surveillance, and it matters because all of these figures come from synthetic data with estimated rates. They show the method works, not how it would perform on Naval's real data.

> **Note:** the map step counts cases record by record in Python. SQL's `GROUP BY` could count faster, but the per-record map step is kept on purpose to show data parallelism.
