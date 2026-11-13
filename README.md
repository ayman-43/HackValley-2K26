# Nagrik Roads

**AI road-damage detection for Bengaluru: find every pothole, count it once, fix the most dangerous first.**

HackValley 2026 · Problem statement PS 02, *AI-Based Road Damage and Pothole Detection System*

A phone or dashcam records the road. A YOLO model trained on Indian roads finds potholes and three kinds of
cracks, tracking follows each defect across frames so it is counted once, GPS pins it on Bengaluru's ward map,
a transparent score ranks it by danger, and Nagrik's public civic data names the ward officer responsible.
Every later drive over the same road checks whether the repair actually happened.

---

## Results

| | |
|---|---|
| **Detector** | YOLO26s, trained on a Kaggle T4 GPU for 148 epochs |
| **Accuracy** | **mAP50 0.686** on 3,653 held-out road images (precision 0.72, recall 0.63) |
| **Indian dashcam potholes** | **F1 0.68**, up from 0.03 with an off-the-shelf RDD2022 model |
| **Counting** | ~1,230 per-frame detections in a 15 s demo video become **39 unique potholes** |
| **Speed** | ~135 ms per frame on a laptop CPU, fast enough for live scanning |

Training curves, confusion matrix, example predictions and the full numbers:
**[`roads/docs/results/`](roads/docs/results/RESULTS.md)**

## What it does

| Step | How |
|---|---|
| **1. Capture** | Phone live scan (frames + GPS ~3×/s), dashcam video upload, or a citizen photo |
| **2. Detect** | YOLO26s finds potholes and longitudinal, transverse and alligator cracks |
| **3. Count once** | ByteTrack follows each defect across frames; flicker under 3 frames is dropped |
| **4. Locate & de-duplicate** | GPS interpolation pins each defect; a 15 m radius + image fingerprint recognises potholes already reported |
| **5. Prioritise** | Explainable 0–100 risk score: severity, road type and speed limit, nearby schools/hospitals/bus stops, repeat reports, rain forecast |
| **6. Assign** | Ward, corporation, responsible officers and nearby road tenders from Nagrik's public API |
| **7. Verify the repair** | Re-inspections mark defects worsening, improving or repaired; a pothole back within 90 days is flagged as a bad repair |

Also included: Nagrik-style ward map (369 Bengaluru wards), review queue where officers confirm or correct the
AI (corrections export as training data), printable complaints and ward digests, repair-crew route planner,
installable phone app, official sign-in with roles, and face / number-plate blurring on every saved image.

## Run it

**Requirements:** Python 3.12, Node 20+.

```powershell
# one-time set-up
cd roads
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
cd ..

# start everything and open http://localhost:5501
start.cmd
```

`start.cmd` starts the Nagrik Roads server (FastAPI, 127.0.0.1:8000) and the Nagrik web server
(Node, http://localhost:5501) together. To scan from a phone on the same Wi-Fi, run `set HOST=0.0.0.0` first.

| Address | Page |
|---|---|
| `/roads/` | Nagrik Roads home |
| `/roads/dashboard` | Officials' dashboard: ward map, defects, review, reports |
| `/roads/app` | Phone app: report a pothole, scan while driving |

The trained model `roads/backend/models/roadsense_y26s.pt` is included. Optional: add the RDD2022 crack model
`rdd_yolov8s.pt` to the same folder to combine it with YOLO26s for better crack detection on uploads.

## Repository layout

| Path | What it is |
|---|---|
```
├── start.cmd, start.mjs      one command starts everything (Nagrik on :5501, Nagrik Roads on :8000)
├── roads/                    Nagrik Roads: the road-damage system
│   ├── backend/app/          FastAPI server: detection, tracking, dedup, risk, sign-in, reports
│   ├── backend/tests/        tests
│   ├── backend/models/       trained detector (roadsense_y26s.pt)
│   ├── frontend/             website pages: home, dashboard, phone app, live scan, sign-in
│   ├── scripts/              dataset, training, evaluation and live-camera tools (see scripts/README.md)
│   ├── dataset/              training data description, splits and licences
│   └── docs/                 architecture, research notes, results for the judges
│       └── results/          model accuracy, plots, Kaggle training log
└── nagrik/                   Nagrik civic-data site that Nagrik Roads plugs into
    └── backend/server.mjs    serves the Nagrik pages and forwards /roads, /api, /media, /eval to Nagrik Roads
```

| Start here | |
|---|---|
| [`roads/README.md`](roads/README.md) | How Nagrik Roads works, requirement by requirement |
| [`roads/docs/results/RESULTS.md`](roads/docs/results/RESULTS.md) | Model results for the judges |
| [`roads/docs/ARCHITECTURE.md`](roads/docs/ARCHITECTURE.md) | Components, API and settings |
| [`roads/dataset/README.md`](roads/dataset/README.md) | Training data and licences |

## About Nagrik

[Nagrik (nagrik.in.net)](https://nagrik.in.net/) is an independent civic-data project that republishes public
records on Bengaluru's wards, officials and public works. The Nagrik pages in `nagrik/` are a local
rebuild of its public site, included to show Nagrik Roads running inside it as one application; ward, officer and
tender data come from Nagrik's public API, with attribution.

## Data and credits

- **Training data:** RDD2022 India and United States (CC BY-SA 4.0), BharatPotHole (CC BY 4.0), Kaggle Potholes
- **Models:** Ultralytics YOLO (AGPL-3.0), RDD2022 YOLOv8s crack model (optional)
- **Maps:** OpenFreeMap and OpenStreetMap contributors (ODbL); ward boundaries from Nagrik
- **Context data:** OpenStreetMap Overpass (road class, nearby facilities), Open-Meteo (rain forecast), OSRM (repair routes)
