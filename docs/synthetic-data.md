# HealthPulz — Synthetic Data Rules

**Status:** Draft v1 · living document, update as development goes on
**Municipality:** Naval, Biliran (RHU Naval + 26 Barangay Health Stations)

> ⚠️ **Disclaimer:** All case data and populations are **synthetic**. The rates below are illustrative values for simulation, not real epidemiology. The docs team should cite DOH/PIDSR sources for the general patterns used (e.g., dengue peaking in the rainy season) and state clearly in the paper that the data is synthetic.

---

## 1. Barangays

The 26 barangays of Naval and their **PSGC codes** come from the official PSA boundary data (PSGC 3Q 2026), the same data the map is drawn from. **Neighbors** are computed from which barangay boundaries touch, by [`tools/build_map.py`](../tools/build_map.py). **Populations are synthetic.**

> **Correction:** an earlier draft listed "Higatangan" as a barangay. Higatangan is an island of Naval (home of Libertad and Mabini), not a barangay; the official list has **Lico** instead, which now uses ID `BRGY-11`.
>
> **Optional (docs team):** replace the synthetic populations with PSA 2020 census figures.

| ID | PSGC | Barangay | Population (synthetic) | Neighbors (touching boundaries) |
|---|---|---|---|---|
| BRGY-01 | 807808001 | Agpangi | 2,100 | Atipolo, Larrazabal, Sabang, Talustusan |
| BRGY-02 | 807808002 | Anislagan | 1,650 | Borac, Haguikhikan, San Pablo, Villa Caneja |
| BRGY-03 | 807808003 | Atipolo | 2,400 | Agpangi, Calumpang, Larrazabal, Padre Inocentes Garcia (Pob.), Santissimo Rosario Pob. |
| BRGY-04 | 807808021 | Borac | 1,800 | Anislagan, Catmon, San Pablo |
| BRGY-05 | 807808022 | Cabungaan | 1,700 | Calumpang, Capiñahan, Caraycaray, Lucsoon, San Pablo, Villa Caneja |
| BRGY-06 | 807808004 | Calumpang | 2,600 | Atipolo, Cabungaan, Caraycaray, Larrazabal, Lucsoon, Padre Inocentes Garcia (Pob.), Padre Sergio Eamiguel |
| BRGY-07 | 807808005 | Capiñahan | 2,300 | Cabungaan, Imelda, Lucsoon, Villa Caneja, Villa Consuelo |
| BRGY-08 | 807808006 | Caraycaray | 6,800 | Cabungaan, Calumpang, Padre Inocentes Garcia (Pob.), San Pablo, Santo Niño |
| BRGY-09 | 807808007 | Catmon | 1,900 | Borac, Haguikhikan, San Pablo |
| BRGY-10 | 807808008 | Haguikhikan | 1,600 | Anislagan, Catmon, Libtong, Villa Caneja |
| BRGY-11 | 807808012 | Lico | 2,200 | Imelda, Lucsoon, Padre Sergio Eamiguel, Talustusan |
| BRGY-12 | 807808023 | Imelda | 2,000 | Capiñahan, Lico, Lucsoon, Talustusan |
| BRGY-13 | 807808024 | Larrazabal | 4,100 | Agpangi, Atipolo, Calumpang, Talustusan |
| BRGY-14 | 807808010 | Libertad | 1,750 | Mabini |
| BRGY-15 | 807808025 | Libtong | 1,550 | Haguikhikan, Villa Caneja, Villa Consuelo |
| BRGY-16 | 807808013 | Lucsoon | 1,850 | Cabungaan, Calumpang, Capiñahan, Lico, Imelda, Padre Sergio Eamiguel |
| BRGY-17 | 807808014 | Mabini | 2,050 | Libertad |
| BRGY-18 | 807808009 | Padre Inocentes Garcia (Pob.) | 5,200 | Atipolo, Calumpang, Caraycaray, Santissimo Rosario Pob., Santo Niño |
| BRGY-19 | 807808026 | Padre Sergio Eamiguel | 3,900 | Calumpang, Lico, Lucsoon, Talustusan |
| BRGY-20 | 807808027 | Sabang | 3,100 | Agpangi, Talustusan |
| BRGY-21 | 807808015 | San Pablo | 2,250 | Anislagan, Borac, Cabungaan, Caraycaray, Catmon, Villa Caneja |
| BRGY-22 | 807808017 | Santissimo Rosario Pob. | 4,800 | Atipolo, Padre Inocentes Garcia (Pob.), Santo Niño |
| BRGY-23 | 807808016 | Santo Niño | 2,700 | Caraycaray, Padre Inocentes Garcia (Pob.), Santissimo Rosario Pob. |
| BRGY-24 | 807808018 | Talustusan | 1,950 | Agpangi, Lico, Imelda, Larrazabal, Padre Sergio Eamiguel, Sabang |
| BRGY-25 | 807808019 | Villa Caneja | 1,600 | Anislagan, Cabungaan, Capiñahan, Haguikhikan, Libtong, San Pablo, Villa Consuelo |
| BRGY-26 | 807808020 | Villa Consuelo | 3,300 | Capiñahan, Libtong, Villa Caneja |

