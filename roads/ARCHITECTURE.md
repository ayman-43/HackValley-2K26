# Nagrik Roads – build contract (read this first)

AI road-damage detection for Bengaluru (Hackathon PS 02). Six people/agents build in parallel.
**Only edit the files you own.** Connect to other parts ONLY through the signatures below.
If you need something from another owner's file, write it in your final report instead of editing it.

## Hard rules for every agent
- Python env: `.venv\Scripts\python.exe` (Windows, Python 3.12). Already installed: fastapi, uvicorn,
  python-multipart, ultralytics, supervision, opencv, numpy, pillow, imagehash, httpx, transformers,
  onnxruntime. **Do NOT pip install anything** (shared env; parallel installs break it). Use stdlib
  or these. Need something else? Say so in your report.
- **The GPU is busy training a model for ~7 hours. Never use the GPU.** Any model call in tests must
  pass `device="cpu"`. Keep tests small (a few images / a 5-second clip).
- Do not touch `reference/`, `.venv/`, `scripts/prepare_dataset.py`, `scripts/train.py`,
  or anything in `C:\Users\ayman\roadsense-data\`.
- No paid APIs, no API keys. Free services only: OpenStreetMap Overpass + Nominatim, Open-Meteo,
  Nagrik (`https://nagrik.in.net/api/v1`, public, rate-limited 120 req/window → cache everything).
- Every network call: timeout ≤ 8 s, wrapped in try/except, cached to `backend/data/cache/`,
  and the app must keep working offline (fallback values, never crash).
- Keep it lightweight. Plain functions, type hints, short docstrings.

## Models
- Pothole Pilot model (works now): `reference/pothole-pilot/ai_model/weights.onnx` (YOLO26n, ONNX).
- RDD2022 YOLOv8s: `backend/models/rdd_yolov8s.pt`.
- Our model (after training): `backend/models/roadsense_y26n.pt` + `.onnx` + `roadsense_y26n_metrics.json`.
- Classes (all models): 0 longitudinal_crack, 1 transverse_crack, 2 alligator_crack, 3 pothole.
- `detector.py` picks the first existing of: env `ROADSENSE_MODEL`, roadsense_y26n.pt, rdd_yolov8s.pt, Pothole Pilot ONNX
  (Pothole Pilot ONNX is last because it scores ~0 F1 on Indian data; see `backend/data/eval/compare.json`).

## Environment variables
| Variable | Default | Meaning |
|---|---|---|
| `ROADSENSE_MODEL` | auto (order above) | Path to detector weights |
| `ROADSENSE_DEVICE` | `cpu` | `0` to run inference on the GPU |
| `ROADSENSE_NAGRIK` | `1` | `0` disables Nagrik ward/officer/tender enrichment |
| `ROADSENSE_DEPTH` | `0` | `1` enables Depth-Anything depth index in severity (heavy) |
| `ROADSENSE_DATA_DIR` | `backend/data` | Database + media folder (tests use a temp dir) |
| `ROADSENSE_CONF` | `0.20` | Detection confidence threshold (tuned for roadsense_y26s with scripts/tune_detector.py) |
| `ROADSENSE_COMBINE` | `1` | Uploads (photos/videos) use potholes from the main model + cracks from the crack model; `0` = main model only. Live scan always uses the main model |
| `ROADSENSE_CRACK_MODEL` | `backend/models/rdd_yolov8s.pt` | Crack model for the combined detector (scripts/eval_combo.py) |
| `ROADSENSE_CRACK_CONF` | `0.35` | Confidence threshold for crack boxes from the crack model |
| `PIN_MEMORY` | – | Set `False` when training on Windows (avoids CUDA "resource already mapped" crash) |

## Data model (`backend/app/db.py`, SQLite, owned by Agent 1 — already written)
- `inspections`: one upload (image or video).
- `defects`: one PHYSICAL defect (unique pothole/crack). code `PH-0001`. Fields include lat/lon,
  severity_score/label, risk_score/label/risk_factors(JSON), trend, report_count, workflow,
  forecast(JSON), ward_name/ward_id/corporation/city, nagrik(JSON), bad_repair, embedding(JSON).
- `observations`: every sighting of a defect (bbox, confidence, severity, frame_path, crop_path,
  lat/lon, track_id, frames_seen, match_info, absent, review_status pending|confirmed|rejected|corrected).
- Use `db.connect()` (context manager, row_factory=Row) and `db.row(r)` to turn rows into dicts.

## Module contracts (function signatures are fixed)

