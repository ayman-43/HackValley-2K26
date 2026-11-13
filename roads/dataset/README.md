# Nagrik Roads training dataset

The full dataset is **2.4 GB (21,562 images)**, too large for a Git repository. This folder holds everything
needed to inspect it and rebuild it exactly:

| Path | What it is |
|---|---|
| `data.yaml` | YOLO dataset config (4 classes) |
| `splits/train.txt`, `splits/val.txt` | Every image in the train / val split (fixed seed-42 split) |
| `splits/val_in.txt`, `val_bp.txt`, `val_kg.txt`, `val_us.txt` | Per-source validation lists used for per-dataset evaluation |
| `stats.json` | Image and label counts per split, source and class |
| `sample/images`, `sample/labels` | 20 labelled validation images (5 per source) in YOLO format |
| `sample/sample_video.mp4` | 15 s road video from the Kaggle "Potholes YOLOv8" dataset (CC0), used by the tests and `scripts/evaluate.py` |

## Sources

| Prefix | Source | Licence | Train images | Val images |
|---|---|---|---|---|
| `in_` | [RDD2022](https://github.com/sekilab/RoadDamageDetector), India subset | CC BY-SA 4.0 | 6,936 | 770 |
| `bp_` | BharatPotHole (Roboflow Universe), Indian dashcam frames | CC BY 4.0 | 5,067 | 2,007 |
| `us_` | [RDD2022](https://github.com/sekilab/RoadDamageDetector), United States subset | CC BY-SA 4.0 | 4,325 | 480 |
| `kg_` | Kaggle "Potholes Detection YOLOv8" | CC0 | 1,581 | 396 |
| | **Total** | | **17,909** | **3,653** |

## Labels per class (train)

| Class | India | BharatPotHole | US | Kaggle | Total |
|---|---|---|---|---|---|
| 0 longitudinal_crack | 1,381 | 0 | 6,050 | 0 | 7,431 |
| 1 transverse_crack | 61 | 0 | 2,966 | 0 | 3,027 |
| 2 alligator_crack | 1,816 | 0 | 763 | 0 | 2,579 |
| 3 pothole | 2,837 | 8,519 | 117 | 5,942 | 17,415 |

Label format: one `.txt` per image, one line per box, `class x_center y_center width height` (normalised 0–1).
RDD2022 Pascal VOC XML was converted to this format and its D00/D10/D20/D40 codes remapped to classes 0–3;
the two pothole-only datasets were mapped to class 3.

## Rebuild the full dataset

1. Download the four source archives into one folder:
   `RDD2022_India_kaggle.zip`, `RDD2022_US_CZ_kaggle.zip`, `bharatpothole.zip`, `kaggle_potholes_yolov8.zip`.
2. Run:
   ```
   .venv\Scripts\python.exe scripts/prepare_dataset.py --raw <downloads folder> --out <dataset folder>
   ```
   This unzips, converts, deduplicates, remaps the classes and writes the same seed-42 split listed in `splits/`.
3. Train: `.venv\Scripts\python.exe scripts/train.py --data <dataset folder>/data.yaml`
   (or on Kaggle with `scripts/kaggle_train_y26s.py`).
