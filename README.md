# Nagrik · HackValley 2026

One application, two ideas:

- **Nagrik**: know who runs your city, what is being built and with how much public money (wards, officials, public works, sources).
- **Nagrik Roads** (HackValley 2026, PS 02): AI road-damage detection for Bengaluru. A phone or dashcam finds potholes and cracks, each defect is counted once, ranked by risk and tracked until it is repaired, and routed to the ward's officials from Nagrik's data.

| Path | What it is |
|---|---|
| `/` (this folder, Node) | Nagrik: home, cities, works, people, sources |
| `/roads/` ([`roads/`](roads/README.md), Python/FastAPI) | Nagrik Roads: road desk, map, live scan, review, reports, repair routes, phone app |

`backend/server.mjs` serves Nagrik and forwards `/roads/*`, the road app's `/api/*` endpoints (all except Nagrik's
`/api/v1/*` and `/api/health`), `/media/*` and `/eval/*` to the Nagrik Roads server, so both run on one address.
`nagrik-roads-link.js` adds the entry points inside Nagrik: a **Roads** link on the Bengaluru card and city nav,
a **Nagrik Roads** header button, and a live road-damage strip on the Bengaluru page.

## Run the whole application

```powershell
# one-time set-up of Nagrik Roads (Python 3.12)
cd roads
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
cd ..
# start both servers and open http://localhost:5501
start.cmd
```

`start.cmd` starts Nagrik Roads on 127.0.0.1:8000 and Nagrik on http://localhost:5501. To scan from a phone on the
same Wi-Fi, run `set HOST=0.0.0.0` before `start.cmd`. Node 20+ is needed for Nagrik.

Model weights are not in Git: put `roadsense_y26n.pt` in `roads/backend/models/` (or train it, see
[`roads/README.md`](roads/README.md)); the training data is described in [`roads/dataset/`](roads/dataset/README.md).

---

# Nagrik website (local copy)

A local recreation of [Nagrik (nagrik.in.net)](https://nagrik.in.net/).

## Features & Parity
- **Exact Pages & Clean URLs**: Homepage (`/`), About (`/about`), Integrations (`/integrations`), plus all 5 cities (Bengaluru, Delhi, Gurugram, Mumbai, Pune) across their Overview, Works, People, and Sources routes. All routes resolve with or without trailing slashes.
- **Assets & Styling**: Pristine Instrument Sans web fonts (`/fonts/`), city landmark silhouettes (`/landmarks/`), high-resolution brand logos, and original CSS styling.
- **Indic Script Support**: Native Kannada (ಕನ್ನಡ), Hindi (हिन्दी), and Marathi (मराठी) text preserved with full UTF-8 fidelity.
- **Theme**: Seamless Light / Dark theme switching persisted across reloads.
- **Client Runtime**: Full React Router client-side hydration with all 53 bundle and route modules.
- **Backend API & Data**: Local snapshot data for Bengaluru, coupled with live upstream Nagrik API proxies for Gurugram, Delhi, Mumbai, and Pune. Includes live weather/AQI telemetry and real-time civic news via Google News RSS.

## Quickstart

Run from `outputs/nagrik-clone`:

```powershell
npm run dev
```

Open [http://127.0.0.1:5501/](http://127.0.0.1:5501/) in your browser.
