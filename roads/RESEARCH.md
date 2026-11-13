# PS 02 – Road Damage & Pothole Detection: Research

_Compiled 2026-11-13. GitHub star counts were taken live from the GitHub API on that date._

## 1. Most-starred GitHub repos and what they cover

| Repo | ★ | What it is | Track IDs | GPS | Severity | Dedup | Risk | Verify | Progress |
|---|---|---|---|---|---|---|---|---|---|
| [ultralytics/ultralytics](https://github.com/ultralytics/ultralytics) | 63.3k | YOLOv8/11 + built-in ByteTrack/BoT-SORT (**our engine**) | tool | – | – | – | – | – | – |
| [roboflow/supervision](https://github.com/roboflow/supervision) | 51.6k | Tracking, line counting, annotators (**our engine**) | tool | – | – | – | – | – | – |
| [sekilab/RoadDamageDetector](https://github.com/sekilab/RoadDamageDetector) | 1,011 | Creators of the RDD dataset; data + baseline | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| [USC-InfoLab/rddc2020](https://github.com/USC-InfoLab/rddc2020) | 292 | Winning entry of the RDD 2020 challenge (YOLOv5 ensemble) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| [coding-parrot/pothole-reporter](https://github.com/coding-parrot/pothole-reporter) | 161 | Android app (GPT vision) that groups nearby reports into one canonical pothole and emails the municipality | ✗ | ✓ | ✗ | ✓ (proximity only) | ✗ | user review | ✗ |
| [lukekratz/raspberry-pi-5-hailo-8-pothole-detection-system](https://github.com/lukekratz/raspberry-pi-5-hailo-8-pothole-detection-system) | 144 | Pi 5 + Hailo-8, YOLOv8, size estimate from a calibrated camera, web UI | ✗ | logging | size | ✗ | ✗ | ✗ | ✗ |
| [oracl4/RoadDamageDetection](https://github.com/oracl4/RoadDamageDetection) | 116 | YOLOv8s on CRDDC2022, Streamlit UI (**our pretrained weights**) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| [FarzadNekouee/YOLOv8_Pothole_Segmentation…](https://github.com/FarzadNekouee/YOLOv8_Pothole_Segmentation_Road_Damage_Assessment) | 57 | YOLOv8n-seg, pothole masks, 780 images | ✗ | ✗ | partial | ✗ | ✗ | ✗ | ✗ |
| [Bryan-Japheth-Charles/RoadScan-AI…](https://github.com/Bryan-Japheth-Charles/RoadScan-AI-Automated-Pothole-Detection-Tracking) | 25 | YOLO11n + ByteTrack + Streamlit | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| [nehaksharma11/RDD2022-Road-Damage-Detection](https://github.com/nehaksharma11/RDD2022-Road-Damage-Detection) | 4 | YOLOv8m on RDD2022, claims 78.1% mAP50 (weights not in repo) | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| [vasugupta9/fusion_pothole_depth_estimation](https://github.com/vasugupta9/fusion_pothole_depth_estimation) | 2 | Depth-Anything + YOLOv8 for pothole depth (proves the depth idea is feasible) | ✗ | ✗ | depth | ✗ | ✗ | ✗ | ✗ |
| [dheeraj-komandur/Pothole-Detection](https://github.com/dheeraj-komandur/Pothole-Detection) | – | SSD + phone accelerometer as a second signal (IEEE PuneCon 2019) | ✗ | ? | ✗ | ✗ | ✗ | ✗ | ✗ |

**Gap:** none of these repos combines tracking, deduplication, risk, verification and progress monitoring. Each covers one or two of the features. The PS requires all five, so implementing all of them already sets a team apart.

## 2. Key facts from the literature

- **Dataset:** RDD2022 has about 47k images from 6 countries, **including India**. Its classes are D00 longitudinal crack, D10 transverse crack, D20 alligator crack and D40 pothole. Potholes are under-represented, which hurts recall on that class.
- **Realistic accuracy:** published YOLOv8 variants score about **62–66% mAP@0.5** on RDD2022 (SEA-YOLOv8 63.2%, RDD-YOLO 62.5%). Treat claims around 78% with caution.
- **Severity standard:** ASTM D6433 (PCI) rates pothole severity Low/Medium/High by **depth × diameter**, with depth ≤25 mm vs >25 mm as the key split. This gives us a standard to base severity on instead of an invented one.
- **Sensor fusion:** a phone accelerometer combined with dashcam images reached **F1 89.9%** (Coventry thesis). Vision struggles in poor light, and vibration-only detection depends on speed. Fusing the two is better than either alone, yet almost no repo does it.
- **What forms potholes:** traffic causes cracks, **water** gets into them, and freeze-thaw or loading turns them into potholes. Poor drainage and low crossfall speed this up (Abed et al., London 2023). I found no published fine-scale model that forecasts pothole formation or growth, so a deterioration forecast would be novel ground.
- **Industry:** Michelin (which bought RoadBotics), Vialytics and RoadVision AI sell smartphone-dashcam road rating to cities. Our system is a free, open, verifiable version of that.

## 3. The unique system

A phone works as a dashcam. AI finds every defect, gives each one an ID that stays the same over time, estimates its depth, forecasts when it will worsen, and produces a repair plan.

### Mandatory features, done properly
1. **Unique tracking:** YOLOv8 (RDD2022) + ByteTrack. Tracks shorter than 3 frames are dropped as flicker. Each class is decided by a confidence-weighted vote across frames, and each defect is counted once when it crosses a line in the frame.
2. **Duplicate detection in 3 layers:** a GPS radius of about 10–15 m, then an image-embedding similarity check (DINOv2), then a class match. A duplicate updates the existing defect and increments its report count instead of creating a new record.
3. **Explainable risk score:** severity + road class and speed limit (OpenStreetMap) + 3-day rain forecast (Open-Meteo, free) + nearby schools or hospitals + report frequency. Every score lists its factors.
4. **Verification and active learning:** the admin can confirm, reject, relabel or redraw a box. Corrections are exported as a YOLO dataset and used to fine-tune the model, and the dashboard shows the before/after metrics.
5. **Progress monitoring:** each defect has a timeline with a before/after slider and a trend label (worsening, improving, stable). If a location is re-scanned and the defect isn't found, it is marked "possibly repaired".

### Differentiators (not found in any repo above)
- **A. PCI-aligned severity with monocular depth.** Depth-Anything-V2 plus the defect's size gives a severity rating that follows the ASTM D6433 logic (depth × diameter), not just box size.
- **B. Phone-as-dashcam PWA with GPS and accelerometer fusion.** No extra hardware is needed. Each frame gets a GPS position, and an accelerometer spike at a pothole location raises confidence and catches potholes the camera missed at night.
- **C. Deterioration forecast.** The pothole's growth history combined with upcoming rainfall estimates "likely to reach High severity in about N days". Research suggests nobody has published this at fine scale.
- **D. Repair planner.** Area × depth gives the patch volume, from which the system estimates material and cost. Repairs are queued by risk, a crew route is drawn on the map, and the report or complaint PDF for each ward is generated automatically.

## 4. Stack
FastAPI · Ultralytics YOLOv8 + ByteTrack · Supervision · Depth-Anything-V2-Small · DINOv2-small · SQLite · Leaflet + heatmap · OpenStreetMap Overpass · Open-Meteo. Everything is free, it runs on the local RTX 2050, and it needs no paid APIs.

## 5. Evaluation (required by the PS)
- Detector: precision, recall, F1, mAP@0.5 and mAP@0.5:0.95 on the RDD2022 validation set (India subset reported separately), plus a confusion matrix.
- Tracking: unique defects counted vs a manual count on test videos.
- Dedup: precision and recall of duplicate matching on repeat-upload pairs.
- Field precision: share of predictions the admin confirmed vs rejected.

## Sources
- RDD2022 paper – https://paperswithcode.com/paper/rdd2022-a-multi-national-image-dataset-for
- SEA-YOLOv8 – https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12176152/
- OBC-YOLOv8 – https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11784705/
- YOLO-RD – https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11902777/
- Pothole segmentation + depth – https://arxiv.org/pdf/2504.13648
- FAA PCI severity levels – https://faapaveair.faa.gov/Help/Documents/severitylevels43.htm
- Pavemetrics LCMS potholes – https://www.pavemetrics.com/capabilities/lcms-2/potholes/
- Accelerometer + image fusion thesis – https://pureportal.coventry.ac.uk/en/studentTheses/deep-learning-approach-for-road-pothole-detection-using-accelerom/
- Crowdsourced multi-sensor fusion – https://www.mdpi.com/2071-1050/15/8/6610/htm
- Pothole formation, London – https://nottingham-repository.worktribe.com/output/20287549/analysis-and-prediction-of-pothole-formation-rate-using-spatial-density-measurements-and-pavement-condition-indicators
- Indiana pothole/weather study – https://docs.lib.purdue.edu/jtrp/1607
- Michelin / RoadBotics – https://mobilityintelligence.michelin.com/us/practical-applications-of-artificial-intelligence-and-machine-learning-for-better-road-maintenance
- AI road assessment startups (ASCE) – https://www.asce.org/publications-and-news/civil-engineering-source/civil-engineering-magazine/issues/magazine-issue/article/2026/01/a-new-ai-powered-tool-could-speed-up-road-assessments
