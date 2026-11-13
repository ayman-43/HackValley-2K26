"""Road-damage detection (YOLO) + ByteTrack video tracking.

Model selection (first that exists): env ROADSENSE_MODEL -> backend/models/roadsense_y26n.pt
-> backend/models/rdd_yolov8s.pt -> reference/pothole-pilot/ai_model/weights.onnx
(RDD YOLOv8s is preferred over the Pothole Pilot ONNX: F1 0.66 vs 0.02 on RDD India).
Device comes from env ROADSENSE_DEVICE (default "cpu").
"""
import logging
import os
import threading
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("roadsense.detector")

try:  # face / number-plate blurring (backend/app/privacy.py)
    from . import privacy as _privacy
except Exception:  # pragma: no cover
    _privacy = None

ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = ROOT / "backend" / "models"
CANDIDATES = [
    MODELS_DIR / "roadsense_y26s.pt",  # Kaggle T4, 148 epochs: BharatPotHole F1 0.68 (y26n: 0.49)
    MODELS_DIR / "roadsense_y26n.pt",
    MODELS_DIR / "rdd_yolov8s.pt",
    ROOT / "reference" / "pothole-pilot" / "ai_model" / "weights.onnx",
]
DEVICE = os.environ.get("ROADSENSE_DEVICE", "cpu")
# 0.20 maximises F1 on the Indian validation sets for roadsense_y26s at imgsz 640
# (scripts/tune_detector.py -> backend/data/eval/tuning_y26s.json; for roadsense_y26n the best was 0.15)
CONF = float(os.environ.get("ROADSENSE_CONF", 0.20))

# Combined detector for uploads (photos and videos; live scan stays single-model for speed):
# potholes from the main model, cracks from a second crack model (rdd_yolov8s, much better on cracks).
# scripts/eval_combo.py -> backend/data/eval/combo.json: RDD India F1 0.501 -> 0.589 (transverse cracks 0 -> 0.60),
# BharatPotHole 0.494 -> 0.485. Turn off with ROADSENSE_COMBINE=0.
CRACK_CLASSES = {"longitudinal_crack", "transverse_crack", "alligator_crack"}
CRACK_MODEL = Path(os.environ.get("ROADSENSE_CRACK_MODEL", str(MODELS_DIR / "rdd_yolov8s.pt")))
CRACK_CONF = float(os.environ.get("ROADSENSE_CRACK_CONF", 0.35))
CRACK_TRACK_OFFSET = 1_000_000  # crack track IDs live in their own range so they never collide with pothole IDs

# Canonical classes (RDD2022 / CRDDC2022 labels D00, D10, D20, D40)
CLASSES = ["longitudinal_crack", "transverse_crack", "alligator_crack", "pothole"]
CLASS_LABELS = {
    "longitudinal_crack": "Longitudinal crack",
    "transverse_crack": "Transverse crack",
    "alligator_crack": "Alligator crack",
    "pothole": "Pothole",
}
CLASS_COLORS = {  # BGR for OpenCV drawing
    "longitudinal_crack": (230, 160, 40),
    "transverse_crack": (200, 90, 220),
    "alligator_crack": (40, 170, 255),
    "pothole": (60, 60, 235),
}

_model = None
_lock = threading.Lock()


def model_path() -> Path:
    """First existing model file according to the selection order."""
    env = os.environ.get("ROADSENSE_MODEL")
    if env and Path(env).exists():
        return Path(env)
    for p in CANDIDATES:
        if p.exists():
            return p
    raise RuntimeError("No detection model found (set ROADSENSE_MODEL or add backend/models/roadsense_y26n.pt)")


def model_info() -> dict:
    try:
        p = model_path()
        return {"name": p.name, "path": str(p), "device": str(DEVICE)}
    except RuntimeError as e:
        return {"name": None, "error": str(e), "device": str(DEVICE)}


def _load():
    from ultralytics import YOLO
    p = model_path()
    log.info("loading model %s on %s", p, DEVICE)
    return YOLO(str(p), task="detect")


def model():
    """Shared model for single-image prediction."""
    global _model
    if _model is None:
        _model = _load()
    return _model


_crack_model = None


def combine_enabled() -> bool:
    return (os.environ.get("ROADSENSE_COMBINE", "1") != "0" and CRACK_MODEL.exists()
            and CRACK_MODEL.resolve() != model_path().resolve())


