# Nagrik · HackValley 2026

This repository has two parts:

| Folder | What it is |
|---|---|
| [`roads/`](roads/README.md) | **Nagrik Roads**, our HackValley 2026 (PS 02) project: AI road-damage detection for Bengaluru. A phone or dashcam finds potholes and cracks, each defect is counted once, ranked by risk and tracked until it is repaired. |
| this folder | A local copy of the Nagrik website (nagrik.in.net), whose ward, official and tender data Nagrik Roads builds on. |

## Nagrik Roads (quick start)

```powershell
cd roads
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --port 8000
```

Open http://localhost:8000. Full documentation, results and the dashcam set-up are in [`roads/README.md`](roads/README.md);
the training data is described in [`roads/dataset/`](roads/dataset/README.md). Model weights are not in Git (see the README for how to train or download them).

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
