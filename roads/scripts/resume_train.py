"""Resume an interrupted scripts/train.py run from its last.pt, then do the same
evaluation (all + per source) and export steps as train.py.

Usage:  python scripts/resume_train.py [--run ~/roadsense-data/runs/rdd_us_in_kaggle_y26n]
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

from ultralytics import YOLO

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train import PROJECT_MODELS, evaluate  # noqa: E402  same metrics code as the original run


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=str(Path.home() / "roadsense-data" / "runs" / "rdd_us_in_kaggle_y26n"))
    a = ap.parse_args()
    run = Path(a.run)
    last = run / "weights" / "last.pt"
    if not last.exists():
        raise SystemExit(f"no checkpoint at {last}")

    model = YOLO(str(last))
    data = model.ckpt["train_args"]["data"] if getattr(model, "ckpt", None) else None
    print(f"resuming {last} (data={data})", flush=True)
    model.train(resume=True)  # restores epochs, optimizer, LR schedule, data, project/name

    best = run / "weights" / "best.pt"
    model = YOLO(str(best))
    data = data or str(Path.home() / "roadsense-data" / "roadsense" / "data.yaml")
    ds = Path(data).parent
    results = [evaluate(model, data, name="all")]
    for src, label in (("in", "rdd_india"), ("bp", "bharatpothole"), ("us", "rdd_usa"), ("kg", "kaggle_potholes")):
        lst = ds / f"val_{src}.txt"
        if lst.exists() and lst.read_text().strip():
            results.append(evaluate(model, data, lst, name=label))

    PROJECT_MODELS.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, PROJECT_MODELS / "roadsense_y26n.pt")
    onnx = model.export(format="onnx", imgsz=640, simplify=True)
    shutil.copy2(onnx, PROJECT_MODELS / "roadsense_y26n.onnx")
    (PROJECT_MODELS / "roadsense_y26n_metrics.json").write_text(json.dumps(results, indent=2))
    print(f"\nSaved model + metrics to {PROJECT_MODELS}", flush=True)


if __name__ == "__main__":
    main()