def _load_crack():
    from ultralytics import YOLO
    log.info("loading crack model %s on %s", CRACK_MODEL, DEVICE)
    return YOLO(str(CRACK_MODEL), task="detect")


def crack_model():
    global _crack_model
    if _crack_model is None:
        _crack_model = _load_crack()
    return _crack_model


def _merge(main_dets: list[dict], crack_dets: list[dict]) -> list[dict]:
    """Potholes from the main model, cracks (conf >= CRACK_CONF) from the crack model."""
    return ([d for d in main_dets if d["damage_class"] not in CRACK_CLASSES]
            + [d for d in crack_dets if d["damage_class"] in CRACK_CLASSES and d["confidence"] >= CRACK_CONF])


def canonical(name: str) -> str:
    n = str(name).lower()
    if "pot" in n or n.startswith("d40"):
        return "pothole"
    if "alligator" in n or "aligator" in n or n.startswith("d20"):
        return "alligator_crack"
    if "transverse" in n or n.startswith("d10"):
        return "transverse_crack"
    if "longitudinal" in n or n.startswith("d00"):
        return "longitudinal_crack"
    return n.replace(" ", "_")


def _boxes(result):
    out = []
    names = result.names
    b = result.boxes
    if b is None or len(b) == 0:
        return out
    xyxy = b.xyxy.cpu().numpy()
    conf = b.conf.cpu().numpy()
    cls = b.cls.cpu().numpy().astype(int)
    ids = b.id.cpu().numpy().astype(int) if b.id is not None else [None] * len(cls)
    for box, c, k, tid in zip(xyxy, conf, cls, ids):
        out.append({
            "bbox": [float(v) for v in box],
            "confidence": float(c),
            "damage_class": canonical(names[int(k)]),
            "track_id": None if tid is None else int(tid),
        })
    return out


def detect_image(img: np.ndarray) -> list[dict]:
    """Detections on one BGR image: [{bbox, confidence, damage_class, track_id=None}]."""
    with _lock:
        r = model().predict(img, conf=CONF, device=DEVICE, verbose=False)[0]
        dets = _boxes(r)
        if combine_enabled():
            rc = crack_model().predict(img, conf=min(CONF, CRACK_CONF), device=DEVICE, verbose=False)[0]
            dets = _merge(dets, _boxes(rc))
    return dets


def draw(img, dets, label_fn=None):
    out = img.copy()
    h = out.shape[0]
    th = max(1, h // 400)
    for d in dets:
        x1, y1, x2, y2 = map(int, d["bbox"])
        color = CLASS_COLORS.get(d["damage_class"], (0, 255, 0))
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2 * th)
        text = label_fn(d) if label_fn else f'{CLASS_LABELS.get(d["damage_class"], d["damage_class"])} {d["confidence"]:.2f}'
        fs = 0.5 * th
        (tw, tht), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, fs, th)
        y0 = max(y1, tht + 6)
        cv2.rectangle(out, (x1, y0 - tht - 6), (x1 + tw + 6, y0), color, -1)
        cv2.putText(out, text, (x1 + 3, y0 - 4), cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), th, cv2.LINE_AA)
    return out


def _writer(path_base: Path, fps, size):
    """Browser-playable video writer: try H.264 mp4, then VP8 webm, then mp4v."""
    for fourcc, ext in (("avc1", ".mp4"), ("VP80", ".webm"), ("mp4v", ".mp4")):
        p = path_base.with_suffix(ext)
        w = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*fourcc), fps, size)
        if w.isOpened():
            return w, p
        w.release()
    raise RuntimeError("No usable video codec")


