# Groundwork ATX

**Live permits, entitlements & council intelligence for Austin commercial real estate.**

A zero-backend dashboard for CRE brokers: every number is fetched live in your
browser from the [City of Austin Open Data Portal](https://data.austintexas.gov)
(Socrata/SODA APIs). No server, no build step, no API keys.

## What it shows

| Tab | What a broker gets |
|---|---|
| **Overview** | KPI tiles (commercial permit count & valuation, ground-up buildings, CofOs, zoning cases, flagged council items), weekly permit-velocity chart, commercial-vs-residential trend, valuation by permit type, and the biggest projects of the last 30 days |
| **Map explorer** | A Leaflet map of Austin with four live layers — commercial building permits, trade permits (electrical / plumbing / mechanical), zoning cases, and certificates of occupancy. **Click anywhere** to drop a ¼ / ½ / 1-mile survey circle and get an instant area report of everything inside it |
| **Permit pipeline** | Searchable, filterable feed of issued permits (window, class, type, council district, free-text) with valuations, status, and deep links to the city record |
| **Zoning cases** | Recent zoning / rezoning cases — the earliest public signal that land use is changing — with full case details |
| **Council watch** | City Council agenda items filtered through a **CRE lens** (zoning, Land Development Code, site plans & PUDs, density bonus, ETOD, permitting, fees, historic/signage, downtown, parking), grouped by meeting date with upcoming meetings flagged — plus links to every commission where land-use policy is debated (Planning Commission, ZAP, Board of Adjustment, Historic Landmark, Design Commission, and the Council Meeting Information Center calendar) |

The Overview also carries a "dirt to doors-open" strip mapping the full
entitlement journey — zoning/use approval → site plan & fire review → building
permit → trade permits → signage & finals → certificate of occupancy — to the
panel that tracks each stage.

## Data sources (all official City of Austin feeds)

- [Issued Construction Permits](https://data.austintexas.gov/Building-and-Development/Issued-Construction-Permits/3syk-w9eu) (`3syk-w9eu`) — building, electrical, plumbing, mechanical, driveway/sidewalk permits with valuations and coordinates
- [Zoning Cases](https://data.austintexas.gov/Building-and-Development/Zoning-Cases/edir-dcnf) (`edir-dcnf`)
- [Certificates of Occupancy](https://data.austintexas.gov/Building-and-Development/Certificates-Of-Occupancy/f9mz-m6dy) (`f9mz-m6dy`)
- [City of Austin Council Agenda Items, Feb 2024–present](https://data.austintexas.gov/City-Government/City-of-Austin-Council-Agenda-Items-Updates-Februa/sich-49ay) (`sich-49ay`)

Basemap tiles: © OpenStreetMap contributors, © CARTO. Map library: Leaflet (CDN).

## Run it

Any static file server works:

```bash
cd ERG
python3 -m http.server 8080     # then open http://localhost:8080
```

Opening `index.html` directly from disk also works in most browsers (the city
APIs allow cross-origin requests), but a local server is the sure path.

### Deploy to GitHub Pages

Settings → Pages → deploy from branch → select this branch, root folder. Done —
it's a fully static site.

## Data freshness: live + nightly snapshot

The dashboard is **live-first**: every panel queries the city APIs directly from
your browser, and the header's **Refresh** button clears the cache and refetches
on demand.

Behind that sits an automatic nightly tier. A GitHub Action
(`.github/workflows/refresh-data.yml`) runs at **12:00 am Austin time** (05:00
UTC; it can also be triggered manually from the repo's Actions tab), executes
`scripts/fetch-snapshots.mjs`, and commits fresh JSON extracts of all four
datasets to `data/`. Because GitHub Pages redeploys on every commit, the
published site always ships with data at most one night old — and whenever a
live API call fails (portal outage, rate limiting, a locked-down network), the
app silently falls back to the snapshot and the header pulse turns amber with
the snapshot's timestamp.

The header therefore always tells you what you're looking at:

- 🟢 `Live · fetched 9:14 AM` — everything came from the city API just now
- 🟠 `Nightly snapshot · refreshed Aug 11, 12:02 AM` — the API was unreachable;
  you're on last night's data

The first snapshot appears after the workflow's first run (trigger it once from
the Actions tab after enabling the repo's Actions).

## Design & architecture notes

- **Schema-drift resilient**: the data layer (`js/soda.js`) resolves real column
  names at runtime from each dataset's metadata against candidate lists in
  `js/config.js`. If the city renames `issued_date`, the dashboard adapts; if a
  feed disappears, only that card degrades, with a link to the dataset page.
- **Session caching**: responses cache in `sessionStorage` for 10 minutes; the
  header's **Refresh** clears the cache.
- **Charts** are hand-rolled SVG following a validated, colorblind-safe palette;
  every chart ships a "Data table" twin so no value is hover-gated.
- **Area reports** are computed client-side (haversine over already-loaded
  layers) — clicking the map costs zero extra API calls.
- To raise API rate limits, put a free [Socrata app token](https://dev.socrata.com/docs/app-tokens.html)
  in `GW_CONFIG.appToken` (`js/config.js`).
- The CRE keyword lens and the commission link list are plain data in
  `js/config.js` — extend them as the policy landscape shifts.

## Caveats

Unofficial market-research tool. Feeds update on the city's schedule (typically
daily for permits). Verify any permit, case, or agenda status in the
[AB+C portal](https://abc.austintexas.gov/) and the
[Council Meeting Information Center](https://www.austintexas.gov/department/city-council/council/council_meeting_info_center.htm)
before relying on it in a transaction.
