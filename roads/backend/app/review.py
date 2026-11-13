"""Admin verification (confirm / reject / correct AI detections), repair workflow,
and export of verified labels in YOLO format for retraining."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from . import db

router = APIRouter(prefix="/api")

CLASSES = ["longitudinal_crack", "transverse_crack", "alligator_crack", "pothole"]
WORKFLOW_ORDER = ["reported", "verified", "scheduled", "repaired", "closed"]
AUTO_MIN_CONF = 0.6      # confident enough when the defect was seen on several scans ...
AUTO_MIN_REPORTS = 3
AUTO_SURE_CONF = 0.85    # ... or this confident on a single scan. Everything else goes to a human.


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def media_abs(path: Optional[str]) -> Optional[Path]:
    """Resolve a stored frame/crop path (absolute or relative to media dir)."""
    if not path:
        return None
    p = Path(path)
    if p.is_absolute():
        return p
    cand = Path(db.MEDIA_DIR) / p
    if cand.exists():
        return cand
    # relative to repo root / backend dir (e.g. "backend/data/media/x.jpg" or "data/media/x.jpg")
    for base in list(Path(db.MEDIA_DIR).parents)[1:3]:
        if (base / p).exists():
            return base / p
    return cand


def media_url(path: Optional[str]) -> Optional[str]:
    """Turn a stored path into a `/media/...` URL (None if not under the media dir)."""
    if not path:
        return None
    p = media_abs(path)
    try:
        rel = p.resolve().relative_to(Path(db.MEDIA_DIR).resolve())
    except (ValueError, OSError):
        s = str(path).replace("\\", "/")
        if "/media/" in s:
            return "/media/" + s.split("/media/", 1)[1]
        return "/media/" + s.lstrip("/")
    return "/media/" + rel.as_posix()


def _obs_out(o: dict) -> dict:
    o["frame_url"] = media_url(o.get("frame_path"))
    o["crop_url"] = media_url(o.get("crop_path"))
    return o


# ---------------------------------------------------------------- review queue
@router.get("/review/queue")
def review_queue(limit: int = 50) -> list[dict]:
    """Pending observations, lowest confidence first, with image URLs and defect info.
    Confident detections are auto-approved first, so only doubtful ones reach a person."""
    with db.connect() as c:
        auto_approve(c)
        rows = c.execute(
            """SELECT o.*, d.code AS defect_code, d.report_count AS report_count,
                      d.workflow AS defect_workflow, d.risk_label AS risk_label
               FROM observations o LEFT JOIN defects d ON d.id = o.defect_id
               WHERE o.review_status = 'pending' AND COALESCE(o.absent, 0) = 0
               ORDER BY COALESCE(o.confidence, 0) ASC, o.id ASC LIMIT ?""",
            (max(1, min(int(limit), 1000)),),
        ).fetchall()
    return [_obs_out(db.row(r)) for r in rows]


def _apply_review(c, obs: dict, action: str, reviewer: str,
                  corrected_class: Optional[str] = None, corrected_bbox: Any = None) -> None:
    ts = now_iso()
    oid, did = obs["id"], obs.get("defect_id")
    if action == "confirm":
        c.execute("UPDATE observations SET review_status='confirmed', reviewed_at=?, reviewer=? WHERE id=?",
                  (ts, reviewer, oid))
    elif action == "reject":
        c.execute("UPDATE observations SET review_status='rejected', reviewed_at=?, reviewer=? WHERE id=?",
                  (ts, reviewer, oid))
        if did is not None:
            others = c.execute(
                "SELECT COUNT(*) FROM observations WHERE defect_id=? AND id<>? AND review_status<>'rejected' "
                "AND COALESCE(absent,0)=0", (did, oid)).fetchone()[0]
            if others == 0:
                c.execute("UPDATE defects SET workflow='rejected', status='rejected' WHERE id=?", (did,))
    elif action == "correct":
        c.execute(
            "UPDATE observations SET review_status='corrected', corrected_class=?, corrected_bbox=?, "
            "reviewed_at=?, reviewer=? WHERE id=?",
            (corrected_class, json.dumps(corrected_bbox) if corrected_bbox is not None else None, ts, reviewer, oid))
        if did is not None and corrected_class:
            latest = c.execute(
                "SELECT id FROM observations WHERE defect_id=? AND COALESCE(absent,0)=0 "
                "ORDER BY observed_at DESC, id DESC LIMIT 1", (did,)).fetchone()
            if latest and latest[0] == oid:
                c.execute("UPDATE defects SET damage_class=? WHERE id=?", (corrected_class, did))


@router.post("/observations/{obs_id}/review")
def review_observation(obs_id: int, body: dict = Body(...)) -> dict:
    """Admin decision on one AI detection: confirm | reject | correct."""
    action = str(body.get("action", "")).lower()
    if action not in ("confirm", "reject", "correct"):
        raise HTTPException(400, "action must be confirm, reject or correct")
    reviewer = str(body.get("reviewer") or "admin")[:80]
    cc, cb = body.get("corrected_class"), body.get("corrected_bbox")
    if action == "correct":
        if cc is None and cb is None:
            raise HTTPException(400, "correct needs corrected_class and/or corrected_bbox")
        if cc is not None and cc not in CLASSES:
            raise HTTPException(400, f"corrected_class must be one of {CLASSES}")
        if cb is not None:
            if not (isinstance(cb, (list, tuple)) and len(cb) == 4):
                raise HTTPException(400, "corrected_bbox must be [x1,y1,x2,y2]")
            try:
                cb = [float(v) for v in cb]
            except (TypeError, ValueError) as e:
                raise HTTPException(400, "corrected_bbox must be numeric") from e
    with db.connect() as c:
        r = c.execute("SELECT * FROM observations WHERE id=?", (obs_id,)).fetchone()
        if r is None:
            raise HTTPException(404, "observation not found")
        obs = db.row(r)
        if action == "correct" and cc is None:
            cc = obs.get("damage_class")
        _apply_review(c, obs, action, reviewer, cc, cb)
        out = db.row(c.execute("SELECT * FROM observations WHERE id=?", (obs_id,)).fetchone())
        d = c.execute("SELECT id, code, workflow, damage_class FROM defects WHERE id=?",
                      (obs.get("defect_id"),)).fetchone()
    res = _obs_out(out)
    res["defect"] = dict(d) if d else None
    return res


def auto_approve(conn) -> int:
    """Confirm pending observations the model is sure about, so people only check the doubtful ones:
    confidence >= 0.6 on a defect with >= 3 reports, or confidence >= 0.85 on its own.
    Idempotent; returns the number of observations confirmed."""
    ts = now_iso()
    cur = conn.execute(
        """UPDATE observations SET review_status='confirmed', reviewed_at=?, reviewer='auto'
           WHERE review_status='pending' AND COALESCE(absent,0)=0
             AND (confidence >= ?
                  OR (confidence >= ? AND defect_id IN (SELECT id FROM defects WHERE report_count >= ?)))""",
        (ts, AUTO_SURE_CONF, AUTO_MIN_CONF, AUTO_MIN_REPORTS))
    return cur.rowcount or 0


@router.post("/review/auto_approve")
def auto_approve_endpoint() -> dict:
    with db.connect() as c:
        n = auto_approve(c)
    return {"approved": n}


# ---------------------------------------------------------------- workflow
def _check_transition(cur: str, new: str) -> None:
    if new == "rejected":
        return
    if new not in WORKFLOW_ORDER:
        raise HTTPException(400, f"workflow must be one of {WORKFLOW_ORDER[1:] + ['rejected']}")
    cur = cur or "reported"
    if cur == "rejected":
        if new == "verified":
            return  # admin may re-open a wrongly rejected defect
        raise HTTPException(409, "rejected defect can only be moved back to verified")
    if cur not in WORKFLOW_ORDER or WORKFLOW_ORDER.index(new) != WORKFLOW_ORDER.index(cur) + 1:
        raise HTTPException(409, f"invalid transition {cur} -> {new}")


def _set_workflow(defect_id: int, new: str, note: Optional[str] = None,
                  repair_photo: Optional[str] = None) -> dict:
    with db.connect() as c:
        r = c.execute("SELECT * FROM defects WHERE id=?", (defect_id,)).fetchone()
        if r is None:
            raise HTTPException(404, "defect not found")
        cur = r["workflow"] or "reported"
        if not (new == "repaired" and cur == "repaired" and repair_photo):  # allow adding a photo later
            _check_transition(cur, new)
        sets, args = ["workflow=?"], [new]
        if new == "repaired":
            sets += ["repaired_at=COALESCE(repaired_at, ?)", "status='repaired'"]
            args.append(now_iso())
        elif new == "closed":
            sets.append("status='repaired'")
        elif new == "rejected":
            sets.append("status='rejected'")
        elif new == "verified":
            sets.append("status='open'")
        if repair_photo:
            sets.append("repair_photo=?")
            args.append(repair_photo)
        c.execute(f"UPDATE defects SET {', '.join(sets)} WHERE id=?", (*args, defect_id))
        out = db.row(c.execute("SELECT * FROM defects WHERE id=?", (defect_id,)).fetchone())
    out["previous_workflow"] = cur
    if note:
        out["note"] = note
    out["repair_photo_url"] = media_url(out.get("repair_photo"))
    return out


@router.patch("/defects/{defect_id}/workflow")
def patch_workflow(defect_id: int, body: dict = Body(...)) -> dict:
    """Move a defect along reported -> verified -> scheduled -> repaired -> closed (or -> rejected)."""
    new = str(body.get("workflow", "")).lower()
    return _set_workflow(defect_id, new, body.get("note"))


@router.post("/defects/{defect_id}/repair")
async def post_repair(defect_id: int, file: Optional[UploadFile] = File(None),
                      note: Optional[str] = Form(None)) -> dict:
    """Mark repaired with an optional after-repair photo (saved under media/repairs/)."""
    rel = None
    if file is not None and file.filename:
        ext = Path(file.filename).suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            ext = ".jpg"
        d = Path(db.MEDIA_DIR) / "repairs"
        d.mkdir(parents=True, exist_ok=True)
        name = f"defect{defect_id}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}{ext}"
        (d / name).write_bytes(await file.read())
        rel = f"repairs/{name}"
    return _set_workflow(defect_id, "repaired", note, rel)


# ---------------------------------------------------------------- export
def _yolo_line(cls_name: str, bbox: Any, fw: Any, fh: Any) -> Optional[str]:
    if cls_name not in CLASSES or not bbox or not fw or not fh:
        return None
    try:
        x1, y1, x2, y2 = [float(v) for v in bbox]
        fw, fh = float(fw), float(fh)
    except (TypeError, ValueError):
        return None
    x1, x2 = sorted((max(0.0, min(fw, x1)), max(0.0, min(fw, x2))))
    y1, y2 = sorted((max(0.0, min(fh, y1)), max(0.0, min(fh, y2))))
    if x2 - x1 <= 0 or y2 - y1 <= 0:
        return None
    cx, cy = (x1 + x2) / 2 / fw, (y1 + y2) / 2 / fh
    w, h = (x2 - x1) / fw, (y2 - y1) / fh
    return f"{CLASSES.index(cls_name)} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"


def build_corrections_zip() -> tuple[bytes, dict]:
    """Zip (images/, labels/, data.yaml) from reviewed observations. Several observations
    sharing a frame become one image with several label lines. Rejected = negatives."""
    with db.connect() as c:
        rows = [db.row(r) for r in c.execute(
            "SELECT * FROM observations WHERE review_status IN ('confirmed','corrected','rejected') "
            "AND frame_path IS NOT NULL ORDER BY id").fetchall()]
    frames: dict[str, dict] = {}
    for o in rows:
        p = media_abs(o["frame_path"])
        if p is None or not p.exists():
            continue
        f = frames.setdefault(str(p), {"path": p, "lines": [], "obs": []})
        f["obs"].append(o["id"])
        if o["review_status"] == "rejected":
            continue
        cls = o.get("corrected_class") or o.get("damage_class")
        bbox = o.get("corrected_bbox") or o.get("bbox")
        line = _yolo_line(cls, bbox, o.get("frame_w"), o.get("frame_h"))
        if line:
            f["lines"].append(line)
    buf = io.BytesIO()
    stats = {"images": 0, "positives": 0, "negatives": 0, "boxes": 0}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, f in enumerate(frames.values()):
            stem = f"rs_{i:05d}_obs{f['obs'][0]}"
            z.write(f["path"], f"images/{stem}{f['path'].suffix.lower() or '.jpg'}")
            z.writestr(f"labels/{stem}.txt", "\n".join(f["lines"]) + ("\n" if f["lines"] else ""))
            stats["images"] += 1
            stats["boxes"] += len(f["lines"])
            stats["positives" if f["lines"] else "negatives"] += 1
        names = "\n".join(f"  {i}: {n}" for i, n in enumerate(CLASSES))
        z.writestr("data.yaml",
                   "# Nagrik Roads verified corrections (admin-reviewed). Empty label files = negatives.\n"
                   f"path: .\ntrain: images\nval: images\nnc: {len(CLASSES)}\nnames:\n{names}\n")
        z.writestr("README.txt", json.dumps({"exported_at": now_iso(), **stats}, indent=2))
    return buf.getvalue(), stats


@router.get("/export/corrections")
def export_corrections() -> Response:
    data, stats = build_corrections_zip()
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="roadsense_corrections.zip"',
        "X-Export-Stats": json.dumps(stats)})
