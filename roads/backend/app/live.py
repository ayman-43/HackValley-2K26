"""Live (real-time) inspection: the phone streams one frame per second with its current GPS fix.

Each frame is detected immediately; boxes go straight back to the phone for the overlay, and every
confident detection is saved as a defect/observation at the GPS position where it was seen
(same dedup as uploads, so the same pothole seen in consecutive frames merges into one defect).
Risk / forecast / Nagrik enrichment runs in a background thread so the frame reply stays fast.

    POST /api/live/start              -> {session_id}
    POST /api/live/{sid}/frame        (frame=jpeg, lat, lon, accuracy, speed, heading) -> {detections, saved, stats}
    POST /api/live/{sid}/position     (lat, lon, accuracy, speed, heading) -> real-time location only, every second
    POST /api/live/{sid}/stop         -> summary
    GET  /api/live/feed?since=<obs id> -> newest observations from live sessions (for the live map)
"""
from __future__ import annotations

import logging
import threading
import time

import cv2
import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from . import db, detector, geo, pipeline

log = logging.getLogger("roadsense.live")
router = APIRouter(prefix="/api/live", tags=["live"])

SAVE_CONF = 0.40          # one frame is less evidence than a 3-frame video track: save only confident boxes
_sessions: dict[int, dict] = {}
_stopped: set[int] = set()  # a frame still in flight after Stop must not restart the session
_lock = threading.Lock()


MIN_TRACK_FRAMES = 2      # at ~3 frames/s, a defect must be tracked in >=2 frames (drops one-frame flicker)


def _new_session() -> dict:
    return {"frames": 0, "raw": 0, "seen": set(), "new": set(), "n": 0, "enriched": {},
            "tracks": {}, "model": None, "lock": threading.Lock(),
            "linker": detector.FrameLinker(max_gap=3, reach=2.0), "step": 0}  # ~3 fps: ~1 s gap, bigger jumps


def _session(sid: int) -> dict:
    s = _sessions.get(sid)
    if s is None:
        if sid in _stopped:
            raise HTTPException(409, "live session already stopped")
        with db.connect() as conn:
            r = conn.execute("SELECT id FROM inspections WHERE id=? AND source_type='live'", (sid,)).fetchone()
        if r is None:
            raise HTTPException(404, "live session not found")
        s = _sessions.setdefault(sid, _new_session())
    if s["model"] is None:
        s["model"] = detector.model()  # shared model; each session has its own FrameLinker (IDs never leak)
    return s


@router.post("/start")
def start(lat: float | None = Form(None), lon: float | None = Form(None)):
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO inspections (created_at, source_type, filename, lat, lon, notes) VALUES (?,?,?,?,?,?)",
            (pipeline.now_iso(), "live", "live-session", lat, lon, "real-time phone session"))
        sid = cur.lastrowid
    (db.MEDIA_DIR / str(sid)).mkdir(parents=True, exist_ok=True)
    s = _sessions[sid] = _new_session()
    s["model"] = detector.model()
    _warmup(s["model"])
    return {"session_id": sid}


def _warmup(m):
    """Run the session model and the privacy model once now, so the first live frame is not slow."""
    dummy = np.zeros((320, 320, 3), np.uint8)
    try:
        m.predict(dummy, conf=detector.CONF, device=detector.DEVICE, verbose=False)
        if pipeline.privacy is not None:
            pipeline.privacy.redact(dummy)
    except Exception as e:
        log.warning("live warm-up failed: %s", e)


def _enrich_bg(ids: set[int]):
    def run():
        try:
            pipeline._enrich(set(ids), None, [])
        except Exception as e:  # never break the live loop
            log.warning("live enrichment failed: %s", e)
    threading.Thread(target=run, name="live-enrich", daemon=True).start()


