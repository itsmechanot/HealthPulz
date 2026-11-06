# Screen spec: Login

**For:** UI designer · **Page:** `/` · **Code:** [`web/index.html`](../../web/index.html), [`web/js/login/main.js`](../../web/js/login/main.js)
**Status:** working page, ready to be redesigned. The *fields and states* below are fixed; the *look* is yours.

## Purpose
The way into the system. Each BHU has one shared login and the RHU has one; each goes straight to its own side.

- **Where it's seen:** mostly on phones in a BHU, and on classmates' phones during the demo, so it must work one-handed at phone width.
- **There is no sign-up or "Forgot password" flow:** only the RHU gives out passwords ([BHU Accounts](rhu-dashboard.md#screen-6--bhu-accounts)), so the page just says to ask the RHU. A BHU that has no password yet sees the same "wrong" message.

## Components
| Component | Details |
|---|---|
| Brand | Logo, name, one line saying what the system is |
| Health unit | Searchable dropdown. First option is **RHU Naval**, then the 26 barangays A–Z. Accents are ignored ("capinahan" finds *Capiñahan*) |
| Password | With a **Show / Hide** button. Passwords look like `k7qx-m2pd` |
| Log in | Primary button. Disabled until the page has loaded, and while checking |
| Error line | One line under the button |
| Privacy note | Client records stay with their health unit; the RHU gets de-identified reports |

## Messages
| When | Text |
|---|---|
| No health unit chosen | Choose your health unit from the list. |
| No password | Enter your password. |
| Wrong login | Wrong health unit or password. |
| 5 wrong tries on this device | Too many wrong passwords. Try again in N seconds. |
| Server unreachable | Cannot reach the RHU server. Check the Wi-Fi and try again. |

## Where it leads
| Account | Goes to |
|---|---|
| A barangay | `/node/` |
| RHU Naval | `/dashboard/` (and `/monitor/` from its sidebar) |

Someone already logged in who opens `/` goes straight to their side. A login lasts 12 hours, then the next action brings them back here.
