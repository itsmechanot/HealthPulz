# Screen spec: RHU Dashboard

**For:** UI designer · **Page:** `/dashboard/` · **Code:** [`web/dashboard/index.html`](../../web/dashboard/index.html), [`web/js/dashboard/main.js`](../../web/js/dashboard/main.js), map: [`web/js/common/naval-map.js`](../../web/js/common/naval-map.js)
**Status:** working app, ready to be refined. The *screens, components, data and actions* below are fixed; the *look* is yours to improve.

| Overview | Map |
|---|---|
| ![Overview](rhu-dashboard-desktop.png) | ![Map](rhu-dashboard-map.png) |

| Alerts | Phone | Phone menu |
|---|---|---|
| ![Alerts](rhu-dashboard-alerts.png) | ![Phone](rhu-dashboard-phone.png) | ![Phone menu](rhu-dashboard-phone-menu.png) |

## Users and context
- **Who:** RHU Naval staff (Municipal Health Officer, nurses, surveillance officer).
- **Job:** see where and which illnesses are rising, and act on outbreak alerts.
- **Device:** laptop or desktop at the RHU; must still work on a phone.
- **Live data:** refreshes by itself **every 10 s** (the analytics interval). The number of stations online updates every second.
- **"So far":** the current week isn't over, so its numbers are usually lower than last week's. This must always be clear; the top bar says "(so far)".

## Layout (app shell)
| Part | Content |
|---|---|
| **Sidebar** | Logo + "RHU Naval"; *Surveillance*: **Overview, Map, Alerts** (red count of open alerts), **Trends, Barangays**; *System*: System Monitor (no links to the BHU's page); footer "All data is synthetic". On phones it slides in from a ☰ button over a dimmed page. |
| **Top bar** | Screen title + one-line description · **week chip** ("Week 2026-W44 · from Oct 26 (so far)") · live pill (**Live** / **Reconnecting…**) · user chip "RHU Staff" |
| **Screens** | One at a time. The URL remembers it (`/dashboard/#/map`), so reload and the Back button work. |

## Screen 1 · Overview (default)
1. **4 KPI cards:**
   - **Cases this week (so far)**, with last week under it
   - **Open outbreak alerts** (how many at epidemic level, how many not yet acknowledged)
   - **Barangays with cases** (`5 / 26`, how many over a threshold)
   - **BHS stations online** (registered stations, barangays covered)
2. **Map card**: small map of Naval for this week, all diseases. Clicking a barangay opens it on the Map screen. Link: "Open map →".
3. **Latest alerts**: the 4 newest active alerts, compact (level, disease, barangay, week, cases vs threshold), with **Acknowledge / Resolve / Show trend**. Link: "View all →".
4. **Disease cards** (6): this week so far, last week, 12-week sparkline, "N barangays on alert". Clicking one opens Trends for that disease.

## Screen 2 · Map
The **municipality of Naval is the focus**:
- **Naval highlighted:** its boundary is drawn as a thick accent-colored outline, its 26 barangays in a light land color, and the **other towns of Biliran greyed out** around it. The sea is blue, with a "Municipality of Naval" badge.
- **Barangays are flagged by color:**
  - no cases: plain land
  - cases: green, darker = more cases per 10,000 people
  - **ALERT:** warning color
  - **EPIDEMIC:** danger color
- **Markers:** a circle on each barangay with cases; size = number of cases; EPIDEMIC markers pulse.
- **Labels:** barangay names (smaller in the tight poblacion area; hidden on phones, where you tap instead).
- **Filters:** disease chips (**All diseases**, Dengue, ILI, …) and **This week / Last week**.
- **Hover:** tooltip with name, cases, rate per 10,000, level.
- **Click a barangay:** fills the **Barangay details** panel:
  - avatar, name, week, level badge
  - cases, per 10,000, population
  - per-disease rows with level badges
  - **neighbors** (they also receive its alerts)
  - **Open trend** button
- **Offline:** the map is drawn from official PSA boundary data stored in the project (no map tiles), so it works without internet. The attribution line under the map must stay.

## Screen 3 · Alerts
Filter: **Active** (open + acknowledged) · **Resolved** · **All**. Newest first. Each alert:
- **Level badge and heading:** EPIDEMIC or ALERT; disease · barangay · week.
- **Message:** e.g. "Dengue cases in Brgy. Cabungaan in week 2026-W44: 9, above the epidemic threshold of 3.2."
- **Status line:** Open / Acknowledged by RHU / Resolved · when it was raised · **sent to N stations, M acknowledged** (the distributed delivery, made visible).
- **Corrected note:** if BHU corrections lowered the count after the alert was raised, a note shows the new count and asks staff to review it. The alert isn't closed automatically.
- **Actions:**
  - **Acknowledge**: only when Open
  - **Resolve**
  - **Reopen**: only when Resolved
  - **Show trend**

Expect around 4–12 active alerts. About 3 per week are false alarms from the synthetic data (see [analytics.md](../analytics.md)). Measles alerts on **one** case.

## Screen 4 · Trends
- **Disease chips:** pick the disease to chart.
- **Area:** All of Naval, or one barangay.
- **Period:** 12, 26 or 52 weeks.
- **Line chart:**
  - cases per week (solid)
  - with one barangay selected, also the **alert threshold** and **epidemic threshold** (dashed)
  - thresholds are hidden for "All of Naval" on purpose (adding them across barangays isn't statistically valid)
- **Axis:** first, middle and last week, the last marked "(so far)".
- **Note under the chart:** explains the thresholds.

## Screen 5 · Barangays
Heatmap table: all 26 barangays × 6 diseases + Total + **Per 10,000**, sorted by rate.
- **Toggle:** This week / Last week.
- **Cell colors:** ALERT and EPIDEMIC cells are colored; zeros are muted.
- **Hover a cell:** shows its thresholds.
- **Click a row:** opens the barangay on the Map.
- **Phones:** the table scrolls sideways inside its card.

## Data sources
| Component | Endpoint |
|---|---|
| Top bar, KPIs, disease cards | `GET /api/stats/summary`, `GET /api/stats/trend` (sparklines), `WS /ws/monitor` (stations online) |
| Map, Barangays | `GET /api/stats/barangays?week=…` (this week and last week) |
| Alerts | `GET /api/alerts`, `POST /api/alerts/{id}/status` |
| Trends | `GET /api/stats/trend?disease=…&barangay_id=…&weeks=…` |
| Map shapes | `/data/naval-map.json` (built by [`tools/build_map.py`](../../tools/build_map.py)) |

## Known limits
- **No login:** anyone on the network can acknowledge or resolve alerts. That's fine for the class demo; real use would need RHU accounts.
- **Synthetic populations:** they affect the "per 10,000" rates.