### Agent 1 – core pipeline + API server  (owns `backend/app/main.py, detector.py, severity.py, geo.py, pipeline.py, db.py`)
- `geo.exif_gps(path) -> tuple[float,float] | None`
- `geo.load_track(path) -> list[dict]`  # GPX, CSV (t,lat,lon) or JSON [{t,lat,lon}] ; t = seconds from video start
- `geo.interpolate(track, t: float) -> tuple[float,float] | None`
- `geo.haversine_m(lat1, lon1, lat2, lon2) -> float`
- `pipeline.process_inspection(inspection_id) -> dict` runs: detect/track → severity → crop+frame
  saved under `backend/data/media/` → dedup.match_defect → create/update defect + observation →
  risk + nagrik enrichment → dedup.update_progress → dedup.mark_absent.
- REST API (all JSON, under `/api`):
  - `POST /api/inspections` multipart: `file` (image/video), optional `lat`, `lon`, `gps_track` (file),
    `road_type`, `notes` → `{inspection_id, job_id}`; processing runs in a background thread.
  - `GET /api/jobs/{job_id}` → `{status: queued|running|done|error, progress 0..1, result?, error?}`
  - `GET /api/inspections`, `GET /api/inspections/{id}` (incl. annotated media URL + observations)
  - `GET /api/defects?status=&severity=&damage_class=&ward=&min_risk=` → list (sorted by risk desc)
  - `GET /api/defects/{id}` → defect + observations (timeline, oldest first)
  - `GET /api/stats` → counts by class, severity, workflow, trend; totals; raw vs unique detections
  - `GET /api/metrics` → contents of `backend/models/*_metrics.json` + `backend/data/eval/*.json`
  - Static: `/media/...` → `backend/data/media`, `/` → `frontend/` (index.html), `/capture` → `frontend/capture.html`
  - Includes `review.router` and `reports.router` (Agent 4) with `app.include_router(...)`.
- Run: `.venv\Scripts\python.exe -m uvicorn backend.app.main:app --port 8000` (from repo root).

### Agent 2 – duplicate detection + progress monitoring  (owns `backend/app/dedup.py`, `backend/tests/test_dedup.py`)
- `fingerprint(crop_bgr: np.ndarray) -> dict`  # pHash + colour histogram, JSON-serialisable
- `similarity(fp_a: dict, fp_b: dict) -> float`  # 0..1
- `match_defect(conn, damage_class: str, lat: float|None, lon: float|None, fp: dict) -> tuple[int|None, dict]`
  3 layers: GPS radius (≤15 m) + image similarity + compatible class. Returns (defect_id or None, match_info
  `{"reason": str, "distance_m": float|None, "similarity": float, "candidates": int}`).
  No GPS → only match on very high similarity (≥0.92).
- `update_progress(conn, defect_id: int) -> str` compares latest vs previous non-absent observation
  (severity_score / area_ratio): `worsening | improving | stable | new`; writes `defects.trend`.
- `mark_absent(conn, lat: float, lon: float, inspection_id: int, seen_defect_ids: set[int], radius_m=12) -> list[int]`
  open defects near a re-inspected location that were NOT seen → insert absent observation; 1st absence
  → trend `possibly_repaired`; 2nd consecutive or workflow=repaired → `repaired`.
- `check_bad_repair(conn, defect_id: int) -> bool` defect seen again within 90 days of `repaired_at` → set `bad_repair=1`, reopen.

### Agent 3 – risk, forecast, civic data  (owns `backend/app/risk.py`, `backend/app/nagrik.py`, `backend/tests/test_risk.py`)
- `risk.road_context(lat, lon) -> dict` (Overpass, cached by ~50 m grid): `{road_type, road_name, speed_limit, lanes, sensitive: [{kind: school|hospital|bus_stop, name, distance_m}]}`
- `risk.weather(lat, lon) -> dict` (Open-Meteo, cached 1 h): `{rain_next_72h_mm, rain_days, temp_c}`
- `risk.compute_risk(defect: dict, context: dict, weather: dict) -> dict` → `{risk_score 0..100, risk_label Low|Medium|High|Critical, risk_factors: [{factor, detail, points}]}`
  Transparent additive formula (severity, road class, speed, sensitive places, report_count, rain, trend). Document weights.
