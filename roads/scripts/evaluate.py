"""Raw detections vs unique tracked defects on a video (CPU, ByteTrack).

Usage (from repo root):
    .venv\\Scripts\\python.exe scripts/evaluate.py --video reference/pothole-pilot/ai_model/sample_video.mp4 \
        --model reference/pothole-pilot/ai_model/weights.onnx

A "unique" defect is a ByteTrack ID seen in >= --min-frames frames (default 3); its class is the
confidence-weighted vote over its frames. Output -> backend/data/eval/tracking_<model-stem>.json
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "backend" / "data" / "eval"
CLASSES = ["longitudinal_crack", "transverse_crack", "alligator_crack", "pothole"]


def canonical(name: str) -> str:
    n = str(name).lower()
    if "pot" in n or n.startswith("d40"):
        return "pothole"
    if "alligator" in n or n.startswith("d20"):
        return "alligator_crack"
    if "transverse" in n or n.startswith("d10"):
        return "transverse_crack"
    if "longitudinal" in n or n.startswith("d00"):
        return "longitudinal_crack"
    return n.replace(" ", "_")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", required=True)
    ap.add_argument("--model", required=True, help=".pt or .onnx")
    ap.add_argument("--device", default="cpu", help="default cpu (GPU is reserved for training)")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--min-frames", type=int, default=3)
    ap.add_argument("--note", default="", help="free-text note stored in the JSON")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if str(args.device).lower() == "cpu":
        import os

        os.environ["CUDA_VISIBLE_DEVICES"] = ""  # hard guard: never touch the GPU (it is training)

    import cv2
    from ultralytics import YOLO

    cap = cv2.VideoCapture(args.video)
    video_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    n_frames_meta = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    model = YOLO(args.model, task="detect")
    names = {int(k): canonical(v) for k, v in model.names.items()}

    raw = 0
    raw_by_class: Counter = Counter()
    per_frame: list[int] = []
    tracks: dict[int, dict] = defaultdict(lambda: {"frames": 0, "votes": Counter(), "first": None, "last": None,
                                                    "max_conf": 0.0})
    t0 = time.perf_counter()
    for fi, res in enumerate(model.track(args.video, stream=True, persist=True, tracker="bytetrack.yaml",
                                         device=args.device, conf=args.conf, imgsz=args.imgsz, verbose=False)):
        b = res.boxes
        n = 0 if b is None else len(b)
        raw += n
        per_frame.append(n)
        if not n:
            continue
        cls = b.cls.cpu().numpy().astype(int)
        conf = b.conf.cpu().numpy()
        for c in cls:
            raw_by_class[names.get(int(c), str(c))] += 1
        if b.id is None:
            continue
        for tid, c, s in zip(b.id.cpu().numpy().astype(int), cls, conf):
            t = tracks[int(tid)]
            t["frames"] += 1
            t["votes"][names.get(int(c), str(c))] += float(s)
            t["first"] = fi if t["first"] is None else t["first"]
            t["last"] = fi
            t["max_conf"] = max(t["max_conf"], float(s))
    elapsed = time.perf_counter() - t0
    n_frames = len(per_frame)

    unique = {tid: t for tid, t in tracks.items() if t["frames"] >= args.min_frames}
    unique_by_class = Counter(t["votes"].most_common(1)[0][0] for t in unique.values())
    stem = Path(args.model).stem
    out = Path(args.out) if args.out else OUT_DIR / f"tracking_{stem}.json"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "video": str(args.video),
        "model": str(args.model),
        "device": args.device,
        "tracker": "bytetrack.yaml",
        "conf": args.conf,
        "imgsz": args.imgsz,
        "min_frames": args.min_frames,
        "video_fps": round(video_fps, 2),
        "resolution": [width, height],
        "frames": n_frames or n_frames_meta,
        "duration_s": round((n_frames or n_frames_meta) / video_fps, 2) if video_fps else None,
        "raw_detections": raw,
        "avg_detections_per_frame": round(raw / max(n_frames, 1), 2),
        "max_detections_in_frame": max(per_frame) if per_frame else 0,
        "track_ids_total": len(tracks),
        "unique_defects": len(unique),
        "dedup_ratio": round(raw / max(len(unique), 1), 1),
        "raw_by_class": dict(raw_by_class),
        "unique_by_class": dict(unique_by_class),
        "processing_fps": round(n_frames / elapsed, 2) if elapsed else None,
        "processing_s": round(elapsed, 1),
        "tracks": [{"id": tid, "class": t["votes"].most_common(1)[0][0], "frames": t["frames"],
                    "first_frame": t["first"], "last_frame": t["last"], "max_conf": round(t["max_conf"], 3)}
                   for tid, t in sorted(unique.items())],
        "note": args.note,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"frames {report['frames']} | raw detections {raw} | track IDs {len(tracks)} | "
          f"unique (>= {args.min_frames} frames) {len(unique)} | {report['processing_fps']} fps on {args.device}")
    print("unique by class:", dict(unique_by_class))
    print("wrote", out)


if __name__ == "__main__":
    main()
