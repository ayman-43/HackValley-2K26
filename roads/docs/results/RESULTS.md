# Nagrik Roads: detection results

All numbers are measured on held-out validation images that the models never saw during training
(the same fixed seed-42 split for every model, so the comparisons are fair).

## Final model: YOLO26s, trained on Kaggle

| | |
|---|---|
| Training | Kaggle Tesla T4 GPU, 148 epochs, 640 px, from COCO-pretrained YOLO26s |
| Training data | 17,909 images: RDD2022 India + United States, BharatPotHole, Kaggle Potholes |
| Validation data | 3,653 images, 4 classes: longitudinal, transverse and alligator cracks, potholes |
| **mAP50 (all validation)** | **0.686** |
| Precision / Recall | 0.719 / 0.634 |
| mAP50-95 | 0.369 |

Per validation source (mAP50): BharatPotHole **0.659**, Kaggle Potholes 0.806, RDD2022 USA 0.618, RDD2022 India 0.500.
Per class (mAP50): longitudinal crack 0.709, transverse crack 0.637, alligator crack 0.709, pothole 0.688.

Training curves: `training_curves.png` (losses fall steadily; mAP50 rises from 0.23 to 0.69 and levels off).

## How it improved

| Model | Indian dashcam potholes (BharatPotHole) F1 | Indian roads, all 4 types (RDD India) F1 |
|---|---|---|
| Off-the-shelf RDD2022 YOLOv8s (starting point) | 0.03 | 0.65* |
| Our YOLO26n, laptop RTX 2050, 29 epochs | 0.49 | 0.50 |
| **Our YOLO26s, Kaggle T4, 148 epochs** | **0.68** | **0.54** |
| YOLO26s + RDD2022 crack model (used for uploads) | 0.67 | **0.61** |

\* The off-the-shelf model was trained on the RDD2022 challenge data, which very likely contains these
RDD India validation images, so its RDD India score is optimistic. It almost never finds Indian dashcam potholes.

## Tuning without retraining

- **Confidence threshold:** 0.20 gives the best F1 for YOLO26s on the Indian validation sets
  (`tuning_y26s.json`, from `scripts/tune_detector.py`).
- **Image size:** 640 px. Larger inputs (800 / 960) were not more accurate overall and were up to 1.9x slower.
- **Crack combination** (`combo_y26s.json`, from `scripts/eval_combo.py`): uploads keep potholes from YOLO26s
  and cracks from the RDD2022 crack model (conf 0.35). Live scan uses YOLO26s alone for speed.

## Counting each defect once

Detection is per frame; Nagrik Roads then tracks every defect across frames (ByteTrack, at least 3 frames)
and merges repeat sightings by GPS distance and image similarity. On the 15-second demo video, about
1,230 per-frame detections become **39 unique potholes**; a second drive over the same road matches the
same potholes instead of creating duplicates.

## Speed (laptop CPU, no GPU)

YOLO26s: about 135 ms per frame, fast enough for live scan at about 3 frames per second.
A 15-second dashcam video is fully processed (detection, tracking, GPS, priority, ward lookup) in about 1.5 minutes.

## Files in this folder

| File | What it shows |
|---|---|
| `training_curves.png` | Loss and accuracy over the 148 training epochs |
| `confusion_matrix.png` | Which damage types get confused with each other (normalised) |
| `precision_recall_curve.png`, `f1_curve.png` | Accuracy trade-offs at every confidence threshold |
| `example_predictions.jpg` / `example_ground_truth.jpg` | Model boxes vs human labels on validation images |
| `roadsense_y26s_metrics.json` | Full Kaggle validation metrics per source and class |
| `kaggle_y26s_results.csv` | Every epoch's losses and metrics |
| `tuning_y26s.json`, `combo_y26s.json`, `compare.json` | Local tuning, combination and model-comparison runs |