- `risk.forecast(observations: list[dict], weather: dict) -> dict` → `{days_to_high: int|None, text: str, growth_per_week: float|None}`
- `nagrik.resolve(lat, lon) -> dict|None` (city auto-detected from Nagrik `/cities` bounds; only BLR, DEL, GGN, GZB, MUM, PUN) → `{city, ward_id, ward_name, corporation, officials: [{department, name, phone}]}`
- `nagrik.ward_works(city, ward_id, query="pothole") -> list[dict]` → `[{title, agency, tender_ref, status, tender_date, budget, contractor_name, source_url}]` (road/pothole works for that ward)
- Ship a small offline cache for 3 demo Bengaluru points (Indiranagar 100 Ft Rd 12.9719,77.6412; Koramangala 12.9352,77.6245; Majestic 12.9767,77.5713).

### Agent 4 – admin verification, workflow, reports  (owns `backend/app/review.py`, `backend/app/reports.py`, `backend/templates/`, `backend/tests/test_review.py`)
- `review.router = APIRouter(prefix="/api")`:
  - `GET /api/review/queue?limit=` pending observations (lowest confidence first) with image URLs
  - `POST /api/observations/{id}/review` `{action: confirm|reject|correct, corrected_class?, corrected_bbox?, reviewer?}`
    reject → if the defect has no other non-rejected observation, defect workflow=`rejected`.
    Auto-approve rule helper `auto_approve(conn)`: confidence ≥0.6 and report_count ≥3 → confirmed.
  - `PATCH /api/defects/{id}/workflow` `{workflow: verified|scheduled|repaired|closed, note?}` (+ optional repair photo multipart on `POST /api/defects/{id}/repair`)
  - `GET /api/export/corrections` → zip of YOLO-format images/labels from confirmed+corrected observations (for retraining)
- `reports.router = APIRouter(prefix="/api")`:
  - `GET /api/defects/{id}/complaint` → printable HTML complaint (photo, map link, severity, risk factors, ward, officer, tender note, timeline). Print-to-PDF friendly.
  - `GET /api/digest?ward=&period=daily|weekly` → HTML officer digest (counts by risk, new, worsening, overdue >15 d, top 5 to fix) – what would be emailed (do NOT send email).
  - `GET /api/report/summary.csv` → all defects as CSV.

### Agent 5 – dashboard UI  (owns `frontend/index.html`, `frontend/app.js`, `frontend/styles.css`, `frontend/assets/`)
Single-page, plain HTML/CSS/JS, no build step. Leaflet 1.9.4 + Leaflet.heat 0.2.0 from unpkg (pinned).
Views: Upload/Inspect (progress, annotated result, raw vs unique count) · Map (markers by risk, heatmap
toggle, filters) · Defects list (sortable) · Defect detail (photo timeline before/after, trend, forecast,
risk factors, ward/officer/tenders, complaint button, workflow buttons) · Review queue (confirm/reject/
correct class, redraw box on canvas) · Reports (digest preview, CSV) · Metrics (P/R/F1/mAP per class,
confusion matrix image if present). Must degrade gracefully when an endpoint 404s. Mobile-friendly. Light/dark.

### Agent 6 – phone capture + evaluation + docs  (owns `frontend/capture.html`, `frontend/capture.js`, `scripts/evaluate.py`, `scripts/compare_models.py`, `scripts/make_demo_gps.py`, `README.md`)
- `capture.html`: phone web page. Rear camera video via MediaRecorder + `navigator.geolocation.watchPosition`
  → GPS track JSON `[{t,lat,lon}]` (t = seconds since recording start) → upload to `POST /api/inspections`
  (file + gps_track). Also photo mode (capture + current location). Works on mobile Chrome over LAN.
- `scripts/compare_models.py --images <list or dir> --models a.onnx b.pt ... --device cpu` → P/R/F1/mAP50 per
  model per class on a labelled YOLO val list (e.g. `C:\Users\ayman\roadsense-data\roadsense\val_in.txt`,
  `val_bp.txt`), writes `backend/data/eval/compare.json`. Must be runnable later after training.
- `scripts/evaluate.py --video <mp4> --model <path>` → raw detections vs unique tracked defects (≥3 frames), fps → `backend/data/eval/tracking_<name>.json`.
- `scripts/make_demo_gps.py <video> --start lat,lon --end lat,lon` → synthetic GPS track JSON for demo videos.
- `README.md`: what it is, features vs PS requirements, architecture, how to run, credits/licenses
  (Ultralytics AGPL-3.0, Pothole Pilot GPL-3.0 weights, RDD2022 CC BY-SA 4.0, BharatPotHole CC BY 4.0,
  Kaggle potholes CC0, OSM ODbL, Open-Meteo, Nagrik).
