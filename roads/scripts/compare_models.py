"""Compare road-damage detectors on labelled YOLO validation sets (CPU only).

Usage (from repo root):
    .venv\\Scripts\\python.exe scripts/compare_models.py \
        --images C:\\Users\\ayman\\roadsense-data\\roadsense\\val_in.txt C:\\...\\val_bp.txt \
        --models backend/models/roadsense_y26s.pt backend/models/rdd_yolov8s.pt \
        --limit 50

Each --images entry is a .txt list of image paths, a directory of images, or a single image.
Labels are found with the standard YOLO swap  .../images/... -> .../labels/...  and ext -> .txt.

Metrics per dataset x model x class (canonical classes 0..3):
  * precision / recall / F1 at conf >= --conf (default 0.25), IoU 0.5, greedy one-to-one matching
  * AP50 and AP50-95 from ultralytics.utils.metrics.ap_per_class on predictions at conf >= 0.001
Results -> backend/data/eval/compare.json (+ a table printed to stdout).
"""
from __future__ import annotations

import argparse
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "backend" / "data" / "eval"
CLASSES = ["longitudinal_crack", "transverse_crack", "alligator_crack", "pothole"]
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
IOUV = np.linspace(0.5, 0.95, 10)


def canonical(name: str) -> int | None:
    """Map any model's class name (D40, 'Potholes', 'Alligator Crack'...) to our class index."""
    n = str(name).lower()
    if "pot" in n or n.startswith("d40"):
        return 3
    if "alligator" in n or n.startswith("d20"):
        return 2
    if "transverse" in n or n.startswith("d10"):
        return 1
    if "longitudinal" in n or n.startswith("d00"):
        return 0
    return None


def list_images(src: str) -> list[Path]:
    p = Path(src)
    if p.is_dir():
        return sorted(q for q in p.rglob("*") if q.suffix.lower() in IMG_EXT)
    if p.suffix.lower() == ".txt":
        base = p.parent
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                q = Path(line)
                out.append(q if q.is_absolute() else (base / q))
        return out
    return [p]


def label_path(img: Path) -> Path:
    parts = list(img.parts)
    for i in range(len(parts) - 1, -1, -1):  # last 'images' component
        if parts[i] == "images":
            parts[i] = "labels"
            break
    return Path(*parts).with_suffix(".txt")


def load_labels(img: Path, w: int, h: int) -> tuple[np.ndarray, np.ndarray]:
    """Return (classes[N], boxes_xyxy[N,4]) in pixels; empty arrays if no label file."""
    lp = label_path(img)
    cls, boxes = [], []
    if lp.exists():
        for line in lp.read_text().splitlines():
            v = line.split()
            if len(v) < 5:
                continue
            c, x, y, bw, bh = int(float(v[0])), *map(float, v[1:5])
            cls.append(c)
            boxes.append([(x - bw / 2) * w, (y - bh / 2) * h, (x + bw / 2) * w, (y + bh / 2) * h])
    return np.array(cls, dtype=int), np.array(boxes, dtype=float).reshape(-1, 4)


def box_iou(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    tl = np.maximum(a[:, None, :2], b[None, :, :2])
    br = np.minimum(a[:, None, 2:], b[None, :, 2:])
    inter = np.clip(br - tl, 0, None).prod(2)
    area_a = (a[:, 2:] - a[:, :2]).prod(1)
    area_b = (b[:, 2:] - b[:, :2]).prod(1)
    return inter / (area_a[:, None] + area_b[None, :] - inter + 1e-9)


def match(pred_cls, pred_box, gt_cls, gt_box) -> np.ndarray:
    """Greedy one-to-one matching (same as ultralytics validator). Returns bool [P, 10] (IoU .5:.95)."""
    correct = np.zeros((len(pred_cls), len(IOUV)), dtype=bool)
    if len(pred_cls) == 0 or len(gt_cls) == 0:
        return correct
    iou = box_iou(gt_box, pred_box) * (gt_cls[:, None] == pred_cls[None, :])
    for i, t in enumerate(IOUV):
        m = np.argwhere(iou >= t)  # [gt, pred]
        if len(m):
            m = m[np.argsort(-iou[m[:, 0], m[:, 1]])]
            m = m[np.unique(m[:, 1], return_index=True)[1]]
            m = m[np.unique(m[:, 0], return_index=True)[1]]
            correct[m[:, 1], i] = True
    return correct


def prf(tp: int, n_pred: int, n_gt: int) -> dict:
    p = tp / n_pred if n_pred else 0.0
    r = tp / n_gt if n_gt else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4), "tp": tp, "n_pred": n_pred, "n_gt": n_gt}


