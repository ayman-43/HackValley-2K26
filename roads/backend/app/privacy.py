"""Privacy redaction: blur faces and number plates before any frame is saved or shown.

Road videos are shot in public, so they contain pedestrians, riders and vehicle
number plates. Nagrik Roads keeps the road-damage evidence but anonymises people
and vehicles, in the spirit of DPDP Act 2023 data minimisation (idea from
iWatchRoad's blurring step - independent implementation).

How: a COCO-pretrained YOLO26n (backend/models/coco_yolo26n.pt, already used as
the base of our damage detector) finds persons and vehicles. We pixelate
  * person  -> head region (top ~22% of the box)
  * car/bus/truck -> lower-centre band, where front/rear plates sit
  * motorcycle    -> lower-centre band (rear plate); the rider is a person -> head blurred
Damage detection runs on the original frame first, so blurring never lowers
detection accuracy. Runs on CPU (~40-70 ms per frame at 640 px).

Env: ROADSENSE_BLUR=0 disables, ROADSENSE_PRIVACY_MODEL overrides the model path,
ROADSENSE_BLUR_CONF (default 0.30).
"""
import logging
import os
import threading
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("roadsense.privacy")

ENABLED = os.environ.get("ROADSENSE_BLUR", "1") != "0"
MODEL_PATH = Path(os.environ.get("ROADSENSE_PRIVACY_MODEL")
                  or Path(__file__).resolve().parents[1] / "models" / "coco_yolo26n.pt")
CONF = float(os.environ.get("ROADSENSE_BLUR_CONF", 0.30))
DEVICE = os.environ.get("ROADSENSE_DEVICE", "cpu")

PERSON, BICYCLE, CAR, MOTORCYCLE, BUS, TRUCK = 0, 1, 2, 3, 5, 7
# region inside the object box to blur: (x0, y0, x1, y1) as fractions of box width/height
REGIONS = {  # front/rear view (what a dashcam mostly sees)
    PERSON: (-0.05, -0.02, 1.05, 0.22),
    CAR: (0.22, 0.66, 0.78, 0.96),
    BUS: (0.28, 0.74, 0.72, 0.97),
    TRUCK: (0.28, 0.74, 0.72, 0.97),
    MOTORCYCLE: (0.22, 0.50, 0.78, 0.95),
}
# side view (box much wider than tall): plates sit at the two ends, low down
SIDE_ASPECT = {CAR: 1.9, BUS: 1.35, TRUCK: 1.35}  # rear-view buses/trucks are taller than wide
SIDE_ENDS = ((0.0, 0.70, 0.16, 0.97), (0.84, 0.70, 1.0, 0.97))

_model = None
_failed = False
_lock = threading.Lock()
stats = {"frames": 0, "regions": 0}


def _get_model():
    global _model, _failed
    if _model is None and not _failed:
        try:
            from ultralytics import YOLO
            _model = YOLO(str(MODEL_PATH), task="detect")
            log.info("privacy model loaded: %s", MODEL_PATH.name)
        except Exception as e:
            _failed = True
            log.warning("privacy blurring unavailable (%s) - frames are saved unblurred", e)
    return _model


def _pixelate(img: np.ndarray, x1: int, y1: int, x2: int, y2: int) -> None:
    roi = img[y1:y2, x1:x2]
    h, w = roi.shape[:2]
    if h < 2 or w < 2:
        return
    block = max(4, min(h, w) // 6)  # coarse blocks: unreadable plates/faces
    small = cv2.resize(roi, (max(1, w // block), max(1, h // block)), interpolation=cv2.INTER_LINEAR)
    img[y1:y2, x1:x2] = cv2.GaussianBlur(cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST), (0, 0), 3)


def regions(img: np.ndarray) -> list[tuple[int, int, int, int, str]]:
    """Sensitive regions [(x1, y1, x2, y2, kind)] in pixel coordinates."""
    m = _get_model()
    if m is None:
        return []
    H, W = img.shape[:2]
    with _lock:
        r = m.predict(img, conf=CONF, classes=list(REGIONS), imgsz=640, device=DEVICE, verbose=False)[0]
    out = []
    if r.boxes is None:
        return out
    for (bx1, by1, bx2, by2), k in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.cls.cpu().numpy().astype(int)):
        bw, bh = bx2 - bx1, by2 - by1
        if bw < 8 or bh < 8:
            continue
        side = k in SIDE_ASPECT and bw / bh >= SIDE_ASPECT[k]
        for fx0, fy0, fx1, fy1 in (SIDE_ENDS if side else (REGIONS[k],)):
            x1 = int(np.clip(bx1 + fx0 * bw, 0, W))
            y1 = int(np.clip(by1 + fy0 * bh, 0, H))
            x2 = int(np.clip(bx1 + fx1 * bw, 0, W))
            y2 = int(np.clip(by1 + fy1 * bh, 0, H))
            if k == PERSON:
                y2 = max(y2, min(H, y1 + 12))  # tiny far-away people: still cover the head
            if x2 - x1 >= 2 and y2 - y1 >= 2:
                out.append((x1, y1, x2, y2, "face" if k == PERSON else "plate"))
    return out


def redact(img: np.ndarray) -> np.ndarray:
    """Return a copy of `img` with faces and number plates pixelated (unchanged if disabled/unavailable)."""
    if not ENABLED or img is None:
        return img
    try:
        regs = regions(img)
    except Exception as e:  # never break the pipeline over privacy blur
        log.warning("redaction failed: %s", e)
        return img
    stats["frames"] += 1
    if not regs:
        return img
    out = img.copy()
    for x1, y1, x2, y2, _ in regs:
        _pixelate(out, x1, y1, x2, y2)
    stats["regions"] += len(regs)
    return out


def status() -> dict:
    return {"enabled": ENABLED, "model": MODEL_PATH.name, "available": MODEL_PATH.exists() and not _failed, **stats}
