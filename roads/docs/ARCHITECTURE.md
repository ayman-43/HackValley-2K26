# Nagrik Roads – architecture

AI road-damage detection for Bengaluru (HackValley 2K26, PS 02). A phone, dashcam or uploaded photo goes in;
one record per physical pothole or crack comes out, with a location, severity, risk score, the responsible
ward and officer, and a repair history. Papers and tools behind each part: [`REFERENCES.md`](REFERENCES.md).

## Overview

```
 phone / dashcam / photo                     website + installable phone app (frontend/, no build step)
          │                                                   ▲
          ▼                                                   │ JSON
  POST /api/inspections ──► job queue ──► pipeline.process_inspection
  POST /api/live/{id}/frame (live scan, ~3 fps)        │
                                                       ▼
   detector   YOLO26s per frame ─► FrameLinker (one track per physical defect)
   severity   damage type + visible size (+ optional Depth Anything V2 index)
   privacy    blur faces and number plates before anything is stored
   geo        EXIF / uploaded GPS track / GPS embedded in the video (dashcam) ─► lat, lon per defect
   dedup      GPS radius + perceptual hashes + class ─► existing defect or a new one; trend; repaired?
   risk       road class, speed, schools/hospitals (OSM), rain (Open-Meteo), repeats ─► score 0-100 + reasons
   nagrik     ward, corporation, officials, recent road tenders (Nagrik public API, cached)
                                                       │
                                                       ▼
                                            SQLite: inspections · defects · observations · users
```

Everything runs on a CPU. Network services (Overpass, Open-Meteo, OSRM, Nagrik) are optional: every call has a
timeout, is cached under `backend/data/cache/`, and falls back to defaults so the app keeps working offline.

## Backend modules (`backend/app/`)

| Module | Responsibility |
|---|---|
| `main.py` | FastAPI app, upload endpoint, background job queue, stats/metrics/training endpoints, serves `frontend/` under `/roads/` |
| `pipeline.py` | One inspection end to end: detect → severity → privacy → location → dedup → save → risk/forecast → civic data → absence check |
| `detector.py` | Model loading, per-image detection, video tracking (`FrameLinker`), drawing. Optional crack model combined with the pothole model |
| `severity.py` | Severity score and label from damage type, box size and optional depth index |
| `privacy.py` | Face and number-plate blurring with a COCO-trained detector |
| `geo.py`, `dashcam.py` | EXIF GPS, GPS track files (GPX/CSV/JSON), interpolation; GPS streams embedded in dashcam videos (via ExifTool) |
| `dedup.py` | Image fingerprints, matching a sighting to an existing defect, trend (worsening/improving), absence → possibly repaired, failed repairs within 90 days |
| `risk.py` | Road context, weather, transparent additive risk score with per-factor points, "days until high risk" forecast |
| `nagrik.py` | Ward and officials lookup, road tenders for the ward |
| `review.py` | Officer review queue (confirm / reject / correct box and class), workflow, corrections export for retraining |
| `reports.py` | Printable complaint per defect, officer digest, CSV export |
| `crew.py` | Repair route over the top defects (OSRM), "repaired by" crew ID |
| `roads.py` | Road health: whole road segments coloured by condition |
| `live.py` | Live scan sessions: frames in, boxes back to the phone, confirmed defects saved at the GPS fix |
| `auth.py`, `auth_store.py` | Email sign-up/sign-in, cookie sessions, roles, server-side access rules and rate limits |
| `db.py` | SQLite schema and helpers |

## Data model (SQLite, `backend/app/db.py`)

- **inspections**: one upload or live session (source type, file, location, raw and unique detection counts).
- **defects**: one *physical* defect, with code `PH-0001` / `CR-0001`, class, location, severity, risk score and factors,
  trend, forecast, report count, workflow (`reported → verified → scheduled → repaired → closed`, or `rejected`),
  ward, corporation and officials, failed-repair flag.
- **observations**: every sighting of a defect: box, confidence, severity, frame and crop images, location, track ID,
  frames seen, why it was matched to this defect, absent flag, review status.
- **users / sessions** (`auth_store.py`): accounts with roles `citizen`, `crew`, `officer`, `admin`.

## API (all JSON unless noted)

