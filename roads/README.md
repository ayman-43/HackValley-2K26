# Nagrik Roads

**AI road-damage and pothole detection for Bengaluru.** Hackathon problem statement PS 02, "AI-Based Road Damage and Pothole Detection System".

Nagrik Roads turns any phone into a road-survey dashcam. You record a ride (or upload photos or videos), and a lightweight YOLO26n detector trained on Indian roads finds potholes and three kinds of cracks. ByteTrack follows each defect across frames so it is counted **once**. GPS, image similarity and class checks recognise a pothole that was already reported, so it updates the existing record instead of creating a duplicate. Every defect gets a severity rating and an explainable risk score, built from road class, speed limit, nearby schools and hospitals, repeat reports and the rain forecast. The system also forecasts when a defect is likely to become dangerous. An admin can confirm, reject or redraw each AI detection. Re-inspections of the same street show whether a defect is worsening, improving or repaired, and Nagrik Roads flags repairs that failed within 90 days. Each defect is linked to its BBMP ward, the responsible officer and recent road tenders through the public Nagrik API. Officers get one daily digest, not a flood of alerts. Everything runs on free, open tools with no paid API.

---

## PS 02 requirements → where Nagrik Roads implements them

### Expected features

| # | Requirement | How / where |
|---|---|---|
| 1 | Upload road images or video clips | Dashboard upload view (`frontend/index.html`, `app.js`) and phone capture page (`/capture`) → `POST /api/inspections` (`backend/app/main.py`), processed in a background job (`GET /api/jobs/{id}`) |
| 2 | Detect potholes and other damage categories | YOLO26n (Ultralytics), 4 classes: longitudinal crack, transverse crack, alligator crack, pothole (`backend/app/detector.py`) |
| 3 | Display bounding boxes / masks | Annotated image or video saved to `backend/data/media/`, shown in the inspection and defect views. The review queue shows each crop with its box |
| 4 | Classify damage severity | `backend/app/severity.py`: damage type × visible area ratio, plus an optional monocular depth index (Depth-Anything-V2, `ROADSENSE_DEPTH=1`) → Low / Medium / High, following the ASTM D6433 logic (depth × diameter) |
| 5 | Capture location metadata | EXIF GPS for photos, a GPS track (JSON/GPX/CSV) for videos interpolated per frame, the phone's live GPS from `/capture`, or manual lat/lon (`backend/app/geo.py`) |
| 6 | Generate road-damage reports | Printable complaint per defect (`/api/defects/{id}/complaint`), officer digest (`/api/digest`), CSV export (`/api/report/summary.csv`) (`backend/app/reports.py`) |
| 7 | Display defect counts and categories | `GET /api/stats`: counts by class, severity, workflow and trend, plus raw vs unique detections. Shown on the dashboard |
| 8 | Dashboard of analysed samples | Single-page dashboard: map (risk markers + heatmap), defects list, defect detail, inspections, reports, metrics |

### Mandatory advanced features

| # | Requirement | How / where |
|---|---|---|
| 1 | Unique pothole tracking | `model.track(..., tracker="bytetrack.yaml")`. Tracks seen in fewer than 3 frames are dropped as flicker, and each track's class comes from a confidence-weighted vote (`detector.track_video`). The sample video gives **3,520 raw detections → 94 unique potholes** (`scripts/evaluate.py`) |
| 2 | Duplicate report detection | `backend/app/dedup.py` checks 3 layers: a GPS radius of 15 m or less, image similarity (pHash + colour histogram), and a compatible class. A duplicate adds an observation and increments `report_count`. Without GPS, only very high similarity (0.92 or more) counts as a match |
| 3 | Road-damage risk prediction | `backend/app/risk.py`: a transparent additive 0–100 score from severity, OSM road class and speed limit, nearby schools, hospitals and bus stops (Overpass), report count, 72-hour rain (Open-Meteo) and trend. Every score lists its factors. Labels are Low, Medium, High and Critical, and high-risk defects are highlighted on the map |
| 4 | Manual verification and correction | Review queue (`backend/app/review.py` + dashboard): confirm, reject, change class or redraw the box. Verified corrections export as a YOLO dataset for retraining (`/api/export/corrections`) |
| 5 | Progress monitoring | Every sighting is stored as a timestamped, geolocated observation. `dedup.update_progress` labels each defect worsening, improving or stable. `mark_absent` marks a defect possibly repaired, then repaired, when a re-inspection no longer finds it. The defect view shows a before/after timeline |