The source of truth is [`rhu/data/barangays.json`](../rhu/data/barangays.json). Libertad and Mabini only border each other because they are on Higatangan island. A barangay's alerts also go to its neighbors' stations.

### Stations per barangay
Each barangay has **one station** (`BRGY-08-MAIN`). There are 30–40 classmates and only 26 barangays, so several devices can share a station, and the System Monitor shows how many are connected. See [protocol.md](protocol.md#1-bhu-accounts-and-stations).

---

## 2. Diseases

The lists come from **RHU Naval**, collected by the team in November 2026: its 10 common illnesses and the diseases it flags on a single case. Dengue, leptospirosis and typhoid aren't on those lists; the team kept them as extra monitored diseases. The source of truth is [`rhu/data/diseases.json`](../rhu/data/diseases.json).

> ⚠️ **The rates are estimates**, not RHU figures. They only shape the synthetic history. Replace `base_rate` with real numbers if the RHU provides them, then re-run `python -m rhu.history`.

### Common illnesses (the RHU's top 10, in its order)

| Code | Disease | Estimated rate (cases/week per 10k) | Peak months | Alerts |
|---|---|---|---|---|
| `ARI` | Acute respiratory tract infection | 8.0 | Jan, Feb, Jul, Aug, Sep, Dec (×2) | Above threshold |
| `SKN` | Skin problem, all types | 3.0 | Jun, Jul, Aug, Sep (×2) | Above threshold |
| `PNE` | Pneumonia | 2.0 | Jan, Feb, Jul, Aug, Sep, Dec (×2) | Above threshold |
| `WND` | Wound, all types | 2.5 | none | None: not contagious, counted for trends |
| `KID` | Kidney diseases | 0.6 | none | None: not contagious, counted for trends |
| `HTN` | Hypertension | 5.0 | none | None: not contagious, counted for trends |
| `ART` | Arthritis, all forms | 1.5 | none | None: not contagious, counted for trends |
| `TB` | Tuberculosis, all forms | 0.8 | none | Above threshold |
| `FEV` | Fever, unknown cause | 3.0 | Jun, Jul, Aug, Sep (×2) | Above threshold |
| `DIA` | Diarrhea | 2.5 | Jun, Jul, Aug, Sep (×2) | Above threshold |

### Urgent: a single case raises an alert

| Code | Disease | Estimated rate (cases/week per 10k) | Peak months | Alerts |
|---|---|---|---|---|
| `AFP` | Acute flaccid paralysis | 0.002 | none | **URGENT** on any case |
| `DIP` | Diphtheria | 0.005 | none | **URGENT** on any case |
| `MEA` | Measles | 0.05 | Jan, Feb, Mar, Apr (×2) | **URGENT** on any case |
| `NTT` | Neonatal tetanus | 0.002 | none | **URGENT** on any case |
| `TET` | Non-neonatal tetanus | 0.005 | none | **URGENT** on any case |
| `PER` | Pertussis | 0.01 | none | **URGENT** on any case |

### Also monitored

| Code | Disease | Estimated rate (cases/week per 10k) | Peak months | Alerts |
|---|---|---|---|---|
| `DEN` | Dengue | 1.5 | Jun, Jul, Aug, Sep, Oct (×3) | Above threshold |
| `LEP` | Leptospirosis | 0.3 | Jul, Aug, Sep, Oct (×4) | Above threshold |
| `TYP` | Typhoid fever | 0.4 | Jun, Jul, Aug, Sep (×1.5) | Above threshold |

### Other consultations (`OTH`)

`rhu/data/diseases.json` also has an entry with `"tracked": false` and a rate of 0. It exists for real checkups whose diagnosis isn't on the lists above. The generators never produce it, so the history is unaffected.

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

### Running the history backfill

```powershell
python -m rhu.history                # 3 past years + this year up to last week (seed 42)
python -m rhu.history --scale 16     # ~900,000 cases, for the speedup benchmark
```

- History covers ISO week 1 of three years ago up to **last week**. The current week is left for live data from the nodes.
- It is stored under one `HISTORY` node per barangay (e.g. `BRGY-08-HIST`). These nodes are hidden from the System Monitor.
- Running it again **replaces** the previous history. Live data sent by nodes is never touched.
- The command prints the injected outbreaks, which the docs team can use as the "expected answers" when checking that alerts fire correctly.
- Reference run (seed 42, scale 1): 200 weeks, ~57,000 cases, 16 injected outbreaks.

---

## 6. Alert levels

Computed by the analytics. For each barangay × disease × week, thresholds compare with the **same ISO week ±1 in up to 5 previous years** (see [analytics.md](analytics.md)):

| Level | Condition | Applies to |
|---|---|---|
| **URGENT** | Any single case | The 6 urgent diseases |
| **EPIDEMIC** | `count > mean + 3·SD` and `count ≥ 3` | Contagious common illnesses (ARI, skin, pneumonia, TB, unknown fever, diarrhea) and dengue, leptospirosis, typhoid |
| **ALERT** | `count > mean + 2·SD` and `count ≥ 3` | Same as EPIDEMIC |
| None | Counted for trends only | Hypertension, arthritis, kidney diseases, wounds, Other consultations |

> **TODO (docs team):** cite the exact threshold method DOH/PIDSR uses so the formula can be adjusted to match.
