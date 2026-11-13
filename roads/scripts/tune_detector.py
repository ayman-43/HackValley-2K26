"""Tune the detector's inference settings (confidence threshold, image size) without retraining.

For each image size the model runs ONCE over the validation lists (keeping every box with conf >= 0.01);
precision / recall / F1 are then recomputed for every threshold (greedy IoU-0.5 matching, re-done per
threshold so low-confidence boxes cannot steal matches), plus mAP50 from the full ranked list.
Also times inference on the CPU, which is what the app uses.

Usage (repo root):
    .venv\\Scripts\\python.exe scripts/tune_detector.py --model backend/models/roadsense_y26n.pt \
        --images C:\\Users\\ayman\\roadsense-data\\roadsense\\val_in.txt C:\\...\\val_bp.txt --sizes 640 800 960
Results -> backend/data/eval/tuning.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_models import canonical, list_images, load_labels, match, prf  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
THRESHOLDS = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]


def run(model, images, imgsz, device, batch=16):
    """Per image: (pred_cls, pred_box, pred_conf, gt_cls, gt_box)."""
    out = []
    for i in range(0, len(images), batch):
        chunk = images[i:i + batch]
        res = model.predict([str(p) for p in chunk], imgsz=imgsz, conf=0.01, device=device, verbose=False)
        for p, r in zip(chunk, res):
            h, w = r.orig_shape
            gc, gb = load_labels(p, w, h)
            b = r.boxes
            cls = np.array([canonical(r.names[int(k)]) for k in b.cls.cpu().numpy()], dtype=object)
            keep = np.array([c is not None for c in cls], dtype=bool)
            out.append((cls[keep].astype(int), b.xyxy.cpu().numpy()[keep], b.conf.cpu().numpy()[keep], gc, gb))
    return out


def scores(per_image, thr):
    tp = n_pred = n_gt = 0
    for pc, pb, cf, gc, gb in per_image:
        m = cf >= thr
        n_pred += int(m.sum())
        n_gt += len(gc)
        if m.any() and len(gc):
            tp += int(match(pc[m], pb[m], gc, gb)[:, 0].sum())
    return prf(tp, n_pred, n_gt)


def map50(per_image):
    from ultralytics.utils.metrics import ap_per_class
    tps, confs, pcls, gcls = [], [], [], []
    for pc, pb, cf, gc, gb in per_image:
        tps.append(match(pc, pb, gc, gb))
        confs.append(cf); pcls.append(pc); gcls.append(gc)
    tp = np.concatenate(tps) if tps else np.zeros((0, 10), bool)
    res = ap_per_class(tp, np.concatenate(confs), np.concatenate(pcls), np.concatenate(gcls))
    ap = res[5]  # [classes, 10 IoU thresholds]
    return float(ap[:, 0].mean()) if len(ap) else 0.0


def cpu_ms(model, images, imgsz, n=25):
    imgs = [cv2.imread(str(p)) for p in images[:n]]
    model.predict(imgs[0], imgsz=imgsz, device="cpu", verbose=False)  # warm-up
    t = time.perf_counter()
    for im in imgs:
        model.predict(im, imgsz=imgsz, conf=0.25, device="cpu", verbose=False)
    return (time.perf_counter() - t) / len(imgs) * 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(ROOT / "backend/models/roadsense_y26n.pt"))
    ap.add_argument("--images", nargs="+", required=True)
    ap.add_argument("--sizes", nargs="+", type=int, default=[640, 800, 960])
    ap.add_argument("--device", default="0")
    ap.add_argument("--out", default=str(ROOT / "backend/data/eval/tuning.json"))
    a = ap.parse_args()
    from ultralytics import YOLO
    model = YOLO(a.model, task="detect")
    sets = {Path(s).stem: list_images(s) for s in a.images}
    report = {"model": a.model, "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "results": []}
    for size in a.sizes:
        row = {"imgsz": size, "cpu_ms_per_frame": round(cpu_ms(model, next(iter(sets.values())), size), 1), "sets": {}}
        for name, imgs in sets.items():
            per = run(model, imgs, size, a.device)
            row["sets"][name] = {"images": len(imgs), "mAP50": round(map50(per), 4),
                                 "by_threshold": {f"{t:.2f}": scores(per, t) for t in THRESHOLDS}}
        report["results"].append(row)
        print(f"\nimgsz {size}: {row['cpu_ms_per_frame']} ms/frame on CPU")
        for name, s in row["sets"].items():
            best = max(s["by_threshold"].items(), key=lambda kv: kv[1]["f1"])
            cur = s["by_threshold"]["0.25"]
            print(f"  {name:8s} mAP50 {s['mAP50']:.3f} | conf 0.25: P {cur['precision']:.3f} R {cur['recall']:.3f} F1 {cur['f1']:.3f}"
                  f" | best conf {best[0]}: P {best[1]['precision']:.3f} R {best[1]['recall']:.3f} F1 {best[1]['f1']:.3f}")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("\nwrote", a.out)


if __name__ == "__main__":
    main()
