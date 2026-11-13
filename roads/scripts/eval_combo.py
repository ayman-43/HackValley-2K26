"""Evaluate combining two detectors: potholes from model A (ours), cracks from model B (rdd_yolov8s).

Each model runs once per validation list (conf >= 0.01); the combination keeps A's pothole boxes
(conf >= --pothole-conf) and B's crack boxes (conf >= each threshold in the sweep). Reports P/R/F1 per class
and overall, for A alone, B alone and the combination, plus CPU time per frame for A+B.

CAVEAT: rdd_yolov8s was trained on RDD2022 (CRDDC2022), which very likely contains our RDD India / US
validation images, so its crack scores on val_in / val_us are optimistic.

Usage (repo root):
    .venv\\Scripts\\python.exe scripts/eval_combo.py --images C:\\...\\val_in.txt C:\\...\\val_bp.txt
Results -> backend/data/eval/combo.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_models import list_images, match, prf  # noqa: E402
from tune_detector import run  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NAMES = ["longitudinal_crack", "transverse_crack", "alligator_crack", "pothole"]
CRACKS = {0, 1, 2}


def combine(pa, pb, pothole_conf, crack_conf):
    out = []
    for (ac, ab, af, gc, gb), (bc, bb, bf, _, _) in zip(pa, pb):
        ka = (ac == 3) & (af >= pothole_conf)
        kb = np.isin(bc, list(CRACKS)) & (bf >= crack_conf)
        out.append((np.concatenate([ac[ka], bc[kb]]).astype(int), np.concatenate([ab[ka], bb[kb]]).reshape(-1, 4),
                    np.concatenate([af[ka], bf[kb]]), gc, gb))
    return out


def per_class(per_image, thr=0.0):
    stats = {c: [0, 0, 0] for c in range(4)}  # tp, n_pred, n_gt
    for pc, pb, cf, gc, gb in per_image:
        m = cf >= thr
        pc, pb = pc[m], pb[m]
        ok = match(pc, pb, gc, gb)[:, 0] if len(pc) and len(gc) else np.zeros(len(pc), bool)
        for c in range(4):
            stats[c][0] += int(ok[pc == c].sum()) if len(pc) else 0
            stats[c][1] += int((pc == c).sum())
            stats[c][2] += int((gc == c).sum())
    tot = [sum(v[i] for v in stats.values()) for i in range(3)]
    return {"overall": prf(*tot), **{NAMES[c]: prf(*v) for c, v in stats.items()}}


def cpu_ms(models, images, n=20):
    imgs = [cv2.imread(str(p)) for p in images[:n]]
    for m in models:
        m.predict(imgs[0], device="cpu", verbose=False)
    t = time.perf_counter()
    for im in imgs:
        for m in models:
            m.predict(im, conf=0.15, device="cpu", verbose=False)
    return round((time.perf_counter() - t) / len(imgs) * 1000, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default=str(ROOT / "backend/models/roadsense_y26n.pt"), help="pothole model")
    ap.add_argument("--b", default=str(ROOT / "backend/models/rdd_yolov8s.pt"), help="crack model")
    ap.add_argument("--images", nargs="+", required=True)
    ap.add_argument("--pothole-conf", type=float, default=0.15)
    ap.add_argument("--device", default="0")
    ap.add_argument("--out", default=str(ROOT / "backend/data/eval/combo.json"))
    a = ap.parse_args()
    from ultralytics import YOLO
    ma, mb = YOLO(a.a, task="detect"), YOLO(a.b, task="detect")
    report = {"a": a.a, "b": a.b, "pothole_conf": a.pothole_conf, "sets": {}}
    first = None
    for src in a.images:
        name, imgs = Path(src).stem, list_images(src)
        first = first or imgs
        pa, pb = run(ma, imgs, 640, a.device), run(mb, imgs, 640, a.device)
        res = {"A_alone@0.15": per_class(pa, 0.15), "B_alone@0.25": per_class(pb, 0.25)}
        for t in (0.15, 0.25, 0.35):
            res[f"combo_crack@{t:.2f}"] = per_class(combine(pa, pb, a.pothole_conf, t))
        report["sets"][name] = res
        print(f"\n{name} ({len(imgs)} images)          overall F1 | " + " | ".join(f"{n[:5]} F1" for n in NAMES))
        for k, v in res.items():
            print(f"  {k:22s} {v['overall']['f1']:.3f}      | " + " | ".join(f"{v[n]['f1']:.3f}   " for n in NAMES))
    report["cpu_ms_A"] = cpu_ms([ma], first)
    report["cpu_ms_A_plus_B"] = cpu_ms([ma, mb], first)
    print(f"\nCPU per frame: A alone {report['cpu_ms_A']} ms, A+B {report['cpu_ms_A_plus_B']} ms")
    Path(a.out).write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
