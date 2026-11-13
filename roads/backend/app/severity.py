"""Severity estimation: damage type + visible size + (optional) monocular depth.

Depth uses Depth-Anything-V2-Small. It gives *relative* depth, so we report a
0-1 "depth index" (how much deeper the defect is than the surrounding road),
not centimetres. OFF by default (heavy); enable with ROADSENSE_DEPTH=1.
"""
import os

import numpy as np

CLASS_WEIGHT = {"pothole": 1.0, "alligator_crack": 0.8, "transverse_crack": 0.55, "longitudinal_crack": 0.45}
USE_DEPTH = os.environ.get("ROADSENSE_DEPTH", "0") == "1"
_depth = None
_depth_failed = False


def _depth_model():
    global _depth, _depth_failed
    if _depth is None and not _depth_failed:
        try:
            from transformers import pipeline
            _depth = pipeline("depth-estimation", model="depth-anything/Depth-Anything-V2-Small-hf",
                              device=-1)  # always CPU (GPU reserved for training)
        except Exception as e:  # offline / no weights: keep working without depth
            print(f"[severity] depth model unavailable: {e}")
            _depth_failed = True
    return _depth


def depth_index(img_bgr: np.ndarray, bbox) -> float | None:
    if not USE_DEPTH:
        return None
    pipe = _depth_model()
    if pipe is None:
        return None
    from PIL import Image
    h, w = img_bgr.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in bbox]
    bw, bh = x2 - x1, y2 - y1
    # crop with a margin ring of surrounding road
    mx, my = int(bw * 0.4) + 4, int(bh * 0.4) + 4
    X1, Y1, X2, Y2 = max(0, x1 - mx), max(0, y1 - my), min(w, x2 + mx), min(h, y2 + my)
    crop = img_bgr[Y1:Y2, X1:X2][:, :, ::-1]
    if crop.size == 0:
        return None
    d = np.array(pipe(Image.fromarray(crop))["depth"], dtype=np.float32)  # larger = closer
    inner = np.zeros(d.shape, bool)
    inner[y1 - Y1:y2 - Y1, x1 - X1:x2 - X1] = True
    if inner.sum() < 10 or (~inner).sum() < 10:
        return None
    ring = np.median(d[~inner])
    hole = np.percentile(d[inner], 15)  # deepest part of the defect = farthest from camera
    spread = np.percentile(d, 95) - np.percentile(d, 5) + 1e-6
    return float(np.clip((ring - hole) / spread, 0, 1))


def severity(damage_class, bbox, frame_w, frame_h, depth=None):
    x1, y1, x2, y2 = bbox
    area_ratio = max(0.0, (x2 - x1) * (y2 - y1) / float(frame_w * frame_h))
    # perspective: defects near the top of the frame are far away and look smaller
    cy = (y1 + y2) / 2 / frame_h
    persp = 1.0 + 1.2 * max(0.0, 0.75 - cy)
    size_term = min(1.0, (area_ratio * persp) / 0.06)
    w = CLASS_WEIGHT.get(damage_class, 0.5)
    score = 100 * w * (0.35 + 0.65 * size_term)
    if depth is not None:
        score = 0.75 * score + 25 * depth * (1.0 if damage_class == "pothole" else 0.6)
    score = float(np.clip(score, 0, 100))
    label = "High" if score >= 60 else "Medium" if score >= 35 else "Low"
    return {"area_ratio": area_ratio, "severity_score": round(score, 1), "severity_label": label}
