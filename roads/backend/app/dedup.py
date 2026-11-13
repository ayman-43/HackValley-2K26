"""Duplicate-report detection + road-damage progress monitoring.

PS requirements covered:
* "Duplicate Report Detection" -> fingerprint / similarity / match_defect
  (3 layers: GPS radius, compatible damage class, image similarity).
* "Road Damage Progress Monitoring" -> update_progress / mark_absent / check_bad_repair
  (worsening / improving / stable, possibly_repaired -> repaired, bad repair = reappears < 90 days).

Lightweight on purpose: no deep model, CPU only, no network. All DB functions take an open
sqlite3 connection (row_factory=sqlite3.Row, as from db.connect()) and do NOT commit;
the caller's context manager commits.
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone

import cv2
import imagehash
import numpy as np
from PIL import Image

# --------------------------------------------------------------------------------------
# Tunable constants (all thresholds live here)
# --------------------------------------------------------------------------------------
FP_VERSION = 1
FP_SIZE = 64                 # crops are resized to 64x64 before fingerprinting (scale invariance)
THUMB_SIZE = 8               # 8x8 zero-mean/unit-var grey thumbnail -> NCC (brightness/contrast invariant, shift tolerant)
HIST_BINS = (8, 4, 4)        # H x S x V bins (128-d, L1-normalised)

# similarity() weights (sum to 1). Hash similarities are rescaled so that chance level
# (hamming 32/64, i.e. raw 1-h/64 = 0.5) maps to 0 -> unrelated crops score near 0, not 0.5.
W_PHASH = 0.30
W_DHASH = 0.15
W_THUMB = 0.35               # normalised cross-correlation of grey thumbnails, clipped to 0..1
W_HIST = 0.20                # colour-histogram correlation (road texture colour), clipped to 0..1

# match_defect() decision rules
MATCH_RADIUS_M = 15.0        # candidates must lie within this GPS radius (phone GPS error ~5-10 m)
NEAR_RADIUS_M = 5.0          # inside 5 m we trust location more ...
SIM_NEAR = 0.45              # ... and accept a moderate image similarity
SIM_FAR = 0.60               # 5-15 m needs clearly similar appearance
SIM_NO_GPS = 0.92            # without GPS only near-identical images (re-uploaded photo) match
GPS_ONLY_RADIUS_M = 3.0      # if a fingerprint is missing on either side, same-class within 3 m still matches
MAX_STORED_FPS = 3           # fingerprints kept per defect (latest views; similarity = best of them)

# Class compatibility weight (multiplies similarity). Severe alligator cracking breaks up into
# potholes, and a crack seen at an angle is easily labelled longitudinal vs transverse.
CLASS_COMPAT = {
    frozenset(["alligator_crack", "pothole"]): 0.85,
    frozenset(["longitudinal_crack", "transverse_crack"]): 0.70,
}

# update_progress() trend thresholds (latest vs previous sighting)
SEVERITY_DELTA = 8.0         # severity_score is 0..100; >8 points is beyond frame-to-frame noise
AREA_REL_DELTA = 0.15        # area_ratio changed by >15 % relative (bbox noise is ~5-10 %)

# mark_absent() / check_bad_repair()
ABSENT_RADIUS_M = 12.0
BAD_REPAIR_DAYS = 90         # pothole back within 90 days of repair = bad repair (contractor liability)


# --------------------------------------------------------------------------------------
# Fingerprints
# --------------------------------------------------------------------------------------
def fingerprint(crop_bgr: np.ndarray) -> dict:
    """JSON-safe appearance fingerprint of a defect crop (BGR uint8).

    {"v", "phash" (hex, 64-bit), "dhash" (hex, 64-bit), "thumb" (64 floats), "hist" (128 floats)}.
    Returns {} for an empty/invalid crop.
    """
    if crop_bgr is None or not isinstance(crop_bgr, np.ndarray) or crop_bgr.size == 0 \
            or crop_bgr.shape[0] < 2 or crop_bgr.shape[1] < 2:
        return {}
    img = crop_bgr
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    img = cv2.resize(img, (FP_SIZE, FP_SIZE), interpolation=cv2.INTER_AREA)
    grey = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    pil = Image.fromarray(grey)
    ph = str(imagehash.phash(pil, hash_size=8))
    dh = str(imagehash.dhash(pil, hash_size=8))

    th = cv2.resize(grey, (THUMB_SIZE, THUMB_SIZE), interpolation=cv2.INTER_AREA).astype(np.float32)
    th = (th - th.mean()) / (th.std() + 1e-6)

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    v = hsv[..., 2].astype(np.float32)
    # gain-normalise brightness so a sunnier/darker photo of the same pothole keeps its histogram
    hsv[..., 2] = np.clip(v * (128.0 / (v.mean() + 1e-6)), 0, 255).astype(np.uint8)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, list(HIST_BINS), [0, 180, 0, 256, 0, 256]).flatten()
    hist = hist / (hist.sum() + 1e-9)

    return {
        "v": FP_VERSION,
        "phash": ph,
        "dhash": dh,
        "thumb": [round(float(x), 3) for x in th.flatten()],
        "hist": [round(float(x), 5) for x in hist],
    }


def _hash_sim(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    try:
        ham = bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return None
    raw = 1.0 - ham / 64.0                      # 1 - hamming/64
    return float(np.clip((raw - 0.5) / 0.5, 0.0, 1.0))  # chance level (0.5) -> 0


def _vec_corr(a, b) -> float | None:
    if not a or not b or len(a) != len(b):
        return None
    x = np.asarray(a, dtype=np.float32)
    y = np.asarray(b, dtype=np.float32)
    x = x - x.mean()
    y = y - y.mean()
    d = float(np.sqrt((x * x).sum() * (y * y).sum()))
    if d < 1e-9:
        return None
    return float(np.clip((x * y).sum() / d, 0.0, 1.0))


def _single_similarity(a: dict, b: dict) -> float:
    parts = [
        (W_PHASH, _hash_sim(a.get("phash"), b.get("phash"))),
        (W_DHASH, _hash_sim(a.get("dhash"), b.get("dhash"))),
        (W_THUMB, _vec_corr(a.get("thumb"), b.get("thumb"))),
        (W_HIST, _vec_corr(a.get("hist"), b.get("hist"))),
    ]
    num = sum(w * s for w, s in parts if s is not None)
    den = sum(w for w, s in parts if s is not None)
    return float(np.clip(num / den, 0.0, 1.0)) if den > 0 else 0.0


def _fp_list(fp) -> list[dict]:
    """Accept a single fingerprint, {"fps": [...]}, a list, or a JSON string of any of these."""
    if fp is None:
        return []
    if isinstance(fp, (str, bytes)):
        try:
            fp = json.loads(fp)
        except ValueError:
            return []
    if isinstance(fp, list):
        return [f for f in fp if isinstance(f, dict) and f]
    if isinstance(fp, dict):
        if "fps" in fp:
            return [f for f in fp.get("fps") or [] if isinstance(f, dict) and f]
        return [fp] if fp else []
    return []


def similarity(fp_a: dict, fp_b: dict) -> float:
    """Appearance similarity 0..1 (1 = identical). If either side holds several stored
    fingerprints ({"fps": [...]}) the best pair is used."""
    la, lb = _fp_list(fp_a), _fp_list(fp_b)
    if not la or not lb:
        return 0.0
    return max(_single_similarity(a, b) for a in la for b in lb)


def merge_fingerprints(existing, fp: dict) -> dict:
    """Return the embedding to store: latest fingerprints first, at most MAX_STORED_FPS."""
    fps = [f for f in [fp] + _fp_list(existing) if f]
    return {"fps": fps[:MAX_STORED_FPS]}


def save_fingerprint(conn: sqlite3.Connection, defect_id: int, fp: dict) -> None:
    """Add fp to defects.embedding (keeps the latest MAX_STORED_FPS views). Call after
    creating a defect or attaching a new observation to it."""
    if not fp:
        return
    r = conn.execute("SELECT embedding FROM defects WHERE id=?", (defect_id,)).fetchone()
    if r is None:
        return
    conn.execute("UPDATE defects SET embedding=? WHERE id=?",
                 (json.dumps(merge_fingerprints(r[0], fp)), defect_id))


# --------------------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------------------
def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres (local copy so dedup does not depend on geo.py)."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def class_compat(a: str | None, b: str | None) -> float:
    """1.0 same class, <1 compatible classes, 0 incompatible."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return CLASS_COMPAT.get(frozenset([a, b]), 0.0)