def track_video(video_path: str, out_base: Path, stride: int = 2, min_frames: int = 3,
                line_frac: float = 0.55, progress_cb=None) -> dict:
    """Track every defect across frames with ByteTrack.

    Each physical defect keeps one track ID, so it is counted once no matter how
    many frames it appears in. Tracks seen in fewer than `min_frames` processed
    frames are discarded as flicker. A counting line at `line_frac` of frame
    height gives the "counted once when it crosses the line" view; every unique
    track carries `crossed_line`. Returns the best (confident + large) frame per track.
    A fresh model instance is used per video so ByteTrack IDs/state never leak between videos.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    scale = min(1.0, 1280 / max(W, 1))
    size = (int(W * scale), int(H * scale))
    writer, out_path = _writer(out_base, max(1, fps / stride), size)

    tracks = defaultdict(lambda: {"frames": 0, "best": None, "best_score": -1, "classes": defaultdict(float),
                                  "first_t": None, "last_t": None})
    raw = 0
    counted = set()
    line_y = int(size[1] * line_frac)
    m = _load()  # fresh tracker state for this video
    mc = _load_crack() if combine_enabled() else None  # own ByteTrack state for cracks
    crack_dets: list[dict] = []
    idx = 0
    processed = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % stride:
                idx += 1
                continue
            if scale != 1.0:
                frame = cv2.resize(frame, size)
            t = idx / fps
            r = m.track(frame, persist=True, tracker="bytetrack.yaml", conf=CONF, device=DEVICE, verbose=False)[0]
            if _privacy is not None:  # tracked on the original; stored best frames + video are redacted
                frame = _privacy.redact(frame)
            dets = _boxes(r)
            if mc is not None:
                if processed % 2 == 0:  # cracks do not move: every other analysed frame is enough
                    rc = mc.track(frame, persist=True, tracker="bytetrack.yaml", conf=min(CONF, CRACK_CONF),
                                  device=DEVICE, verbose=False)[0]
                    crack_dets = [{**d, "track_id": None if d["track_id"] is None else d["track_id"] + CRACK_TRACK_OFFSET}
                                  for d in _boxes(rc)]
                    dets = _merge(dets, crack_dets)
                else:
                    dets = [d for d in dets if d["damage_class"] not in CRACK_CLASSES]
            raw += len(dets)
            for d in dets:
                if d["track_id"] is None:
                    continue
                tr = tracks[d["track_id"]]
                tr["frames"] += 1
                tr["classes"][d["damage_class"]] += d["confidence"]
                tr["first_t"] = t if tr["first_t"] is None else tr["first_t"]
                tr["last_t"] = t
                x1, y1, x2, y2 = d["bbox"]
                area = (x2 - x1) * (y2 - y1)
                score = d["confidence"] * np.sqrt(max(area, 0))  # best view: confident and large (close)
                if score > tr["best_score"]:
                    tr["best_score"] = score
                    tr["best"] = {**d, "frame": frame.copy(), "t": t}
                if tr["frames"] >= min_frames and (y1 + y2) / 2 >= line_y:
                    counted.add(d["track_id"])

            vis = draw(frame, [d for d in dets if d["track_id"] is not None],
                       lambda d: f'#{d["track_id"]} {CLASS_LABELS.get(d["damage_class"], "")} {d["confidence"]:.2f}')
            cv2.line(vis, (0, line_y), (size[0], line_y), (0, 255, 255), 2)
            cv2.rectangle(vis, (8, 8), (330, 44), (20, 20, 20), -1)
            cv2.putText(vis, f"Unique defects counted: {len(counted)}", (16, 33), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                        (255, 255, 255), 2, cv2.LINE_AA)
            writer.write(vis)
            processed += 1
            idx += 1
            if progress_cb and total:  # frame count can be wrong (phone WebM): estimate only, never stop early
                progress_cb(min(idx / total, 0.99))
    finally:
        cap.release()
        writer.release()
        for mm in (m, mc):
            try:
                for trk in getattr(getattr(mm, "predictor", None), "trackers", []) or []:
                    trk.reset()
            except Exception:
                pass

    uniques = []
    for tid, tr in tracks.items():
        if tr["frames"] < min_frames or tr["best"] is None:
            continue
        cls = max(tr["classes"], key=tr["classes"].get)  # confidence-weighted majority vote
        b = tr["best"]
        uniques.append({**b, "damage_class": cls, "track_id": tid, "frames_seen": tr["frames"],
                        "first_t": tr["first_t"], "last_t": tr["last_t"], "crossed_line": tid in counted})
    uniques.sort(key=lambda u: u["t"])
    return {
        "uniques": uniques,
        "raw_detections": raw,
        "frames_processed": processed,
        "counted_line": len(counted),
        "out_path": out_path,
        "fps": fps,
        "duration": idx / fps if fps else 0,
    }