@router.post("/{sid}/frame")
async def frame(sid: int, frame: UploadFile = File(...), lat: float | None = Form(None),
                lon: float | None = Form(None), accuracy: float | None = Form(None),
                speed: float | None = Form(None), heading: float | None = Form(None)):
    s = _session(sid)
    buf = np.frombuffer(await frame.read(), np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "could not decode frame")
    H, W = img.shape[:2]
    with s["lock"]:
        with detector._lock:  # the shared model (ByteTrack dropped flickering potholes; see detector.FrameLinker)
            r = s["model"].predict(img, conf=detector.CONF, device=detector.DEVICE, verbose=False)[0]
        s["step"] += 1
        dets = s["linker"].update(s["step"], detector._boxes(r))
        # a track is saved once, when it reaches MIN_TRACK_FRAMES, at the GPS of its best (confident + close) view
        ready = []
        for d in dets:
            tid = d["track_id"]
            if tid is None:
                continue
            tr = s["tracks"].setdefault(tid, {"frames": 0, "classes": {}, "best_score": -1.0, "best": None,
                                              "defect_id": None})
            tr["frames"] += 1
            tr["classes"][d["damage_class"]] = tr["classes"].get(d["damage_class"], 0.0) + d["confidence"]
            x1, y1, x2, y2 = d["bbox"]
            score = d["confidence"] * float(np.sqrt(max((x2 - x1) * (y2 - y1), 0)))
            if score > tr["best_score"] and tr["defect_id"] is None:
                tr["best_score"], tr["best"] = score, {"det": dict(d), "img": img, "lat": lat, "lon": lon}
            if (tr["defect_id"] is None and tr["best"] is not None
                    and (tr["frames"] >= MIN_TRACK_FRAMES or d["confidence"] >= detector.SOLO_CONF)
                    and tr["best"]["det"]["confidence"] >= SAVE_CONF):
                ready.append((tid, tr))
    reply = [{"bbox": [round(v, 1) for v in d["bbox"]], "damage_class": d["damage_class"],
              "confidence": round(d["confidence"], 3), "track_id": d["track_id"]} for d in dets]
    saved = []
    if ready:
        insp = {"id": sid, "created_at": pipeline.now_iso(), "road_type": None}
        media_dir = db.MEDIA_DIR / str(sid)
        media_dir.mkdir(parents=True, exist_ok=True)
        drawn = []
        with db.connect() as conn:
            for tid, tr in ready:
                b = tr["best"]
                det = {**b["det"], "damage_class": max(tr["classes"], key=tr["classes"].get),
                       "frames_seen": tr["frames"]}
                bimg = b["img"]
                if pipeline.privacy is not None:  # blur faces/plates before anything is stored
                    try:
                        bimg = pipeline.privacy.redact(bimg)
                    except Exception as e:
                        log.warning("redact failed: %s", e)
                with _lock:
                    s["n"] += 1
                    n = s["n"]
                rec = pipeline._record(conn, insp, det, bimg, b["lat"], b["lon"], media_dir, f"t{tid}_{n}",
                                       s["seen"], allow_same_inspection_merge=True)
                tr["defect_id"] = rec["defect_id"]
                b["img"] = None  # free the frame
                if rec["new"]:
                    s["new"].add(rec["defect_id"])
                obs = conn.execute("SELECT crop_path FROM observations WHERE defect_id=? ORDER BY id DESC LIMIT 1",
                                   (rec["defect_id"],)).fetchone()
                saved.append({**rec, "lat": b["lat"], "lon": b["lon"], "track_id": tid,
                              "crop_url": obs["crop_path"] if obs else None})
                drawn.append((bimg, det))
        now = time.time()
        due = {x["defect_id"] for x in saved if now - s["enriched"].get(x["defect_id"], 0) > 60}
        for did in due:
            s["enriched"][did] = now
        if due:
            _enrich_bg(due)
        try:  # latest evidence frame for officials (already blurred)
            bimg, det = drawn[-1]
            ok, jpg = cv2.imencode(".jpg", detector.draw(bimg, [det]), [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                s["last_jpg"], s["last_at"] = jpg.tobytes(), pipeline.now_iso()
        except Exception as e:
            log.warning("evidence frame failed: %s", e)
    s["frames"] += 1
    s["raw"] += len(dets)
    _set_position(s, lat, lon, accuracy, speed, heading)
    with db.connect() as conn:
        conn.execute("UPDATE inspections SET frames_processed=?, raw_detections=?, unique_defects=?, "
                     "new_defects=?, duplicate_defects=?, lat=COALESCE(lat, ?), lon=COALESCE(lon, ?) WHERE id=?",
                     (s["frames"], s["raw"], len(s["seen"]), len(s["new"]), len(s["seen"] - s["new"]),
                      lat, lon, sid))
    return {"frame_w": W, "frame_h": H, "detections": reply, "saved": saved,
            "stats": {"frames": s["frames"], "raw_detections": s["raw"], "unique_defects": len(s["seen"]),
                      "new_defects": len(s["new"])}}


TRAIL_MAX_POINTS = 1000   # ~15-30 min of driving at one point every 1-2 s
TRAIL_MIN_STEP_M = 3.0    # skip GPS jitter while standing still


def _set_position(s: dict, lat: float | None, lon: float | None, accuracy: float | None,
                  speed: float | None = None, heading: float | None = None) -> None:
    """Latest position of a live vehicle (for the officials' map) and its trail.
    Speed comes from the phone when it reports one; otherwise it is estimated from the distance
    travelled since the last trail point (many browsers and laptops report no speed)."""
    if lat is None or lon is None:
        return
    now = time.time()
    trail = s.setdefault("trail", [])
    last = s.get("last_point")  # (time, lat, lon) of the newest trail point
    moved = geo.haversine_m(last[1], last[2], lat, lon) if last else None
    if speed is None and last:
        dt = now - last[0]
        if moved >= TRAIL_MIN_STEP_M and dt >= 0.8:
            speed = moved / dt
        elif dt > 5:
            speed = 0.0  # no real movement for 5 s
        else:
            speed = (s.get("pos") or {}).get("speed")
    s["pos"] = {"lat": lat, "lon": lon, "accuracy": accuracy, "speed": speed, "heading": heading,
                "at": pipeline.now_iso(), "t": now}
    if last is None or moved >= TRAIL_MIN_STEP_M:
        trail.append([round(lon, 6), round(lat, 6)])
        del trail[:-TRAIL_MAX_POINTS]
        s["last_point"] = (now, lat, lon)


@router.post("/{sid}/position")
def position(sid: int, lat: float = Form(...), lon: float = Form(...), accuracy: float | None = Form(None),
             speed: float | None = Form(None), heading: float | None = Form(None)):
    """Real-time location only (no frame): the phone sends it every second while scanning, so the vehicle
    keeps moving on the officials' map even while a frame is still being processed."""
    s = _session(sid)
    _set_position(s, lat, lon, accuracy, speed, heading)
    return {"ok": True}


@router.post("/{sid}/stop")
def stop(sid: int):
    s = _session(sid)
    dedup = pipeline.dedup
    if dedup is not None:
        with db.connect() as conn:
            for did in s["seen"]:
                try:
                    dedup.update_progress(conn, did)
                except Exception as e:
                    log.warning("update_progress failed: %s", e)
    out = {"session_id": sid, "frames": s["frames"], "raw_detections": s["raw"],
           "unique_defects": len(s["seen"]), "new_defects": len(s["new"])}
    s["model"] = None
    _sessions.pop(sid, None)
    _stopped.add(sid)
    return out


@router.get("/feed")
def feed(since: int = 0, limit: int = 50):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT o.id, o.defect_id, o.inspection_id, o.observed_at, o.damage_class, o.confidence, "
            "o.severity_label, o.lat, o.lon, o.crop_path, o.frame_path, d.code, d.risk_label, d.ward_name "
            "FROM observations o JOIN inspections i ON i.id=o.inspection_id "
            "LEFT JOIN defects d ON d.id=o.defect_id "
            "WHERE i.source_type='live' AND o.id>? ORDER BY o.id DESC LIMIT ?", (since, limit)).fetchall()
        sess = conn.execute("SELECT id, created_at, frames_processed, raw_detections, unique_defects "
                            "FROM inspections WHERE source_type='live' ORDER BY id DESC LIMIT 1").fetchone()
    now = time.time()
    vehicles = [{"session_id": k, **{key: val for key, val in v["pos"].items() if key != "t"},
                 "age_s": round(now - v["pos"]["t"], 1), "trail": list(v.get("trail", [])),
                 "frame_at": v.get("last_at")}
                for k, v in list(_sessions.items()) if v.get("pos")]
    return {"items": [db.row(r) for r in rows][::-1], "active": sorted(_sessions), "vehicles": vehicles,
            "latest_session": db.row(sess) if sess else None}


@router.get("/{sid}/latest.jpg")
def latest(sid: int):
    """Latest evidence frame (boxes drawn, faces/plates blurred) of an active session, for officials."""
    s = _sessions.get(sid)
    if not s or not s.get("last_jpg"):
        raise HTTPException(404, "no evidence frame yet")
    return Response(s["last_jpg"], media_type="image/jpeg", headers={"Cache-Control": "no-store"})