### Technical expectations

| Expectation | How / where |
|---|---|
| YOLO / SSD / equivalent | YOLO26n (ours, ONNX for CPU), with the Pothole Pilot YOLO26n and RDD2022 YOLOv8s as baselines |
| Image preprocessing | Letterbox resize and normalisation at 640 px. Large uploads are downscaled (INTER_AREA). Video frames are sampled with a stride and resized for tracking. Crops are normalised for fingerprinting. Training uses mosaic, HSV and flip augmentation |
| Video-based object tracking | ByteTrack (Ultralytics), with a 3-frame minimum track length |
| Image similarity and/or location for dedup | Both: haversine radius plus pHash/histogram similarity (`dedup.py`) |
| Clear risk scoring + simple verification UI | `risk.compute_risk` (documented weights, factor list) and the review queue |
| Inspection dates and locations over time | SQLite `inspections` and `observations` tables (timestamp, lat/lon, severity, bbox) feed the timeline and trend (`backend/app/db.py`) |
| Precision, recall, F1, mAP | `scripts/train.py` (Ultralytics val, all data + India only), `scripts/compare_models.py` (per class, per dataset, per model), `scripts/evaluate.py` (tracking). Results appear in `backend/data/eval/*.json` and on the dashboard's Metrics view (`GET /api/metrics`) |

## What goes beyond the brief

- **Depth-ready severity.** A relative depth index from Depth-Anything-V2-Small follows the PCI logic (depth × diameter) instead of using box size alone. It is optional because it is heavy.
- **Rain-based worsening forecast.** Growth history plus the Open-Meteo rain forecast produce "likely to reach High in about N days" (`risk.forecast`). Water is the main driver of pothole growth.
- **Nagrik accountability.** Each defect is matched to its ward, corporation, responsible officers and recent pothole/road tenders (agency, contractor, budget) through the public Nagrik API, with results cached.
- **Officer daily digest instead of spam.** One summary per ward covers new, worsening and overdue defects plus the top 5 to fix. It is not one alert per pothole.
- **Bad-repair detection.** A pothole that reappears within 90 days of being marked repaired is flagged `bad_repair` and reopened. This is evidence for contractor liability.
- **Phone-as-dashcam capture (`/capture`).** Records rear-camera video and a 1 Hz GPS track (with accuracy and speed) in the browser. It can also log accelerometer bumps (opt-in) and uploads with progress. No app install or extra hardware is needed.
- **Privacy by default.** Faces and number plates are pixelated in every saved frame, crop and annotated video (`backend/app/privacy.py`, COCO YOLO26n: people → head region, vehicles → plate region; side-view vehicles → both ends). Damage detection runs on the original frame, so accuracy is unaffected. Disable with `ROADSENSE_BLUR=0`. Idea from iWatchRoad (NISER), independent implementation.
- **Repair-crew route planner (`/route.html`).** Pick the top-N defects by risk (or click them on the map), set the depot, and get the optimised driving order on real roads (free OSRM `trip` service), with arrival and repair times per stop, a printable crew sheet, a navigation link and "schedule verified stops". It falls back to an offline nearest-first estimate when OSRM is unreachable (`backend/app/crew.py`). Idea from Pothole Pilot, independent implementation.
- **Lightweight model.** YOLO26n exported to ONNX runs at about 15 images/s on a laptop CPU (about 60 ms per image), so a municipal office needs no GPU.

## Architecture

```
  Phone /capture (MediaRecorder + GPS + bumps)        Dashboard / (Leaflet map, review, reports, metrics)
                 │  multipart: file + track.json                     │  JSON
                 ▼                                                   ▼
 ┌──────────────────────────── FastAPI  backend/app/main.py ─────────────────────────────┐
 │  POST /api/inspections ──► background job ──► pipeline.process_inspection()           │
 │                                                                                         │
 │   detector.py  YOLO26n (ONNX/PT, CPU) ──► ByteTrack (unique IDs, ≥3 frames)            │
 │        │                                                                                │
 │   geo.py  EXIF / GPS track interpolation ──► lat,lon per detection                      │
 │   severity.py  type × area (× depth index) ──► Low/Medium/High                          │
 │   dedup.py  GPS ≤15 m + pHash/colour similarity + class ──► new defect | new observation│
 │             progress: worsening/improving/stable · absent → repaired · bad repair       │
 │   risk.py   OSM Overpass road context + Open-Meteo rain ──► risk 0–100 + factors + forecast│
 │   nagrik.py ward / officers / tenders (cached)                                          │
 │   review.py confirm/reject/correct · workflow · YOLO export     reports.py complaint/digest/CSV│
 └───────────────────────────────┬─────────────────────────────────────────────────────────┘
                                 ▼
          SQLite (inspections · defects · observations)   +   backend/data/media (frames, crops)
```

