# Screen spec: RHU Dashboard

**For:** UI designer · **Page:** `/dashboard/` · **Code:** [`web/dashboard/index.html`](../../web/dashboard/index.html), [`web/js/dashboard/main.js`](../../web/js/dashboard/main.js)
**Status:** working prototype, ready to be redesigned. The *components, data and actions* below are fixed; the *look* is yours to design.

| Desktop | Phone |
|---|---|
| ![Desktop](rhu-dashboard-desktop.png) | ![Phone](rhu-dashboard-phone.png) |

## Purpose and context

- **Who:** RHU Naval staff (Municipal Health Officer, nurses, surveillance officer).
- **Job:** see which illnesses are rising and where, and act on outbreak alerts.
- **Device:** mainly a laptop or desktop at the RHU, but it must stay usable on a phone.
- **Updates:** data refreshes by itself **every 10 seconds**, the same rate as the analytics. There is no refresh button.
- **Order of the screen = order of importance:** what needs action first (alerts), then the details (trend, table).
- **Synthetic data:** all data is synthetic. A small note somewhere on the page would be good for the defense.

## Components (top to bottom)

### 1. Title bar
| | |
|---|---|
| Shows | "Illness overview · Naval, Biliran"; the current week, e.g. `This week 2026-W44 (from Oct 26, so far) · compared with 2026-W43`; and when the data was last analyzed |
| Header | Status pill: **Live** / **Disconnected, retrying…** / **Loading…** |
| Note | **"So far" matters:** the current week isn't over, so its numbers are usually lower than last week. The design must make that clear, so nobody reads it as "cases are dropping". |

### 2. Disease cards (6, one per disease)
| Part | Example | Data |
|---|---|---|
| Disease name | Dengue | `diseases[].name` |
| Big number | 25 | cases **this week so far** (`this_week`) |
| Comparison | last week 26 | `last_week` |
| Sparkline | last 12 weeks | `GET /api/stats/trend?disease=DEN&weeks=12` |
| Badge | "1 barangay on alert" (warning color) or "No alerts" | `barangays_on_alert` |

- **Interaction:** clicking a card **selects that disease** for the trend chart. The selected card is highlighted.
- **States:** selected / not selected; with or without alerts. A value of 0 is normal.
- **Disease list:** Dengue, Influenza-like illness, Acute watery diarrhea, Leptospirosis, Typhoid fever, Measles. Long names must fit or wrap.
- The API also returns `change_pct`, which the prototype doesn't show because "this week so far" makes percentages misleading early in the week.

### 3. Alerts (the RHU's to-do list)
Filter tabs: **Active** (open and acknowledged) · **Resolved** · **All**. Newest first.

Each alert shows:
| Part | Example |
|---|---|
| Level badge | `ALERT` (warning) or `EPIDEMIC` (danger) |
| Title | Dengue · Brgy. Caraycaray · week 2026-W44 |
| Message | "Dengue cases in Brgy. Caraycaray in week 2026-W44: 25, above the epidemic threshold of 11.1." |
| Meta line | Status (Open / Acknowledged by RHU / Resolved) · when it was raised · **sent to N stations, M acknowledged** |
| Actions | **Acknowledge** (only if Open) · **Resolve** · **Reopen** (only if Resolved) · **Show trend** (jumps to the chart for that disease and barangay) |

- **States:** empty ("No active alerts. All barangays are within normal levels."), Open, Acknowledged, Resolved (faded), and a button busy while saving.
- **Volume:** expect roughly 2–6 active alerts at a time. With the synthetic data, about 3 false alarms per week across Naval is normal (see [analytics.md](../analytics.md)).
- **Measles:** alerts on **a single case**, and its message says so.
- **"Sent to N stations":** shows the distributed side of the system: how many BHS nodes received the alert and how many acknowledged it on their screen.

### 4. Trend chart
| | |
|---|---|
| Controls | **Area:** "All of Naval" or one of 26 barangays. **Period:** 12 / 26 / 52 weeks. The disease comes from the selected card. |
| Lines | **Cases per week** (solid); with one barangay selected, also **alert threshold** (dashed, warning color) and **epidemic threshold** (dashed, danger color) |
| Axis | Week labels (first, middle, last), with the last one marked "(so far)"; the y-axis maximum |
| Note under the chart | Explains the thresholds, or asks the user to choose a barangay to see them |
| Rule | Thresholds exist only for **one barangay + one disease**. For "All of Naval" they're hidden on purpose, because adding thresholds across barangays isn't statistically valid. |
| Opened from | the disease cards, the "Show trend" button on an alert, or clicking a row in the barangay table |

Charts are plain SVG with no chart library (the demo may run without internet). Keep designs within what simple lines, areas or bars can do.

### 5. Barangay table (heatmap)
| | |
|---|---|
| Toggle | **This week** (so far) / **Last week** |
| Rows | All 26 barangays, sorted by **cases per 10,000 people** (highest first) |
| Columns | Barangay · one column per disease · Total · Per 10,000 |
| Cell colors | Normal (plain; zero is muted) · **ALERT** (warning) · **EPIDEMIC** (danger), using the same rule as the alerts |
| Hover on a cell | The thresholds, e.g. "Alert above 4.2, epidemic above 6.0" |
| Click a row | Shows that barangay's trend; the selected row is outlined |
| Phone | The table scrolls sideways **inside its card**. A card-per-barangay layout could work better. |

"Per 10,000" lets small and large barangays be compared fairly: Caraycaray has about 6,800 people, Libtong about 1,550.

## Color meanings (same as the other screens)
| Meaning | Used for |
|---|---|
| Accent (brand) | selected card, cases line, primary buttons |
| Warning | ALERT badge, cells and border; alert threshold line |
| Danger | EPIDEMIC badge, cells and border; epidemic threshold line; Disconnected |
| OK | Live pill |

Light and dark mode are both supported (it follows the device). The screenshots are dark mode.

## Data sources
| Component | Endpoint |
|---|---|
| Title, disease cards | `GET /api/stats/summary`, `GET /api/stats/trend` (sparklines) |
| Alerts | `GET /api/alerts`, `POST /api/alerts/{id}/status` |
| Trend chart | `GET /api/stats/trend?disease=…&barangay_id=…&weeks=…` |
| Barangay table | `GET /api/stats/barangays?week=2026-W44` |

Full field lists: [protocol.md → Statistics](../protocol.md#statistics-rhu-dashboard-and-bhs-nodes).

## Known prototype limits (for the redesign)
- The page redraws every 10 s, so keyboard focus on a card or button can be lost during a refresh.
- No login: anyone on the network can acknowledge or resolve alerts. That's fine for the class demo but would need RHU accounts in real use.
