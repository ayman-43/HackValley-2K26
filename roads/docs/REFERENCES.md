# Research and references

The papers, datasets and tools Nagrik Roads builds on, and where each one is used in this repository.
Competitor projects we studied are in [`RESEARCH.md`](RESEARCH.md); our own measured results are in
[`results/RESULTS.md`](results/RESULTS.md).

## 1. Road-damage detection

| # | Reference | How Nagrik Roads uses it |
|---|---|---|
| [1] | J. Redmon, S. Divvala, R. Girshick, A. Farhadi. *You Only Look Once: Unified, Real-Time Object Detection.* CVPR 2016. arXiv:1506.02640 | The single-stage detector family we use. One pass per frame is what lets detection run on a CPU and in live scan. |
| [2] | G. Jocher, J. Qiu, A. Chaurasia. *Ultralytics YOLO* (software, AGPL-3.0), including the YOLO26 models. https://github.com/ultralytics/ultralytics | Training and inference engine. Our detector `roadsense_y26s.pt` is YOLO26s trained by us (148 epochs on Kaggle, `scripts/kaggle_train_y26s.py`). |
| [3] | H. Maeda, Y. Sekimoto, T. Seto, T. Kashiyama, H. Omata. *Road Damage Detection and Classification Using Deep Neural Networks with Smartphone Images.* Computer-Aided Civil and Infrastructure Engineering 33(12), 2018. arXiv:1801.09454 | Origin of the smartphone road-damage task and the D00/D10/D20/D40 classes we map to longitudinal, transverse and alligator crack, and pothole (`detector.canonical`). |
| [4] | D. Arya, H. Maeda, S. K. Ghosh, D. Toshniwal, A. Mraz, T. Kashiyama, Y. Sekimoto. *Deep learning-based road damage detection and classification for multiple countries.* Automation in Construction 132, 103935, 2021. | Shows models trained in one country transfer poorly to another. This is why we added Indian data (RDD India + BharatPotHole) instead of using an off-the-shelf model. |
| [5] | D. Arya, H. Maeda, S. K. Ghosh, D. Toshniwal, Y. Sekimoto. *RDD2022: A multi-national image dataset for automatic Road Damage Detection.* 2022. arXiv:2209.08538 | Main training data (India and United States subsets, CC BY-SA 4.0), prepared by `scripts/prepare_dataset.py`. |
| [6] | D. Arya et al. *Crowdsensing-based Road Damage Detection Challenge (CRDDC'2022).* IEEE BigData 2022. | Benchmark behind the public RDD2022 YOLOv8s weights we use as the optional crack model and as a baseline (`scripts/compare_models.py`). |
| [7] | C. Koch, I. Brilakis. *Pothole detection in asphalt pavement images.* Advanced Engineering Informatics 25(3), 2011. | Classical (non-deep) pothole detection from shape and texture; background for scoring severity from damage type and visible size in `severity.py`. |

## 2. Phone-based road monitoring

| # | Reference | How Nagrik Roads uses it |
|---|---|---|
| [8] | J. Eriksson, L. Girod, B. Hull, R. Newton, S. Madden, H. Balakrishnan. *The Pothole Patrol: Using a Mobile Sensor Network for Road Surface Monitoring.* MobiSys 2008. | The idea of surveying roads from vehicles people already drive, instead of dedicated survey vans. Our live scan does this with a phone camera. |
| [9] | P. Mohan, V. N. Padmanabhan, R. Ramjee. *Nericell: Rich Monitoring of Road and Traffic Conditions using Mobile Smartphones.* SenSys 2008. | Smartphone road sensing on Indian roads (Bengaluru). Our capture page also records accelerometer bumps next to the video (`capture.js`). |
| [10] | A. Mednis, G. Strazdins, R. Zviedris, G. Kanonirs, L. Selavo. *Real time pothole detection using Android smartphones with accelerometers.* DCOSS 2011. | Real-time detection on the phone side; informs the ~3 frames/s budget of live scan. |

## 3. Counting each pothole once (tracking)

| # | Reference | How Nagrik Roads uses it |
|---|---|---|
| [11] | A. Bewley, Z. Ge, L. Ott, F. Ramos, B. Upcroft. *Simple Online and Realtime Tracking (SORT).* ICIP 2016. arXiv:1602.00763 | Tracking-by-detection: detect per frame, then link detections into tracks. Our `detector.FrameLinker` follows this pattern. |
| [12] | Y. Zhang et al. *ByteTrack: Multi-Object Tracking by Associating Every Detection Box.* ECCV 2022. arXiv:2110.06864 | Our first tracker. On real bike rides it confirmed almost no potholes: it needs the same object again in the very next frame with overlapping boxes, and small potholes seen from a moving vehicle flicker and jump. We replaced it with `FrameLinker`, which links by distance, tolerates missed frames and expects defects to move down the frame (9 s test ride: 1 pothole with ByteTrack, 8 potholes + 2 cracks with `FrameLinker`). |

## 4. Recognising a pothole that was already reported

| # | Reference | How Nagrik Roads uses it |
|---|---|---|
| [13] | C. Zauner. *Implementation and Benchmarking of Perceptual Image Hash Functions.* Master's thesis, Upper Austria University of Applied Sciences, Hagenberg, 2010. | Perceptual hashing (pHash) of each defect crop. Combined with GPS distance and class to decide whether a new sighting is an existing defect (`dedup.py`). |
| [14] | N. Krawetz. *Kind of Like That* (difference hash, dHash). The Hacker Factor Blog, 2013. | dHash, the second hash in the `dedup.py` similarity score. |

## 5. Severity, privacy, risk and routing

| # | Reference | How Nagrik Roads uses it |
|---|---|---|
| [15] | L. Yang, B. Kang, Z. Huang, Z. Zhao, X. Xu, J. Feng, H. Zhao. *Depth Anything V2.* NeurIPS 2024. arXiv:2406.09414 | Optional monocular depth index for pothole severity (`severity.py`, enabled with `ROADSENSE_DEPTH=1`). |
| [16] | T.-Y. Lin et al. *Microsoft COCO: Common Objects in Context.* ECCV 2014. arXiv:1405.0312 | The COCO-trained detector in `privacy.py` finds people and vehicles so faces and number plates are blurred before any image is stored. |
| [17] | D. Luxen, C. Vetter. *Real-time routing with OpenStreetMap data.* ACM SIGSPATIAL GIS 2011. | OSRM, used by the repair-crew route planner (`crew.py`) to find the fastest order to visit defects on real roads. |
| [18] | OpenStreetMap contributors, Overpass API (ODbL). https://www.openstreetmap.org | Road class, speed limit and nearby schools and hospitals for the risk score (`risk.py`). |
| [19] | Open-Meteo weather API (CC BY 4.0 data). https://open-meteo.com | Rain forecast that raises the risk of open potholes (`risk.py`). |

## Datasets

| Dataset | Licence | Used for |
|---|---|---|
| RDD2022 India and United States [5] | CC BY-SA 4.0 | Cracks and potholes, 4 classes |
| BharatPotHole (Roboflow Universe) | CC BY 4.0 | Indian dashcam potholes |
| Kaggle "Potholes YOLOv8" | CC0 | Extra pothole images |
| Ward boundaries, officials and tenders: Nagrik public API (https://nagrik.in.net) | attribution | Linking each defect to its ward and officer |

## Other projects whose ideas we used (our own implementation, no code copied)

- **iWatchRoad**: colouring whole roads by condition instead of only showing defect pins. Our version is `roads.py` (`GET /api/roads/health`) and `frontend/roads.html`.
- **Pothole Pilot** route planner: planning a repair round over the worst defects. Our version is `crew.py` (OSRM `trip` service on the real road graph [17], with a nearest-neighbour fallback when offline) and `frontend/route.html`.

## Baselines we compared against (not part of the app)

- **RDD2022 YOLOv8s** weights from [oracl4/RoadDamageDetection](https://github.com/oracl4/RoadDamageDetection), trained on CRDDC2022 [6]. Kept as the optional crack model.
- **Pothole Pilot** (Technology for Good 2026 hackathon, King's AI Club, Edmonton; GPL-3.0). Its YOLO26n model was a baseline in `scripts/compare_models.py` and its training recipe was our starting point. None of its code or weights ship in Nagrik Roads.

Comparison numbers are in [`../README.md`](../README.md#results) and [`results/RESULTS.md`](results/RESULTS.md).