## Data and model

| Dataset | Use | Licence |
|---|---|---|
| RDD2022, India subset | Indian roads, 4 classes (D00/D10/D20/D40) | CC BY-SA 4.0 |
| RDD2022, United States subset | Cracks, the same subset Pothole Pilot used | CC BY-SA 4.0 |
| BharatPotHole (via Roboflow) | Indian potholes from dashcam video frames | CC BY 4.0 |
| Kaggle "Potholes YOLOv8" | Potholes, the same subset Pothole Pilot used | CC0 |

Combined, deduplicated and remapped to 4 classes (`scripts/prepare_dataset.py`), the training set has **17,909 train / 3,653 val images**: 6,936 India, 5,067 BharatPotHole, 4,325 US and 1,581 Kaggle for training. Classes are 0 longitudinal_crack, 1 transverse_crack, 2 alligator_crack and 3 pothole. The split lists, per-class counts and a labelled sample are in [`dataset/`](dataset/README.md); the full 2.4 GB set is rebuilt from the source archives with `scripts/prepare_dataset.py`.

**Model:** YOLO26n at 640 px, trained with the same recipe as [Pothole Pilot](reference/pothole-pilot/) (built at the Technology for Good 2026 hackathon, King's AI Club, Edmonton) **plus Indian data** (RDD India + BharatPotHole) → `backend/models/roadsense_y26n.pt` / `.onnx`.

### Results

Final YOLO26n (best checkpoint, epoch 29 of 30) on the full validation set, conf 0.001 / IoU 0.7 Ultralytics val (`scripts/finalize_model.py` → `backend/models/roadsense_y26n_metrics.json`):

| Set | Precision | Recall | F1 | mAP50 | mAP50-95 |
|---|---|---|---|---|---|
| All val (3,649 img) | 0.619 | 0.545 | 0.579 | 0.585 | 0.296 |
| RDD India (769 img, 4 classes) | 0.398 | 0.317 | 0.353 | 0.386 | 0.168 |
| BharatPotHole (2,006 img, Indian dashcam) | 0.528 | 0.415 | 0.465 | 0.412 | 0.144 |
| RDD US (479 img) | 0.570 | 0.475 | 0.518 | 0.507 | 0.277 |
| Kaggle potholes (395 img) | 0.776 | 0.687 | 0.728 | 0.780 | 0.498 |

RDD India is the hardest split: it has all four classes, and thin cracks are much harder than potholes. A larger YOLO26s is being trained on Kaggle (2 × T4) with the same data and split (`scripts/kaggle_train_y26s.ipynb`).

Baseline comparison on a CPU, using a random 200-image subsample per split, conf 0.25 and IoU 0.5 (`scripts/compare_models.py` → `backend/data/eval/compare.json`). The table shows overall P / R / F1 / mAP50:

| Val split | Pothole Pilot YOLO26n (ONNX) | RDD2022 YOLOv8s | **Nagrik Roads YOLO26n** § |
|---|---|---|---|
| RDD India | 0.25 / 0.01 / 0.02 / 0.03 | 0.67 / 0.64 / 0.66 / 0.69 † | 0.40 / 0.32 / 0.35 / 0.39 |
| BharatPotHole | 0.12 / 0.01 / 0.01 / 0.01 | 0.00 / 0.00 / 0.00 / 0.00 | **0.53 / 0.42 / 0.46 / 0.41** |
| Kaggle potholes | 0.74 / 0.72 / 0.73 / 0.77 ‡ | 0.36 / 0.17 / 0.23 / 0.16 | 0.78 / 0.69 / 0.73 / 0.78 |
| RDD US | 0.70 / 0.76 / 0.73 / 0.71 ‡ | 0.52 / 0.52 / 0.52 / 0.43 † | 0.57 / 0.48 / 0.52 / 0.51 |

§ Nagrik Roads numbers are from the full validation splits (Ultralytics val on GPU), not the 200-image CPU subsample used for the baselines, so the columns are indicative rather than identical protocols. Nagrik Roads is the only model that works on all four splits, including BharatPotHole (F1 0.46 vs about 0.01 for both baselines). ‡ in-domain for Pothole Pilot. † RDD YOLOv8s was trained on CRDDC2022, which includes RDD India and US training images, so these numbers may be optimistic. **Neither baseline works on BharatPotHole**, the Indian dashcam footage closest to what a Bengaluru phone sees. That gap is why we retrained with Indian data.

Tracking (`scripts/evaluate.py`, Pothole Pilot ONNX, CPU): on the 15 s sample video (375 frames) the model makes **3,520 raw detections, which collapse to 94 unique potholes** at conf 0.1, or 2,986 raw detections → 84 unique at conf 0.25. It runs at about 6–7 fps on a CPU. That clip comes from the Kaggle dataset the model was trained on, so this result is in-domain. A Nagrik Roads-model tracking run on a recorded Bengaluru ride is still to be added.

## How to run

Requirements: Windows/Linux/macOS with Python 3.12. Everything runs on the CPU.

```bash
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: source .venv/bin/activate
pip install fastapi uvicorn python-multipart ultralytics supervision opencv-python numpy pillow imagehash httpx onnxruntime transformers
# from the repo root:
.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

- Dashboard: <http://localhost:8000/>
- Phone capture: <http://localhost:8000/capture>
- Useful env vars: `ROADSENSE_MODEL` (path to .pt/.onnx; default order: `backend/models/roadsense_y26n.pt` → `rdd_yolov8s.pt` → Pothole Pilot ONNX), `ROADSENSE_CONF` (default 0.15, tuned on Indian val: see backend/data/eval/tuning.json), `ROADSENSE_DEPTH=1` (depth index), `ROADSENSE_DEVICE` (default `cpu`), `ROADSENSE_NAGRIK=0` (turn off ward/officer/tender lookup, on by default), `ROADSENSE_DATA_DIR` (database + media folder). Full table in `ARCHITECTURE.md`.

### Using the phone capture page over Wi-Fi (LAN)

Browsers allow the camera and GPS only in a **secure context**: HTTPS or `localhost`. Plain `http://192.168.x.x:8000` does not qualify. Pick one of these options:

1. **Chrome flag (quickest, Android).** Start uvicorn with `--host 0.0.0.0` and find your PC's IP with `ipconfig`. Allow port 8000 through Windows Firewall if prompted. On the phone, open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`, enter `http://<PC-IP>:8000`, choose **Enabled**, relaunch Chrome, then open `http://<PC-IP>:8000/capture`.
2. **HTTPS tunnel (works on iOS too).** For example, run `cloudflared tunnel --url http://localhost:8000` (free quick tunnel, no account needed) or `ssh -R 80:localhost:8000 nokey@localhost.run`, then open the printed `https://…/capture` URL on the phone.
3. **Self-signed certificate.** Run `openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 30 -subj "/CN=roadsense"`, then `uvicorn backend.app.main:app --host 0.0.0.0 --port 8443 --ssl-keyfile key.pem --ssl-certfile cert.pem`. Accept the browser warning once.
4. **USB.** Run `adb reverse tcp:8000 tcp:8000`, and the phone can then use `http://localhost:8000/capture`.

If none of these is possible, the page still offers **"Upload from the camera app"**, which uses the native camera through a file input, without live GPS.

Capture page features:

- **Ride mode** records rear-camera video (MP4 if the browser supports it, otherwise WebM) and a GPS fix about every second as `[{t, lat, lon, speed, accuracy}]`. The optional *bump logging* adds accelerometer spikes as `{points: [...], bumps: [{t, g}]}`.
- **Stop & upload** uploads with a progress bar, follows the processing job and links to `/#/inspection/<id>`.
- **Photo mode** sends one still with the current location.

### Demo video without a phone

```bash
# synthetic GPS for a demo clip along 100 Feet Road, Indiranagar (25 km/h by default)
.venv\Scripts\python.exe scripts/make_demo_gps.py demo.mp4                      # → demo.track.json
.venv\Scripts\python.exe scripts/make_demo_gps.py demo.mp4 --waypoints "12.9784,77.6408;12.9719,77.6412;12.9634,77.6411" --fit
```

Upload `demo.mp4` together with `demo.track.json` as the GPS track.

### Dashcam integration

Three ways to use a vehicle dashcam, from zero hardware to a fleet set-up:

| Set-up | How | Real-time |
|---|---|---|
| Phone as dashcam | Mount a phone, open `/app` → Live scan (camera + phone GPS) | Yes |
| Wi-Fi / USB dashcam | `scripts/dashcam_bridge.py` on a laptop or Raspberry Pi in the vehicle streams the dashcam to `/api/live` | Yes |
| Dashcam SD card | Upload the video and its GPS file (GPX / NMEA / CSV) on **Scan a road** after the ride | After the ride |

The bridge reads any source OpenCV can open, sends ~3 frames/s with the current GPS position, and the
defects appear on the dashboard map within seconds (same tracking and de-duplication as the phone):

```bash
# Wi-Fi dashcam (RTSP) + USB GPS receiver (pip install pyserial)
.venv\Scripts\python.exe scripts/dashcam_bridge.py --source rtsp://192.168.1.254/live --nmea-port COM5
# USB dashcam / webcam at a fixed spot
.venv\Scripts\python.exe scripts/dashcam_bridge.py --source 0 --lat 12.9716 --lon 77.5946
# replay a recorded dashcam file with its GPS track, at real speed, as if it were live
.venv\Scripts\python.exe scripts/dashcam_bridge.py --source ride.mp4 --track ride.gpx
```

Use `--server http://<laptop-ip>:8000` when the bridge runs on a different machine from the server.

## Training and evaluation

```bash
# 1. build the combined 4-class dataset (RDD2022 IN+US, BharatPotHole, Kaggle) -> data.yaml + val_<src>.txt lists
.venv\Scripts\python.exe scripts/prepare_dataset.py --raw <downloads dir> --out <dataset dir>
# 2. train YOLO26n (GPU), evaluate on all val + India only, export ONNX -> backend/models/roadsense_y26n.*
.venv\Scripts\python.exe scripts/train.py --data <dataset dir>/data.yaml --epochs 60
# 3. compare detectors per class / per dataset on CPU -> backend/data/eval/compare.json
.venv\Scripts\python.exe scripts/compare_models.py --images <dataset>/val_in.txt <dataset>/val_bp.txt <dataset>/val_kg.txt <dataset>/val_us.txt --models backend/models/roadsense_y26n.onnx reference/pothole-pilot/ai_model/weights.onnx backend/models/rdd_yolov8s.pt --limit 200
# 4. raw vs unique tracked defects on a video -> backend/data/eval/tracking_<model>.json
.venv\Scripts\python.exe scripts/evaluate.py --video <clip.mp4> --model backend/models/roadsense_y26n.onnx
```

`compare_models.py` takes `.txt` image lists, image directories or single images. It finds each label by swapping `images` for `labels` in the path. It reports P/R/F1 at conf 0.25 and IoU 0.5, plus mAP50 and mAP50-95 from Ultralytics `ap_per_class`. It maps class names across models (D40 / "Potholes" / "pothole" → `pothole`). Pass `--limit N` to subsample for speed. `--device` defaults to `cpu`.

## Credits and licences

- **Ultralytics YOLO** (detection, ByteTrack): AGPL-3.0. Nagrik Roads is therefore released under **AGPL-3.0**.
- **[Pothole Pilot](reference/pothole-pilot/)**: GPL-3.0. We use its YOLO26n weights and training recipe as a reference and baseline. Credit to its authors.
- **RDD2022**: Arya et al., CC BY-SA 4.0. **BharatPotHole** (via Roboflow Universe): CC BY 4.0. **Kaggle Potholes YOLOv8 dataset**: CC0.
- **RDD2022 YOLOv8s baseline weights**: [oracl4/RoadDamageDetection](https://github.com/oracl4/RoadDamageDetection).
- **Supervision** (Roboflow): MIT. **Depth-Anything-V2-Small**: Apache-2.0. **Leaflet** / **Leaflet.heat**: BSD-2-Clause.
- **Map data** © OpenStreetMap contributors, ODbL (Overpass API, Nominatim). Tiles follow the OSM tile usage policy.
- **Weather**: [Open-Meteo](https://open-meteo.com/), free non-commercial API, CC BY 4.0 data.
- **Civic data**: [Nagrik](https://nagrik.in.net/) public API (wards, officials, tenders). Used with attribution, responses are cached, and we have requested permission for continued use.
- Research notes: see `RESEARCH.md`. Build contract: see `ARCHITECTURE.md`.