| Area | Endpoints |
|---|---|
| Inspections | `POST /api/inspections` (file, optional `lat`, `lon`, `gps_track`, `road_type`, `notes`) → `{inspection_id, job_id}` · `GET /api/jobs/{id}` (progress, result) · `GET /api/inspections[/{id}]` |
| Defects | `GET /api/defects?workflow=&damage_class=&min_risk=` (sorted by risk) · `GET /api/defects/{id}` (with observation timeline) · `GET /api/stats` · `GET /api/wards/geojson` |
| Live scan | `POST /api/live/start` · `POST /api/live/{id}/frame` · `POST /api/live/{id}/stop` · `GET /api/live/feed` |
| Review and workflow | `GET /api/review/queue` · `POST /api/observations/{id}/review` · `PATCH /api/defects/{id}/workflow` · `POST /api/defects/{id}/repair` · `GET /api/export/corrections` (zip, YOLO format) |
| Reports | `GET /api/defects/{id}/complaint` (HTML) · `GET /api/digest` (HTML) · `GET /api/report/summary.csv` |
| Crews and roads | `POST /api/crew/route` · `POST /api/defects/{id}/repaired_by` · `GET /api/roads/health` |
| Model | `GET /api/metrics` · `GET /api/training` · `GET /api/health` |
| Accounts | `POST /api/auth/signup`, `/login`, `/logout` · `GET /api/auth/me`, `/config` · `GET/POST/PATCH /api/auth/users` (admin) |

Access rules are enforced on the server (`auth.RULES`): reading is public, reviewing and workflow changes need an
officer or admin, repair actions need a crew member or above, and account management needs an admin.

## Frontend (`frontend/`, plain HTML/CSS/JS)

| Page | Purpose |
|---|---|
| `home.html` (`/roads/`) | Website and installable phone app: report a photo or video, "near you" map, how it works |
| `index.html` + `app.js` (`/roads/dashboard`) | Dashboard: map, defects, defect detail, upload, review, reports, metrics |
| `live.html`, `livemap.html` | Live scan from the phone camera, and the live map of what it finds |
| `capture.html` | Record a ride with GPS track and accelerometer bumps, or take a photo |
| `route.html`, `roads.html` | Repair route planner, road health map |
| `training.html` | How the model was trained (curves, metrics) |
| `login.html`, `users.html`, `gate.js` | Sign-in, account management, per-role page access |

Maps use MapLibre GL with OpenFreeMap tiles and Nagrik ward boundaries (`rsmap.js`). `manifest.webmanifest` and `sw.js`
make the site installable as a phone app.

## Detector

- Model: `backend/models/roadsense_y26s.pt`, YOLO26s trained by us on RDD2022 (India, US), BharatPotHole and Kaggle
  potholes (17,909 train / 3,653 val images, 4 classes). Val mAP50 0.686. Details: [`results/RESULTS.md`](results/RESULTS.md).
- Classes: 0 longitudinal_crack, 1 transverse_crack, 2 alligator_crack, 3 pothole.
- Uploads add cracks from an optional RDD2022 YOLOv8s crack model (`ROADSENSE_COMBINE`); live scan uses one model for speed.
- Counting: `FrameLinker` links detections across frames by distance, tolerating missed frames and fast motion. A defect
  counts when seen in 2+ analysed frames or once above 0.5 confidence. It replaced ByteTrack, which dropped most
  potholes on real bike rides (see `REFERENCES.md` [12]).

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `ROADSENSE_MODEL` | `roadsense_y26s.pt` → `roadsense_y26n.pt` → `rdd_yolov8s.pt` | Detector weights |
| `ROADSENSE_DEVICE` | `cpu` | `0` for the first GPU |
| `ROADSENSE_CONF` | `0.20` | Detection confidence (tuned with `scripts/tune_detector.py`) |
| `ROADSENSE_COMBINE`, `ROADSENSE_CRACK_MODEL`, `ROADSENSE_CRACK_CONF` | `1`, `rdd_yolov8s.pt`, `0.35` | Optional crack model for uploads |
| `ROADSENSE_SOLO_CONF` | `0.5` | A defect seen in only one frame counts above this confidence |
| `ROADSENSE_DEPTH` | `0` | `1` adds the Depth Anything V2 depth index to severity (slow on CPU) |
| `ROADSENSE_NAGRIK` | `1` | `0` turns off ward, officer and tender lookup |
| `ROADSENSE_DATA_DIR` | `backend/data` | Database, media and cache (tests use a temporary folder) |
| `ROADSENSE_ADMIN_EMAILS` | – | Emails that become administrators when they sign up |
| `ROADSENSE_TRUST_PROXY` | `0` | `1` behind a hosting proxy, so rate limits use the visitor's address |

## Deployment

- **Local**: `start.cmd` runs the FastAPI backend (port 8000) and the Nagrik site (port 5501), which forwards
  `/roads`, `/api`, `/media` and `/eval` to the backend.
- **Online**: the website is static on Vercel (`vercel.json`, `deploy/vercel-build.mjs`), and Vercel forwards the API
  to the backend on a Hugging Face Space. Uploads over 12 MB go to the Space directly.

## Tests

`backend/tests/` covers the pipeline end to end (photo and video), dedup, risk, review/workflow, roads, crew and
privacy, and dashcam GPS. Each test uses a temporary data folder and runs on the CPU:
`python backend/tests/test_pipeline.py` (likewise for the other files).
