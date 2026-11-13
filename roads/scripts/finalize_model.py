"""Finalise a training run without more training: evaluate best.pt (all + per source, same code as
train.py), copy it into backend/models, export ONNX and write the metrics JSON.

Usage:  python scripts/finalize_model.py [--run ~/roadsense-data/runs/rdd_us_in_kaggle_y26n] [--name roadsense_y26n]
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

from ultralytics import YOLO

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train import PROJECT_MODELS, evaluate  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=str(Path.home() / "roadsense-data" / "runs" / "rdd_us_in_kaggle_y26n"))
    ap.add_argument("--data", default=str(Path.home() / "roadsense-data" / "roadsense" / "data.yaml"))
    ap.add_argument("--name", default="roadsense_y26n")
    a = ap.parse_args()
    best = Path(a.run) / "weights" / "best.pt"
    model = YOLO(str(best))
    ds = Path(a.data).parent
    results = [evaluate(model, a.data, name="all")]
    for src, label in (("in", "rdd_india"), ("bp", "bharatpothole"), ("us", "rdd_usa"), ("kg", "kaggle_potholes")):
        lst = ds / f"val_{src}.txt"
        if lst.exists() and lst.read_text().strip():
            results.append(evaluate(model, a.data, lst, name=label))
    PROJECT_MODELS.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, PROJECT_MODELS / f"{a.name}.pt")
    (PROJECT_MODELS / f"{a.name}_metrics.json").write_text(json.dumps(results, indent=2))
    print("metrics saved", flush=True)
    onnx = model.export(format="onnx", imgsz=640, simplify=True)
    shutil.copy2(onnx, PROJECT_MODELS / f"{a.name}.onnx")
    print(f"\nFINALIZE DONE -> {PROJECT_MODELS}", flush=True)


if __name__ == "__main__":
    main()
