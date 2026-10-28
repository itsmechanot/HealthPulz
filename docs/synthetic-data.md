# HealthPulz — Synthetic Data Rules

**Status:** Draft v1 · living document, update as development goes on
**Municipality:** Naval, Biliran (RHU Naval + 26 Barangay Health Stations)

> ⚠️ **Disclaimer:** All case data and populations are **synthetic**. The rates below are illustrative values for simulation, not real epidemiology. The docs team should cite DOH/PIDSR sources for the general patterns used (e.g., dengue peaking in the rainy season) and state clearly in the paper that the data is synthetic.

---

## 1. Barangays

> **TODO (docs team):** verify the list and spellings against the PSA PSGC listing for Naval, and fill in the `neighbors` column from a map of Naval. Optionally, replace the synthetic populations with PSA 2020 census figures.

| ID | Barangay | Population (synthetic) | Neighbors |
|---|---|---|---|
| BRGY-01 | Agpangi | TBD | TODO |
| BRGY-02 | Anislagan | TBD | TODO |
| BRGY-03 | Atipolo | TBD | TODO |
| BRGY-04 | Borac | TBD | TODO |
| BRGY-05 | Cabungaan | TBD | TODO |
| BRGY-06 | Calumpang | TBD | TODO |
| BRGY-07 | Capiñahan | TBD | TODO |
| BRGY-08 | Caraycaray | TBD | TODO |
| BRGY-09 | Catmon | TBD | TODO |
| BRGY-10 | Haguikhikan | TBD | TODO |
| BRGY-11 | Higatangan | TBD | TODO |
| BRGY-12 | Imelda | TBD | TODO |
| BRGY-13 | Larrazabal | TBD | TODO |
| BRGY-14 | Libertad | TBD | TODO |
| BRGY-15 | Libtong | TBD | TODO |
| BRGY-16 | Lucsoon | TBD | TODO |
| BRGY-17 | Mabini | TBD | TODO |
| BRGY-18 | Padre Inocentes Garcia (Pob.) | TBD | TODO |
| BRGY-19 | Padre Sergio Eamiguel | TBD | TODO |
| BRGY-20 | Sabang | TBD | TODO |
| BRGY-21 | San Pablo | TBD | TODO |
| BRGY-22 | Santissimo Rosario Pob. (Santo Rosa) | TBD | TODO |
| BRGY-23 | Santo Niño | TBD | TODO |
| BRGY-24 | Talustusan | TBD | TODO |
| BRGY-25 | Villa Caneja | TBD | TODO |
| BRGY-26 | Villa Consuelo | TBD | TODO |

The current synthetic populations (1,500–12,000) and neighbor lists live in [`rhu/data/barangays.json`](../rhu/data/barangays.json), which is the source of truth. Update that file when real figures or neighbors are added.

### Nodes per barangay
There are 30–40 classmates and only 26 barangays, so some barangays will have **more than one node** (main BHS + satellite stations). Each node picks its barangay from a searchable dropdown; see [protocol.md](protocol.md#1-node-registration-dropdown-login).

---

## 2. Diseases

| Code | Disease | Category | Base rate (cases/week per 10k pop) | Peak months (multiplier) | Main age groups |
|---|---|---|---|---|---|
| `DEN` | Dengue | VECTOR | 1.5 | Jun–Oct (×3) | 5–19 |
| `ILI` | Influenza-like illness | RESPIRATORY | 4.0 | Jul–Sep, Dec–Feb (×2) | all ages |
| `AWD` | Acute watery diarrhea | WATERBORNE | 2.5 | Jun–Sep (×2) | 0–4, 60+ |
| `LEP` | Leptospirosis | WATERBORNE | 0.3 | Jul–Oct (×4) | 20–59, mostly male |
| `TYP` | Typhoid fever | WATERBORNE | 0.4 | Jun–Sep (×1.5) | 5–39 |
| `MEA` | Measles | VACCINE_PREVENTABLE | 0.05 | Jan–Apr (×2) | 0–9 · **any case = alert** |

Months outside the peak use a multiplier of **×1**.

---

## 3. Generation formula

For each barangay × disease × week:

```
λ = base_rate × (population / 10,000) × season_multiplier[month] × noise(0.8–1.2)
case_count ~ Poisson(λ)
```

Each generated case gets:
- `age_years`, from the disease's age profile
- `sex`: 50/50, except leptospirosis (~80% male)
- `purok`: 1–7
- `onset_date`: random day within the week
- `consult_date`: 0–3 days after onset
- `case_status`: ~70% SUSPECTED, ~30% CONFIRMED

---

## 4. Outbreak injection

Outbreaks are added on purpose so the alert system has something to detect:
- Pick a barangay + disease + start week, then multiply λ by **×3–5 for 2–4 weeks**.
- Neighboring barangays get **×1.5** for the same weeks (spillover).
- The generator uses a **fixed random seed**, so results can be reproduced for the paper.

---

## 5. Generator modes

| Mode | Runs in | Purpose |
|---|---|---|
| **History backfill** | Python, on the RHU | 3 years of past data, so thresholds have a baseline. A `--scale` option produces 1M+ records for the speedup benchmark. |
| **Live: Realistic** | JavaScript, in the node browser | Generates this week's cases for the node's barangay. |
| **Live: Stress** | JavaScript, in the node browser | Generates N records (e.g. 500) and sends them all at once, for the burst test. |
| **Live: Outbreak toggle** | JavaScript, in the node browser | Forces a spike so an alert fires live during the demo. |

Both the Python and JS generators read the **same rules file** (`diseases.json`, served at `GET /api/config`), so they can't drift apart.

---

## 6. Alert thresholds

Computed in parallel by the analytics process pool. For each barangay × disease × week, compared against the **same ISO week in previous years** of history:

| Level | Condition |
|---|---|
| **ALERT** | `count > mean + 2·SD` **and** `count ≥ 3` (the minimum stops tiny numbers from triggering alerts) |
| **EPIDEMIC** | `count > mean + 3·SD` |
| **Measles** | Any single case → ALERT |

> **TODO (docs team):** cite the exact threshold method DOH/PIDSR uses so the formula can be adjusted to match.
