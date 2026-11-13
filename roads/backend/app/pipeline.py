"""Inspection processing: detect/track -> severity -> media -> dedup -> defect/observation ->
risk + civic enrichment -> progress/absence. Other agents' modules are imported defensively:
if one is missing or raises, a simple fallback is used and a warning is logged.
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from . import db, detector, geo, severity

try:  # face / number-plate blurring before anything is saved
    from . import privacy
except Exception:  # pragma: no cover
    privacy = None
try:  # GPS embedded in dashcam videos (needs ExifTool; returns [] without it)
    from . import dashcam
except Exception:  # pragma: no cover
    dashcam = None

log =logging.getLogger("roadsense.pipeline")

try:
    from . import dedup
except Exception as e:  # pragma: no cover - depends on other agents
    dedup = None
    log.warning("dedup module unavailable (%s): every detection becomes a new defect", e)
try:
    from . import risk
except Exception as e:  # pragma: no cover
    risk = None
    log.warning("risk module unavailable (%s): risk from severity only", e)
# Nagrik civic enrichment (ward, officials, road tenders) is ON by default; disable with ROADSENSE_NAGRIK=0.
nagrik = None
if os.environ.get("ROADSENSE_NAGRIK", "1") == "1":
    try:
        from . import nagrik
    except Exception as e:  # pragma: no cover
        nagrik = None
        log.warning("nagrik module unavailable (%s): skipping civic enrichment", e)

UPLOAD_DIR = db.DATA_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
CRACKS = {"longitudinal_crack", "transverse_crack", "alligator_crack"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def media_url(path: Path | str | None) -> str | None:
    """Disk path under MEDIA_DIR -> '/media/...' URL."""
    if not path:
        return None
    try:
        return "/media/" + Path(path).resolve().relative_to(db.MEDIA_DIR.resolve()).as_posix()
    except ValueError:
        return None


def media_file(url: str | None) -> Path | None:
    """'/media/...' URL -> disk path."""
    if not url or not url.startswith("/media/"):
        return None
    return db.MEDIA_DIR / url[len("/media/"):]


def upload_dir(inspection_id: int) -> Path:
    d = UPLOAD_DIR / str(inspection_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _find(inspection_id: int, stem: str) -> Path | None:
    d = UPLOAD_DIR / str(inspection_id)
    if not d.exists():
        return None
    for p in sorted(d.iterdir()):
        if p.stem == stem:
            return p
    return None


def imread(path) -> np.ndarray | None:
    """cv2.imread that also works with non-ASCII Windows paths."""
    try:
        return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return None


def imwrite_jpg(path: Path, img: np.ndarray, max_side: int | None = None, quality: int = 88) -> Path:
    if max_side:
        h, w = img.shape[:2]
        s = max_side / max(h, w)
        if s < 1:
            img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if ok:
        path.parent.mkdir(parents=True, exist_ok=True)
        buf.tofile(str(path))
    return path


def crop_with_margin(img: np.ndarray, bbox, margin: float = 0.2) -> np.ndarray:
    h, w = img.shape[:2]
    x1, y1, x2, y2 = bbox
    mx, my = (x2 - x1) * margin, (y2 - y1) * margin
    X1, Y1 = max(0, int(x1 - mx)), max(0, int(y1 - my))
    X2, Y2 = min(w, int(x2 + mx) + 1), min(h, int(y2 + my) + 1)
    c = img[Y1:Y2, X1:X2]
    return c if c.size else img


def _code(conn, damage_class: str) -> str:
    prefix = "PH" if damage_class == "pothole" else "CR"
    n = 0
    for (code,) in conn.execute("SELECT code FROM defects WHERE code LIKE ?", (prefix + "-%",)):
        try:
            n = max(n, int(code.split("-")[1]))
        except (ValueError, IndexError):
            pass
    return f"{prefix}-{n + 1:04d}"


def fallback_risk(defect: dict) -> dict:
    """Risk from severity + repeat reports only (used when risk.py is unavailable)."""
    sev = float(defect.get("severity_score") or 0)
    rc = int(defect.get("report_count") or 1)
    pts_sev = 0.7 * sev
    pts_rep = min(rc - 1, 5) * 4
    score = round(min(100.0, pts_sev + pts_rep), 1)
    label = "Critical" if score >= 75 else "High" if score >= 50 else "Medium" if score >= 25 else "Low"
    return {"risk_score": score, "risk_label": label, "risk_factors": [
        {"factor": "severity", "detail": f"severity {sev:.0f}/100 (fallback formula)", "points": round(pts_sev, 1)},
        {"factor": "report_count", "detail": f"{rc} report(s)", "points": pts_rep}]}


def _fingerprint(crop: np.ndarray) -> dict:
    if dedup is None:
        return {}
    try:
        return dedup.fingerprint(crop)
    except Exception as e:
        log.warning("dedup.fingerprint failed: %s", e)
        return {}


def _match(conn, damage_class, lat, lon, fp, exclude) -> tuple[int | None, dict]:
    if dedup is None:
        return None, {"reason": "dedup unavailable - new defect", "distance_m": None, "similarity": 0.0, "candidates": 0}
    try:
        try:
            return dedup.match_defect(conn, damage_class, lat, lon, fp, exclude_ids=exclude)
        except TypeError:
            did, info = dedup.match_defect(conn, damage_class, lat, lon, fp)
            if did in exclude:
                return None, {**info, "reason": "matched a defect already seen in this inspection - kept separate"}
            return did, info
    except Exception as e:
        log.warning("dedup.match_defect failed: %s", e)
        return None, {"reason": f"dedup error: {e}", "distance_m": None, "similarity": 0.0, "candidates": 0}


def _save_fp(conn, defect_id: int, fp: dict):
    if not fp:
        return
    try:
        if dedup is not None and hasattr(dedup, "save_fingerprint"):
            dedup.save_fingerprint(conn, defect_id, fp)
        else:
            conn.execute("UPDATE defects SET embedding=? WHERE id=?", (json.dumps(fp), defect_id))
    except Exception as e:
        log.warning("saving fingerprint failed: %s", e)


def _record(conn, insp: dict, det: dict, frame: np.ndarray, lat, lon, media_dir: Path, tag: str,
            seen: set[int], allow_same_inspection_merge: bool) -> dict:
    """Save media, dedup, create/update defect and insert observation. Returns summary."""
    H, W = frame.shape[:2]
    bbox = det["bbox"]
    depth = severity.depth_index(frame, bbox)
    sev = severity.severity(det["damage_class"], bbox, W, H, depth)
    # media
    frame_p = imwrite_jpg(media_dir / f"{tag}_frame.jpg", frame, max_side=1280)
    crop = crop_with_margin(frame, bbox, 0.2)
    crop_p = imwrite_jpg(media_dir / f"{tag}_crop.jpg", crop)
    fp = _fingerprint(crop)
    exclude = set() if allow_same_inspection_merge else set(seen)
    defect_id, info = _match(conn, det["damage_class"], lat, lon, fp, exclude)
    ts = insp["created_at"] or now_iso()
    is_new = defect_id is None
    if is_new:
        code = _code(conn, det["damage_class"])
        cur = conn.execute(
            "INSERT INTO defects (code, damage_class, lat, lon, first_seen, last_seen, severity_score, "
            "severity_label, report_count, road_type, trend) VALUES (?,?,?,?,?,?,?,?,1,?,'new')",
            (code, det["damage_class"], lat, lon, ts, ts, sev["severity_score"], sev["severity_label"],
             insp.get("road_type")))
        defect_id = cur.lastrowid
    else:
        d = conn.execute("SELECT * FROM defects WHERE id=?", (defect_id,)).fetchone()
        code = d["code"]
        rc = d["report_count"] or 1
        new_rc = rc if defect_id in seen else rc + 1  # one report per inspection
        nlat, nlon = d["lat"], d["lon"]
        if lat is not None and lon is not None:
            if nlat is None or nlon is None:
                nlat, nlon = lat, lon
            elif defect_id not in seen:  # running mean of sightings
                nlat = (nlat * rc + lat) / (rc + 1)
                nlon = (nlon * rc + lon) / (rc + 1)
        conn.execute(
            "UPDATE defects SET last_seen=?, severity_score=?, severity_label=?, report_count=?, lat=?, lon=? "
            "WHERE id=?",
            (ts, sev["severity_score"], sev["severity_label"], new_rc, nlat, nlon, defect_id))
    _save_fp(conn, defect_id, fp)
    conn.execute(
        "INSERT INTO observations (defect_id, inspection_id, observed_at, damage_class, confidence, bbox, "
        "frame_w, frame_h, area_ratio, depth_index, severity_score, severity_label, track_id, frames_seen, "
        "frame_path, crop_path, lat, lon, match_info) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (defect_id, insp["id"], ts, det["damage_class"], det["confidence"], json.dumps([round(v, 1) for v in bbox]),
         W, H, sev["area_ratio"], depth, sev["severity_score"], sev["severity_label"], det.get("track_id"),
         det.get("frames_seen", 1), media_url(frame_p), media_url(crop_p), lat, lon, json.dumps(info)))
    seen.add(defect_id)
    return {"defect_id": defect_id, "code": code, "new": is_new, "damage_class": det["damage_class"],
            "severity_label": sev["severity_label"], "severity_score": sev["severity_score"],
            "confidence": round(det["confidence"], 3), "match_info": info}


def _enrich(defect_ids: set[int], road_type_hint: str | None, warnings: list[str]):
    """Risk, forecast and Nagrik ward data for each defect. Network calls happen outside the DB lock."""
    memo: dict = {}  # (kind, ~11 m grid) -> result, so N defects at one spot cost one lookup

    def cached(kind, fn, lat, lon):
        k = (kind, round(lat, 4), round(lon, 4))
        if k not in memo:
            memo[k] = fn(lat, lon)
        return memo[k]

    for did in defect_ids:
        with db.connect() as conn:
            d = db.row(conn.execute("SELECT * FROM defects WHERE id=?", (did,)).fetchone())
            obs = [db.row(r) for r in conn.execute(
                "SELECT * FROM observations WHERE defect_id=? ORDER BY observed_at, id", (did,))]
        if d is None:
            continue
        lat, lon = d.get("lat"), d.get("lon")
        ctx, wx, upd = {}, {}, {}
        if risk is not None:
            try:
                if lat is not None and lon is not None:
                    ctx = dict(cached("ctx", risk.road_context, lat, lon) or {})
                    wx = cached("wx", risk.weather, lat, lon) or {}
                if road_type_hint and (not ctx or ctx.get("road_type") in (None, "unknown")):
                    ctx = {**ctx, "road_type": road_type_hint}
                rk = risk.compute_risk(d, ctx or None, wx or None)
            except Exception as e:
                warnings.append(f"risk failed for {d['code']}: {e}")
                log.warning("risk failed for %s: %s", d["code"], e)
                rk = fallback_risk(d)
            try:
                upd["forecast"] = json.dumps(risk.forecast(obs, wx or None))
            except Exception as e:
                log.warning("forecast failed for %s: %s", d["code"], e)
        else:
            rk = fallback_risk(d)
        upd.update(risk_score=rk.get("risk_score"), risk_label=rk.get("risk_label"),
                   risk_factors=json.dumps(rk.get("risk_factors", [])))
        if ctx:
            upd.update(road_type=ctx.get("road_type") or d.get("road_type"),
                       road_name=ctx.get("road_name") or d.get("road_name"),
                       speed_limit=ctx.get("speed_limit") or d.get("speed_limit"))
        if nagrik is not None and lat is not None and lon is not None:
            try:
                ward = cached("ward", nagrik.resolve, lat, lon)
                if ward:
                    works = []
                    try:
                        works = nagrik.ward_works(ward.get("city"), ward.get("ward_id")) or []
                    except Exception as e:
                        log.warning("nagrik.ward_works failed: %s", e)
                    upd.update(ward_name=ward.get("ward_name"), ward_id=ward.get("ward_id"),
                               corporation=ward.get("corporation"), city=ward.get("city"),
                               nagrik=json.dumps({"officials": ward.get("officials", []), "works": works}))
            except Exception as e:
                log.warning("nagrik.resolve failed: %s", e)
        cols = ", ".join(f"{k}=?" for k in upd)
        with db.connect() as conn:
            conn.execute(f"UPDATE defects SET {cols} WHERE id=?", (*upd.values(), did))


def process_inspection(inspection_id: int, progress_cb=None) -> dict:
    """Run the full pipeline for one uploaded inspection. Returns a summary dict."""
    prog = progress_cb or (lambda p: None)
    warnings: list[str] = []
    with db.connect() as conn:
        insp = db.row(conn.execute("SELECT * FROM inspections WHERE id=?", (inspection_id,)).fetchone())
    if insp is None:
        raise ValueError(f"inspection {inspection_id} not found")
    src = _find(inspection_id, "input")
    if src is None:
        raise FileNotFoundError(f"no uploaded file for inspection {inspection_id}")
    media_dir = db.MEDIA_DIR / str(inspection_id)
    media_dir.mkdir(parents=True, exist_ok=True)
    form_loc = (insp["lat"], insp["lon"]) if insp.get("lat") is not None and insp.get("lon") is not None else None
    prog(0.02)

    items = []  # (det, frame, lat, lon)
    if insp["source_type"] == "image":
        img = imread(src)
        if img is None:
            raise ValueError("could not read image")
        loc = geo.exif_gps(src) or form_loc
        dets = detector.detect_image(img)
        if privacy is not None:  # detect on the original, save/show only the redacted image
            img = privacy.redact(img)
        prog(0.4)
        annotated = imwrite_jpg(media_dir / "annotated.jpg", detector.draw(img, dets), max_side=1600)
        raw, frames, track = len(dets), 1, []
        for d in dets:
            items.append((d, img, *(loc or (None, None))))
        insp_loc = loc
    else:
        tpath = _find(inspection_id, "gps_track")
        track = geo.load_track(tpath) if tpath else []
        if tpath and not track:
            warnings.append("GPS track could not be parsed")
        if not track and dashcam is not None:  # no usable uploaded track: try GPS embedded in the video
            try:
                track = dashcam.extract_embedded_track(src) or []
            except Exception as e:
                log.warning("embedded GPS extraction failed: %s", e)
                track = []
            if len(track) == 1 and form_loc:  # a single clip-level location does not beat the typed one
                track = []
            if track:
                warnings.append(f"GPS source: track embedded in the video ({len(track)} points, read with ExifTool)")
        res = detector.track_video(str(src), media_dir / "annotated",
                                   progress_cb=lambda p: prog(0.02 + 0.68 * p))
        annotated = res["out_path"]
        raw, frames = res["raw_detections"], res["frames_processed"]
        for u in res["uniques"]:
            loc = geo.interpolate(track, u["t"]) if track else form_loc
            items.append((u, u["frame"], *(loc or (None, None))))
        insp_loc = (track[0]["lat"], track[0]["lon"]) if track else form_loc
    prog(0.72)

    seen: set[int] = set()
    summaries = []
    with db.connect() as conn:
        for i, (det, frame, lat, lon) in enumerate(items):
            tag = f"t{det['track_id']}" if det.get("track_id") is not None else f"d{i}"
            # Two tracks of one video may merge into one defect only when a GPS track places them: without
            # one, every pothole in the clip shares the same point and similar-looking asphalt would merge
            # different potholes (the tracker already counts each pothole in the clip once).
            summaries.append(_record(conn, insp, det, frame, lat, lon, media_dir, tag, seen,
                                     allow_same_inspection_merge=insp["source_type"] == "video" and bool(track)))
        if dedup is not None:  # trend before risk, so risk can use it
            for did in seen:
                try:
                    dedup.update_progress(conn, did)
                except Exception as e:
                    log.warning("update_progress failed for %s: %s", did, e)
        new = len({s["defect_id"] for s in summaries if s["new"]})
        uniq = len(seen)
        conn.execute(
            "UPDATE inspections SET media_path=?, frames_processed=?, raw_detections=?, unique_defects=?, "
            "new_defects=?, duplicate_defects=?, lat=COALESCE(lat, ?), lon=COALESCE(lon, ?) WHERE id=?",
            (media_url(annotated), frames, raw, uniq, new, uniq - new,
             insp_loc[0] if insp_loc else None, insp_loc[1] if insp_loc else None, inspection_id))
    prog(0.8)

    _enrich(seen, insp.get("road_type"), warnings)
    prog(0.92)

    absent: list[int] = []
    with db.connect() as conn:
        if dedup is not None:
            points = geo.sample_track(track) if track else ([insp_loc] if insp_loc else [])
            for lat, lon in points:
                try:
                    absent += dedup.mark_absent(conn, lat, lon, inspection_id, seen)
                except Exception as e:
                    log.warning("mark_absent failed: %s", e)
                    break
        trends = {r["id"]: r["trend"] for r in conn.execute(
            f"SELECT id, trend FROM defects WHERE id IN ({','.join('?' * len(seen)) or 'NULL'})", tuple(seen))}
    for s in summaries:
        s["trend"] = trends.get(s["defect_id"])
    prog(1.0)
    return {
        "inspection_id": inspection_id,
        "source_type": insp["source_type"],
        "media_url": media_url(annotated),
        "frames_processed": frames,
        "raw_detections": raw,
        "unique_defects": len(seen),
        "new_defects": len({s["defect_id"] for s in summaries if s["new"]}),
        "duplicate_defects": len(seen) - len({s["defect_id"] for s in summaries if s["new"]}),
        "marked_absent": sorted(set(absent)),
        "location": list(insp_loc) if insp_loc else None,
        "gps_points": len(track),
        "model": detector.model_info().get("name"),
        "defects": summaries,
        "warnings": warnings,
    }
