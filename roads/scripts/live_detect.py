"""Real-time road-damage detection demo (webcam / RTSP dashcam stream / video file), CPU friendly.

  python scripts/live_detect.py --source 0                       # webcam 0, window, press q to quit
  python scripts/live_detect.py --source rtsp://cam/stream --save-dir runs/live --lat 12.97 --lon 77.64
  python scripts/live_detect.py --source clip.mp4 --no-window --max-frames 100   # headless test

Model: --model, else backend/models/roadsense_y26n.onnx -> roadsense_y26n.pt -> rdd_yolov8s.pt.
Every frame is tracked with ByteTrack, so each physical defect keeps one ID and is counted once
(after it has been seen in --min-frames frames). With --save-dir, each unique track's best frame
(faces / number plates blurred unless --no-blur) is written as track_<id>.jpg when the track ends,
plus one JSON line in detections.jsonl: track_id, class, confidence, time, lat/lon.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from backend.app import detector  # noqa: E402  (class names, colours, box parsing, drawing, privacy hook)

MODELS = [detector.MODELS_DIR / n for n in ("roadsense_y26n.onnx", "roadsense_y26n.pt", "rdd_yolov8s.pt")]


def pick_model(arg: str | None) -> Path:
    if arg:
        p = Path(arg)
        if not p.exists():
            raise SystemExit(f"model not found: {p}")
        return p
    for p in MODELS:
        if p.exists():
            return p
    raise SystemExit("no model found in backend/models (roadsense_y26n.onnx / roadsense_y26n.pt / rdd_yolov8s.pt)")


def overlay(vis, fps: float, unique: int, active: int):
    text = f"FPS {fps:4.1f} | unique defects {unique} | in view {active}"
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)
    cv2.rectangle(vis, (8, 8), (tw + 24, th + 26), (20, 20, 20), -1)
    cv2.putText(vis, text, (16, th + 16), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="0", help="webcam index, rtsp:// / http:// URL, or video file")
    ap.add_argument("--model", help="model file (.onnx / .pt); default: best available in backend/models")
    ap.add_argument("--conf", type=float, default=detector.CONF)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--width", type=int, default=960, help="resize frames to this width before detection (0 = keep)")
    ap.add_argument("--stride", type=int, default=1, help="process every Nth frame")
    ap.add_argument("--min-frames", type=int, default=3, help="frames a track must be seen before it counts")
    ap.add_argument("--lost-frames", type=int, default=30, help="processed frames without a track before it is saved")
    ap.add_argument("--save-dir", help="save each unique track's best frame + detections.jsonl here")
    ap.add_argument("--lat", type=float, help="fixed latitude written to the JSON lines")
    ap.add_argument("--lon", type=float, help="fixed longitude written to the JSON lines")
    ap.add_argument("--no-blur", action="store_true", help="do not blur faces / number plates in saved frames")
    ap.add_argument("--no-window", action="store_true", help="headless: no OpenCV window, print stats")
    ap.add_argument("--max-frames", type=int, default=0, help="stop after N processed frames (0 = until the end)")
    a = ap.parse_args()

    from ultralytics import YOLO
    mp = pick_model(a.model)
    model = YOLO(str(mp), task="detect")
    src = int(a.source) if a.source.isdigit() else a.source
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        print(f"cannot open source {a.source!r}")
        return 2
    live = isinstance(src, int) or "://" in str(src)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 0
    print(f"model {mp.name} | device {a.device} | source {a.source} ({'live' if live else 'file'}, "
          f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))} @ {src_fps:.0f} fps)")

    save_dir = Path(a.save_dir) if a.save_dir else None
    jsonl = None
    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)
        jsonl = open(save_dir / "detections.jsonl", "a", encoding="utf-8")
    redact = None
    if save_dir and not a.no_blur and detector._privacy is not None:
        redact = detector._privacy.redact

    tracks: dict[int, dict] = {}  # id -> {frames, best_score, best, classes, last_seen, counted}
    unique = saved = raw = 0
    by_class: dict[str, int] = {}

    def flush(tid: int):
        """Write a finished track's best frame + JSON line."""
        nonlocal saved
        tr = tracks.pop(tid)
        if not tr["counted"] or save_dir is None:
            return
        b = tr["best"]
        cls = max(tr["classes"], key=tr["classes"].get)
        frame = b["frame"]
        if redact is not None:
            try:
                frame = redact(frame)
            except Exception as e:  # noqa: BLE001
                print(f"privacy blur failed, frame of track {tid} not saved: {e}")
                frame = None
        name = None
        if frame is not None:
            name = f"track_{tid:05d}.jpg"
            vis = detector.draw(frame, [{**b, "damage_class": cls}],
                                lambda d: f'#{tid} {detector.CLASS_LABELS.get(cls, cls)} {d["confidence"]:.2f}')
            ok, buf = cv2.imencode(".jpg", vis, [cv2.IMWRITE_JPEG_QUALITY, 88])
            if ok:
                buf.tofile(str(save_dir / name))
        jsonl.write(json.dumps({
            "track_id": tid, "class": cls, "confidence": round(b["confidence"], 3),
            "time": b["time"], "video_t": round(b["t"], 2), "frames_seen": tr["frames"],
            "bbox": [round(v, 1) for v in b["bbox"]], "lat": a.lat, "lon": a.lon,
            "frame": name, "source": str(a.source), "model": mp.name}) + "\n")
        jsonl.flush()
        saved += 1

    idx = processed = 0
    ema = 0.0
    infer_s = 0.0
    t_start = time.perf_counter()
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            idx += 1
            if a.stride > 1 and (idx - 1) % a.stride:
                continue
            t0 = time.perf_counter()
            if a.width and frame.shape[1] > a.width:
                s = a.width / frame.shape[1]
                frame = cv2.resize(frame, (a.width, int(frame.shape[0] * s)), interpolation=cv2.INTER_AREA)
            r = model.track(frame, persist=True, tracker="bytetrack.yaml", conf=a.conf, imgsz=a.imgsz,
                            device=a.device, verbose=False)[0]
            infer_s += time.perf_counter() - t0
            dets = [d for d in detector._boxes(r) if d["track_id"] is not None]
            raw += len(dets)
            processed += 1
            video_t = (idx - 1) / src_fps if (src_fps and not live) else time.perf_counter() - t_start
            for d in dets:
                tr = tracks.setdefault(d["track_id"], {"frames": 0, "best_score": -1.0, "best": None,
                                                       "classes": {}, "last_seen": 0, "counted": False})
                tr["frames"] += 1
                tr["last_seen"] = processed
                tr["classes"][d["damage_class"]] = tr["classes"].get(d["damage_class"], 0.0) + d["confidence"]
                x1, y1, x2, y2 = d["bbox"]
                score = d["confidence"] * np.sqrt(max((x2 - x1) * (y2 - y1), 0))  # confident and close
                if score > tr["best_score"]:
                    tr["best_score"] = score
                    tr["best"] = {**d, "t": video_t, "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                  "frame": frame.copy() if save_dir else None}
                if not tr["counted"] and tr["frames"] >= a.min_frames:
                    tr["counted"] = True
                    unique += 1
                    cls = max(tr["classes"], key=tr["classes"].get)
                    by_class[cls] = by_class.get(cls, 0) + 1
            for tid in [t for t, tr in tracks.items() if processed - tr["last_seen"] > a.lost_frames]:
                flush(tid)

            dt = time.perf_counter() - t0
            ema = 1.0 / dt if ema == 0 else 0.9 * ema + 0.1 / max(dt, 1e-6)
            if not a.no_window:
                vis = detector.draw(frame, dets, lambda d: f'#{d["track_id"]} '
                                    f'{detector.CLASS_LABELS.get(d["damage_class"], d["damage_class"])} {d["confidence"]:.2f}')
                overlay(vis, ema, unique, len(dets))
                cv2.imshow("Nagrik Roads live (q to quit)", vis)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
            elif processed % 25 == 0:
                print(f"frame {processed:5d} | fps {ema:5.1f} | unique {unique} | in view {len(dets)}", flush=True)
            if a.max_frames and processed >= a.max_frames:
                break
    except KeyboardInterrupt:
        pass
    finally:
        wall = time.perf_counter() - t_start
        cap.release()
        for tid in list(tracks):
            flush(tid)
        if jsonl:
            jsonl.close()
        if not a.no_window:
            cv2.destroyAllWindows()

    print(f"done: {processed} frames in {wall:.1f} s -> {processed / max(wall, 1e-6):.1f} FPS end-to-end "
          f"(inference only {processed / max(infer_s, 1e-6):.1f} FPS) | raw detections {raw} | "
          f"unique defects {unique} {by_class or ''}"
          + (f" | saved {saved} to {save_dir}" if save_dir else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