def evaluate(model_path: str, images: list[Path], device: str, conf: float, imgsz: int, batch: int) -> dict:
    from ultralytics import YOLO

    model = YOLO(model_path, task="detect")
    cmap = {int(k): canonical(v) for k, v in model.names.items()}
    supported = sorted({c for c in cmap.values() if c is not None})

    stats = {"tp": [], "conf": [], "pcls": [], "tcls": []}
    tp25 = np.zeros(4, int)
    np25 = np.zeros(4, int)
    t0 = time.perf_counter()
    for i in range(0, len(images), batch):
        chunk = [str(p) for p in images[i : i + batch]]
        for path, res in zip(chunk, model.predict(chunk, imgsz=imgsz, conf=0.001, iou=0.7, device=device,
                                                  verbose=False, max_det=300)):
            h, w = res.orig_shape
            gcls, gbox = load_labels(Path(path), w, h)
            b = res.boxes
            raw_cls = b.cls.cpu().numpy().astype(int)
            pcls = np.array([cmap.get(c) if cmap.get(c) is not None else -1 for c in raw_cls], dtype=int)
            keep = pcls >= 0
            pcls, pconf, pbox = pcls[keep], b.conf.cpu().numpy()[keep], b.xyxy.cpu().numpy()[keep]
            stats["tp"].append(match(pcls, pbox, gcls, gbox))
            stats["conf"].append(pconf)
            stats["pcls"].append(pcls)
            stats["tcls"].append(gcls)
            hi = pconf >= conf  # operating point for P/R/F1: re-match the filtered set
            c50 = match(pcls[hi], pbox[hi], gcls, gbox)[:, 0]
            for c in range(4):
                np25[c] += int((pcls[hi] == c).sum())
                tp25[c] += int(c50[pcls[hi] == c].sum())
    elapsed = time.perf_counter() - t0

    tp = np.concatenate(stats["tp"]) if stats["tp"] else np.zeros((0, 10), bool)
    cf = np.concatenate(stats["conf"]) if stats["conf"] else np.zeros(0)
    pc = np.concatenate(stats["pcls"]) if stats["pcls"] else np.zeros(0, int)
    tc = np.concatenate(stats["tcls"]) if stats["tcls"] else np.zeros(0, int)
    ngt = np.bincount(tc, minlength=4)[:4]

    ap50 = {c: 0.0 for c in range(4)}
    ap5095 = {c: 0.0 for c in range(4)}
    if len(tc):
        from ultralytics.utils.metrics import ap_per_class

        out = ap_per_class(tp, cf, pc, tc, names={i: n for i, n in enumerate(CLASSES)})
        ap, ucls = out[5], out[6]
        for j, c in enumerate(ucls):
            ap50[int(c)] = float(ap[j, 0])
            ap5095[int(c)] = float(ap[j].mean())

    per_class = {}
    for c, name in enumerate(CLASSES):
        d = prf(int(tp25[c]), int(np25[c]), int(ngt[c]))
        d.update(map50=round(ap50[c], 4), map50_95=round(ap5095[c], 4), supported=c in supported)
        per_class[name] = d
    present = [c for c in range(4) if ngt[c] > 0]
    overall = prf(int(tp25.sum()), int(np25.sum()), int(ngt.sum()))
    overall["map50"] = round(float(np.mean([ap50[c] for c in present])) if present else 0.0, 4)
    overall["map50_95"] = round(float(np.mean([ap5095[c] for c in present])) if present else 0.0, 4)
    overall["map_classes"] = [CLASSES[c] for c in present]
    return {
        "model": model_path,
        "model_names": {int(k): v for k, v in model.names.items()},
        "overall": overall,
        "per_class": per_class,
        "ms_per_image": round(1000 * elapsed / max(len(images), 1), 1),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--images", nargs="+", required=True, help="val list .txt / image dir (one entry per dataset)")
    ap.add_argument("--models", nargs="+", required=True, help=".pt or .onnx detectors")
    ap.add_argument("--device", default="cpu", help="default cpu (GPU is reserved for training)")
    ap.add_argument("--conf", type=float, default=0.25, help="conf threshold for P/R/F1")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="random subsample N images per dataset (seed 0)")
    ap.add_argument("--note", default="", help="free-text caveats stored in the JSON")
    ap.add_argument("--out", default=str(OUT_DIR / "compare.json"))
    args = ap.parse_args()
    if str(args.device).lower() == "cpu":
        import os

        os.environ["CUDA_VISIBLE_DEVICES"] = ""  # hard guard: never touch the GPU (it is training)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "device": args.device, "conf": args.conf, "iou_match": 0.5, "imgsz": args.imgsz,
        "limit": args.limit or None, "classes": CLASSES,
        "notes": "P/R/F1 at conf>=%.2f, IoU 0.5; mAP50 via ultralytics ap_per_class on conf>=0.001 preds, "
                 "mean over classes present in the ground truth." % args.conf,
        "caveats": args.note, "datasets": {}, "results": [],
    }
    for src in args.images:
        imgs = list_images(src)
        if args.limit and len(imgs) > args.limit:
            imgs = sorted(random.Random(0).sample(imgs, args.limit))
        ds = Path(src).stem
        gt_counts = np.zeros(4, int)
        for p in imgs:
            c, _ = load_labels(p, 1, 1)
            gt_counts += np.bincount(c, minlength=4)[:4]
        entry = {"source": str(src), "n_images": len(imgs),
                 "n_instances": {CLASSES[c]: int(gt_counts[c]) for c in range(4)}, "models": {}}
        for m in args.models:
            print(f"[{ds}] {m} on {len(imgs)} images ({args.device}) ...", flush=True)
            r = evaluate(m, imgs, args.device, args.conf, args.imgsz, args.batch)
            name = f"{Path(m).parent.name}/{Path(m).name}"
            entry["models"][name] = r
            report["results"].append({"dataset": ds, "model": name, **r["overall"], "ms_per_image": r["ms_per_image"]})
        report["datasets"][ds] = entry

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    hdr = f"{'dataset':<10} {'model':<34} {'class':<19} {'P':>6} {'R':>6} {'F1':>6} {'mAP50':>6} {'GT':>5}"
    print("\n" + hdr + "\n" + "-" * len(hdr))
    for ds, e in report["datasets"].items():
        for mname, r in e["models"].items():
            rows = [("ALL", r["overall"])] + list(r["per_class"].items())
            for cname, d in rows:
                if cname != "ALL" and d["n_gt"] == 0 and d["n_pred"] == 0:
                    continue
                print(f"{ds:<10} {mname[-34:]:<34} {cname:<19} {d['precision']:>6.3f} {d['recall']:>6.3f} "
                      f"{d['f1']:>6.3f} {d['map50']:>6.3f} {d['n_gt']:>5}")
            print(f"{'':<10} {'':<34} {'(ms/img %.0f)' % r['ms_per_image']}")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
