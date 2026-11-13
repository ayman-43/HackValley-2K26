# Scripts

Run every script from the project folder (the one with `backend/` in it), for example
`.venv\Scripts\python.exe scripts/train.py --help`. They stay in one folder because several import each other.

## Data

| Script | What it does |
|---|---|
| `prepare_dataset.py` | Builds the training set from the RDD2022, BharatPotHole and Kaggle archives: one YOLO format, 4 classes, fixed 83/17 split |
| `pack_for_kaggle.py` | Zips the prepared dataset for upload to Kaggle |
| `make_demo_gps.py` | Makes a GPS track (JSON) for a demo video that has none |

## Training

| Script | What it does |
|---|---|
| `train.py` | Trains YOLO26n on the laptop GPU and evaluates it on every validation source |
| `resume_train.py` | Resumes an interrupted `train.py` run from its last checkpoint |
| `kaggle_train_y26s.py`, `kaggle_train_y26s.ipynb` | The Kaggle notebook that trained the final YOLO26s model (Tesla T4, 148 epochs) |
| `finalize_model.py` | Copies a run's best weights into `backend/models/`, exports ONNX and writes the metrics file |

## Evaluation

| Script | What it does |
|---|---|
| `compare_models.py` | Precision, recall, F1 and mAP per model, per dataset and per class |
| `tune_detector.py` | Finds the confidence threshold and image size with the best F1 |
| `eval_combo.py` | Scores YOLO26s for potholes combined with the RDD2022 model for cracks |
| `evaluate.py` | Counts raw detections vs unique tracked defects on a video |

Results are written to `backend/data/eval/` and summarised in [`docs/results/`](../docs/results/RESULTS.md).

## Live detection tools

| Script | What it does |
|---|---|
| `live_detect.py` | Runs the detector on a webcam, stream or video file, with an optional window |
| `dashcam_bridge.py` | Streams a Wi-Fi or USB dashcam (plus GPS) to the Nagrik Roads live scan, from a laptop or Raspberry Pi in the vehicle |