def match_defect(conn: sqlite3.Connection, damage_class: str, lat: float | None, lon: float | None,
                 fp: dict, exclude_ids: set[int] | None = None) -> tuple[int | None, dict]:
    """Find an existing physical defect this detection duplicates.

    Layers: (1) GPS radius <= 15 m, (2) compatible damage class, (3) image similarity.
      <= 5 m  & compatible -> match if weighted similarity >= 0.45
      5-15 m  & compatible -> match if weighted similarity >= 0.60
      no GPS (either side) -> match only if similarity >= 0.92
    Repaired defects are candidates too (so a reappearing pothole is linked -> bad repair);
    rejected ones are not. `exclude_ids`: defects already matched in this inspection (so two
    different potholes in one frame never merge). Returns (defect_id | None, match_info).
    """
    exclude_ids = exclude_ids or set()
    has_gps = lat is not None and lon is not None
    rows = conn.execute(
        "SELECT id, code, damage_class, lat, lon, embedding FROM defects "
        "WHERE COALESCE(status,'open') != 'rejected' AND COALESCE(workflow,'reported') != 'rejected'"
    ).fetchall()

    best = None  # (score, defect_id, distance, sim, reason, code)
    n_cand = 0
    best_sim_seen = 0.0
    for r in rows:
        did = r["id"]
        if did in exclude_ids:
            continue
        w = class_compat(damage_class, r["damage_class"])
        if w <= 0:
            continue
        cand_gps = r["lat"] is not None and r["lon"] is not None
        dist = haversine_m(lat, lon, r["lat"], r["lon"]) if (has_gps and cand_gps) else None
        if dist is not None and dist > MATCH_RADIUS_M:
            continue
        n_cand += 1
        stored = _fp_list(r["embedding"])
        have_fp = bool(stored) and bool(_fp_list(fp))
        sim = similarity(fp, {"fps": stored}) if have_fp else 0.0
        eff = sim * w
        best_sim_seen = max(best_sim_seen, sim)

        reason = None
        if dist is None:
            if have_fp and eff >= SIM_NO_GPS:
                reason = "no GPS: near-identical image"
                score = eff
        elif not have_fp:
            if dist <= GPS_ONLY_RADIUS_M and w == 1.0:
                reason = f"same class within {GPS_ONLY_RADIUS_M:.0f} m (no image fingerprint)"
                score = 0.5 * (1 - dist / MATCH_RADIUS_M)
        elif dist <= NEAR_RADIUS_M and eff >= SIM_NEAR:
            reason = f"within {NEAR_RADIUS_M:.0f} m + similar image"
            score = 0.7 * eff + 0.3 * (1 - dist / MATCH_RADIUS_M)
        elif eff >= SIM_FAR:
            reason = f"within {MATCH_RADIUS_M:.0f} m + very similar image"
            score = 0.7 * eff + 0.3 * (1 - dist / MATCH_RADIUS_M)
        if reason and w < 1.0:
            reason += f" (compatible class {r['damage_class']})"
        if reason and (best is None or score > best[0]):
            best = (score, did, dist, sim, reason, r["code"])

    if best is None:
        if n_cand == 0:
            reason = "new: no compatible defect within 15 m" if has_gps else "new: no compatible defect"
        elif not has_gps:
            reason = "new: no GPS and no near-identical image"
        else:
            reason = "new: nearby defects look different"
        return None, {"reason": reason, "distance_m": None, "similarity": round(best_sim_seen, 3),
                      "candidates": n_cand}
    _, did, dist, sim, reason, code = best
    return did, {"reason": reason, "distance_m": None if dist is None else round(dist, 2),
                 "similarity": round(sim, 3), "candidates": n_cand, "defect_code": code}


