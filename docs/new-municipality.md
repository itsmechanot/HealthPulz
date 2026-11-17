# Setting up HealthPulz for another municipality

HealthPulz has no municipality built into its code. Everything specific to Naval is in **four data files**, so moving the system to another town is configuration, not programming. Each municipality runs **its own RHU server**, which serves its own BHUs.

| File | What it holds |
|---|---|
| `rhu/data/municipality.json` | The town's name, province, PSGC code, and the RHU's name shown on every page |
| `rhu/data/barangays.json` | Each barangay: ID, PSGC code, name, population, neighbors |
| `web/data/map.json` | The offline map, built from official boundaries (step 3) |
| `rhu/data/diseases.json` | The disease lists and alert rules (step 5, optional) |

Tested: Caibiran, Biliran (17 barangays) ran with only these files changed, from login to the map and the BHU pages, and no page mentioned Naval.

## Steps

### 1. Get the official boundaries
The map uses the Philippine Statistics Authority's PSGC boundaries, published as open data by [faeldon/philippines-json-maps](https://github.com/faeldon/philippines-json-maps) (MIT license). Download two files, replacing the codes with your province's and municipality's PSGC codes:

```text
Province (all its towns):
https://raw.githubusercontent.com/faeldon/philippines-json-maps/master/latest/geojson/provdists/medres/municities-provdist-<PROVINCE PSGC>.0.01.json

Municipality (its barangays):
https://raw.githubusercontent.com/faeldon/philippines-json-maps/master/latest/geojson/municities/medres/bgysubmuns-municity-<MUNICIPALITY PSGC>.0.01.json
```
Example for Caibiran, Biliran: province `807800000`, municipality `807804000`.

### 2. Name the municipality
Edit `rhu/data/municipality.json`:
```json
{
  "name": "Caibiran",
  "province": "Biliran",
  "psgc": 807804000,
  "rhu_name": "RHU Caibiran"
}
```

### 3. Build the map and the barangay list
```powershell
python tools/build_map.py --province biliran.json --municipality caibiran.json --new
```
`--new` makes the barangay list from the boundaries: IDs `BRGY-01`, `BRGY-02`, … in name order, and each barangay's neighbors from which boundaries touch (neighbors also receive a barangay's outbreak alerts). The tool reads the PSGC code from `municipality.json`; pass `--psgc` to use another. A wrong code is refused with a list of the province's towns and their codes.

### 4. Enter the real populations
`--new` sets every population to 1000. Replace them in `rhu/data/barangays.json` with census figures (PSA), because the **cases per 10,000 people** on the map and tables use them.

### 5. Disease lists (optional)
If the RHU's common and urgent diseases differ, edit `rhu/data/diseases.json`. Each disease has a `group`, whether it is `tracked`, and whether it raises `threshold_alerts` or a `single_case_alert`.

### 6. Start with a fresh database
The `var/` folder holds the previous municipality's data and passwords. Move it aside, then:
```powershell
python -m rhu.history      # optional: synthetic past years, so outbreak thresholds have a baseline
python -m rhu              # creates the database and the accounts
python -m rhu.accounts     # shows the RHU password
```
Log in as the RHU and give each BHU its password on **BHU Accounts**.

## Adding one barangay to an existing municipality
1. Add an entry to `rhu/data/barangays.json` with the **next free ID** (never reuse or renumber an ID: past cases point to it), its PSGC code, name and population.
2. Run `python tools/build_map.py --province … --municipality …` **without** `--new`. It keeps every ID, draws the new boundary and recalculates neighbors. The barangay must be in the official boundary file.
3. Restart the server. The barangay appears in the login list, the dashboard and the analytics, and gets a BHU account waiting for its password.

## Limits
- At most 99 barangays per municipality (IDs `BRGY-01` to `BRGY-99`).
- Barangays are added by editing files and restarting; there is no admin screen for it yet (future work).
