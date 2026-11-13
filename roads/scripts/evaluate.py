"""Raw detections vs unique defects on a video, counted the same way as the app (detector.FrameLinker).

Usage (from repo root):
    .venv\\Scripts\\python.exe scripts/evaluate.py --video dataset/sample/sample_video.mp4 \
        --model backend/models/roadsense_y26s.pt [--compare-bytetrack]

A unique defect is a linked track seen in >= --min-frames analysed frames (default 2) or once at
confidence >= --solo-conf (default 0.5); its class is the confidence-weighted vote over its frames.
--compare-bytetrack also counts ByteTrack tracks of >= 3 frames (the previous method) on the same frames.
Output -> backend/data/eval/tracking_<model-stem>.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / "backend" / "data" / "eval"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", required=True)
    ap.add_argument("--model", required=True, help=".pt or .onnx")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--conf", type=float, default=0.20)
    ap.add_argument("--stride", type=int, default=2, help="analyse every n-th frame (the app uses 2)")
    ap.add_argument("--min-frames", type=int, default=2)
    ap.add_argument("--solo-conf", type=float, default=0.5)
    ap.add_argument("--compare-bytetrack", action="store_true")
    ap.add_argument("--note", default="", help="free-text note stored in the JSON")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    import cv2
    from ultralytics import YOLO

    from backend.app.detector import FrameLinker, _boxes

    model = YOLO(args.model, task="detect")
    tracker_model = YOLO(args.model, task="detect") if args.compare_bytetrack else None
    linker = FrameLinker()
    tracks: dict[int, dict] = defaultdict(lambda: {"frames": 0, "votes": Counter(), "first": None, "last": None,
                                                    "max_conf": 0.0})
    bytetrack_frames: Counter = Counter()
    raw, per_frame, raw_by_class = 0, [], Counter()

    cap = cv2.VideoCapture(args.video)
    video_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    t0, idx, step = time.perf_counter(), 0, 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % args.stride == 0:
            dets = linker.update(step, _boxes(model.predict(frame, conf=args.conf, device=args.device, verbose=False)[0]))
            raw += len(dets)
            per_frame.append(len(dets))
            for d in dets:
                raw_by_class[d["damage_class"]] += 1
                t = tracks[d["track_id"]]
                t["frames"] += 1
                t["votes"][d["damage_class"]] += d["confidence"]
                t["first"] = idx if t["first"] is None else t["first"]
                t["last"] = idx
                t["max_conf"] = max(t["max_conf"], d["confidence"])
            if tracker_model is not None:
                r = tracker_model.track(frame, persist=True, tracker="bytetrack.yaml", conf=args.conf,
                                        device=args.device, verbose=False)[0]
                if r.boxes.id is not None:
                    bytetrack_frames.update(int(i) for i in r.boxes.id.tolist())
            step += 1
        idx += 1
    cap.release()
    elapsed = time.perf_counter() - t0

    unique = {tid: t for tid, t in tracks.items() if t["frames"] >= args.min_frames or t["max_conf"] >= args.solo_conf}
    unique_by_class = Counter(t["votes"].most_common(1)[0][0] for t in unique.values())
    out = Path(args.out) if args.out else OUT_DIR / f"tracking_{Path(args.model).stem}.json"
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "video": str(args.video),
        "model": str(args.model),
        "device": args.device,
        "tracker": "FrameLinker",
        "conf": args.conf,
        "stride": args.stride,
        "min_frames": args.min_frames,
        "solo_conf": args.solo_conf,
        "video_fps": round(video_fps, 2),
        "resolution": [width, height],
        "frames": idx,
        "frames_analysed": step,
        "duration_s": round(idx / video_fps, 2) if video_fps else None,
        "raw_detections": raw,
        "avg_detections_per_frame": round(raw / max(step, 1), 2),
        "max_detections_in_frame": max(per_frame) if per_frame else 0,
        "track_ids_total": len(tracks),
        "unique_defects": len(unique),
        "dedup_ratio": round(raw / max(len(unique), 1), 1),
        "raw_by_class": dict(raw_by_class),
        "unique_by_class": dict(unique_by_class),
        "bytetrack_unique_3_frames": (sum(1 for n in bytetrack_frames.values() if n >= 3)
                                      if tracker_model is not None else None),
        "processing_fps": round(step / elapsed, 2) if elapsed else None,
        "processing_s": round(elapsed, 1),
        "tracks": [{"id": tid, "class": t["votes"].most_common(1)[0][0], "frames": t["frames"],
                    "first_frame": t["first"], "last_frame": t["last"], "max_conf": round(t["max_conf"], 3)}
                   for tid, t in sorted(unique.items())],
        "note": args.note,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"frames {idx} (analysed {step}) | raw detections {raw} | unique defects {len(unique)} | "
          f"{report['processing_fps']} analysed fps on {args.device}")
    if tracker_model is not None:
        print("ByteTrack (>= 3 frames) on the same frames:", report["bytetrack_unique_3_frames"])
    print("unique by class:", dict(unique_by_class))
    print("wrote", out)


if __name__ == "__main__":
    main()