# --------------------------------------------------------------------------------------
# Progress monitoring
# --------------------------------------------------------------------------------------
def _parse_ts(s) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _observations(conn: sqlite3.Connection, defect_id: int) -> list[sqlite3.Row]:
    """Non-rejected observations, oldest first."""
    return conn.execute(
        "SELECT * FROM observations WHERE defect_id=? AND COALESCE(review_status,'pending') != 'rejected' "
        "ORDER BY observed_at, id", (defect_id,)).fetchall()


def compute_trend(prev: dict, latest: dict) -> str:
    """worsening | improving | stable from two sightings (severity_score, area_ratio)."""
    worse = better = False
    ps, ls = prev.get("severity_score"), latest.get("severity_score")
    ds = None
    if ps is not None and ls is not None:
        ds = float(ls) - float(ps)
        worse |= ds > SEVERITY_DELTA
        better |= ds < -SEVERITY_DELTA
    pa, la = prev.get("area_ratio"), latest.get("area_ratio")
    if pa is not None and la is not None and float(pa) > 0:
        rel = (float(la) - float(pa)) / float(pa)
        worse |= rel > AREA_REL_DELTA
        better |= rel < -AREA_REL_DELTA
    if worse and better:  # signals disagree -> severity decides, else call it stable
        if ds is not None and ds > SEVERITY_DELTA:
            return "worsening"
        if ds is not None and ds < -SEVERITY_DELTA:
            return "improving"
        return "stable"
    return "worsening" if worse else "improving" if better else "stable"


