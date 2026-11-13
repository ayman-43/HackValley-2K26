"""Train the road-damage detector (same recipe as Pothole Pilot: YOLO26n, 4 classes, 640px)
plus Indian data. Then evaluate on all data and on India only, and export ONNX.

Usage:  python scripts/train.py --data <dataset>/data.yaml [--epochs 60] [--model yolo26n.pt]
"""
import argparse
import json
import shutil
from pathlib import Path

from ultralytics import YOLO

PROJECT_MODELS = Path(__file__).resolve().parents[1] / "backend" / "models"


def f1(p, r):
    return 2 * p * r / (p + r) if p + r else 0.0


def evaluate(model, data, split_file=None, name="all"):
    kw = dict(data=data, imgsz=640, batch=16, device=0, plots=True, verbose=False, name=f"val_{name}")
    if split_file:
        # temporary yaml pointing val at a per-source list (e.g. India only)
        y = Path(data).read_text().replace("val: images/val", f"val: {Path(split_file).as_posix()}")
        tmp = Path(data).with_name(f"data_{name}.yaml")
        tmp.write_text(y)
        kw["data"] = str(tmp)
    m = model.val(**kw)
    p, r = float(m.box.mp), float(m.box.mr)
    res = {"set": name, "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1(p, r), 4),
           "mAP50": round(float(m.box.map50), 4), "mAP50_95": round(float(m.box.map), 4),
           "per_class_mAP50": {model.names[int(c)]: round(float(v), 4)
                               for c, v in zip(m.box.ap_class_index, m.box.ap50)}}
    print(json.dumps(res, indent=2))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--model", default="yolo26n.pt")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch", type=int, default=-1, help="-1 = auto-fit GPU memory")
    ap.add_argument("--project", default=str(Path.home() / "roadsense-data" / "runs"))
    ap.add_argument("--name", default="rdd_us_in_kaggle_y26n")
    a = ap.parse_args()

    model = YOLO(a.model)
    model.train(data=a.data, epochs=a.epochs, imgsz=640, batch=a.batch, device=0, workers=4,
                patience=15, cos_lr=True, close_mosaic=10, project=a.project, name=a.name,
                exist_ok=True, plots=True, seed=42)

    best = Path(a.project) / a.name / "weights" / "best.pt"
    model = YOLO(str(best))
    ds = Path(a.data).parent
    results = [evaluate(model, a.data, name="all")]
    for src, label in (("in", "rdd_india"), ("bp", "bharatpothole"), ("us", "rdd_usa"), ("kg", "kaggle_potholes")):
        lst = ds / f"val_{src}.txt"
        if lst.exists() and lst.read_text().strip():
            results.append(evaluate(model, a.data, lst, name=label))

    PROJECT_MODELS.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, PROJECT_MODELS / "roadsense_y26n.pt")
    onnx = model.export(format="onnx", imgsz=640, simplify=True)
    shutil.copy2(onnx, PROJECT_MODELS / "roadsense_y26n.onnx")
    (PROJECT_MODELS / "roadsense_y26n_metrics.json").write_text(json.dumps(results, indent=2))
    print(f"\nSaved model + metrics to {PROJECT_MODELS}")


if __name__ == "__main__":
    main()