def update_progress(conn: sqlite3.Connection, defect_id: int) -> str:
    """Compare the latest vs the previous non-absent sighting (from an earlier inspection when
    possible) and write defects.trend. Returns new | worsening | improving | stable, or the
    current absence trend (possibly_repaired/repaired) if the latest record is an absence.
    Also runs check_bad_repair() when a repaired defect is seen again."""
    obs = _observations(conn, defect_id)
    d = conn.execute("SELECT trend FROM defects WHERE id=?", (defect_id,)).fetchone()
    if d is None:
        return "new"
    if obs and obs[-1]["absent"]:
        return d["trend"] or "new"
    present = [o for o in obs if not o["absent"]]
    if not present:
        return d["trend"] or "new"
    check_bad_repair(conn, defect_id)
    latest = present[-1]
    prev = None
    for o in reversed(present[:-1]):
        if latest["inspection_id"] is None or o["inspection_id"] != latest["inspection_id"]:
            prev = o
            break
    trend = "new" if prev is None else compute_trend(dict(prev), dict(latest))
    conn.execute("UPDATE defects SET trend=? WHERE id=?", (trend, defect_id))
    return trend


def mark_absent(conn: sqlite3.Connection, lat: float, lon: float, inspection_id: int,
                seen_defect_ids: set[int], radius_m: float = ABSENT_RADIUS_M) -> list[int]:
    """A location was re-inspected: every open defect within radius_m that was NOT seen gets an
    absent observation. 1st consecutive absence -> trend possibly_repaired; 2nd consecutive (or
    workflow already 'repaired') -> trend repaired, status repaired (repaired_at set if empty).
    Idempotent per inspection. Returns the defect ids marked absent."""
    if lat is None or lon is None:
        return []
    seen = set(seen_defect_ids or ())
    insp = conn.execute("SELECT created_at FROM inspections WHERE id=?", (inspection_id,)).fetchone()
    ts = (insp["created_at"] if insp and insp["created_at"] else None) or _now_iso()
    rows = conn.execute(
        "SELECT id, damage_class, lat, lon, workflow, status, trend, repaired_at FROM defects "
        "WHERE lat IS NOT NULL AND lon IS NOT NULL AND COALESCE(status,'open') != 'rejected' "
        "AND COALESCE(workflow,'reported') NOT IN ('rejected','closed') AND COALESCE(trend,'') != 'repaired'"
    ).fetchall()
    marked = []
    for r in rows:
        if r["id"] in seen or haversine_m(lat, lon, r["lat"], r["lon"]) > radius_m:
            continue
        if conn.execute("SELECT 1 FROM observations WHERE defect_id=? AND inspection_id=?",
                        (r["id"], inspection_id)).fetchone():
            continue  # already recorded (seen or absent) in this inspection
        info = {"reason": "location re-inspected, defect not detected",
                "distance_m": round(haversine_m(lat, lon, r["lat"], r["lon"]), 2)}
        conn.execute(
            "INSERT INTO observations (defect_id, inspection_id, observed_at, damage_class, lat, lon, "
            "absent, match_info, review_status, reviewer) VALUES (?,?,?,?,?,?,1,?,'confirmed','auto:absence')",
            (r["id"], inspection_id, ts, r["damage_class"], lat, lon, json.dumps(info)))
        consecutive = 0
        for o in reversed(_observations(conn, r["id"])):
            if not o["absent"]:
                break
            consecutive += 1
        if consecutive >= 2 or r["workflow"] == "repaired":
            conn.execute("UPDATE defects SET trend='repaired', status='repaired', "
                         "repaired_at=COALESCE(repaired_at, ?) WHERE id=?", (ts, r["id"]))
        else:
            conn.execute("UPDATE defects SET trend='possibly_repaired' WHERE id=?", (r["id"],))
        marked.append(r["id"])
    return marked


def check_bad_repair(conn: sqlite3.Connection, defect_id: int) -> bool:
    """If a repaired defect has a non-absent sighting after repaired_at: reopen it
    (status open, workflow reported). If that sighting is within 90 days of the repair,
    set bad_repair=1 and return True."""
    d = conn.execute("SELECT status, workflow, trend, repaired_at, bad_repair FROM defects WHERE id=?",
                     (defect_id,)).fetchone()
    if d is None:
        return False
    is_repaired = d["status"] == "repaired" or d["workflow"] in ("repaired", "closed") or d["trend"] == "repaired"
    rep = _parse_ts(d["repaired_at"])
    if not is_repaired or rep is None:
        return bool(d["bad_repair"])
    after = [o for o in _observations(conn, defect_id)
             if not o["absent"] and (_parse_ts(o["observed_at"]) or datetime.min) > rep]
    if not after:
        return bool(d["bad_repair"])
    first = _parse_ts(after[0]["observed_at"])
    bad = (first - rep).days < BAD_REPAIR_DAYS
    conn.execute("UPDATE defects SET status='open', workflow='reported', trend='worsening', "
                 "bad_repair=? WHERE id=?", (1 if bad or d["bad_repair"] else 0, defect_id))
    return bad or bool(d["bad_repair"])
