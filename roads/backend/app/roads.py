"""Road health: colour whole ROADS by condition, not just individual defect pins (idea: iWatchRoad).

  GET /api/roads/health  ->  {source, segments: [...worst first], defects: [...pins], summary}

How a segment is built
  1. Open defects with GPS are snapped to the nearest OpenStreetMap road (Overpass API, free, cached on disk
     per 0.01 deg tile under <data>/cache/overpass_*.json) within SNAP_M metres. Each affected OSM way becomes
     one segment with its real geometry ("geometry": "osm").
  2. Defects that could not be snapped (Overpass unreachable, or no road within SNAP_M) are grouped by
     `road_name` (or by a ~150 m grid cell when the name is missing). The segment is the polyline through
     those defects ordered along their principal axis ("geometry": "approx").

Health score (0-100, 100 = perfect), per segment
    density      = defect_count / max(length_km, 0.1)            defects per km (length floored at 100 m so a
                                                                 20 m OSM stub with one pothole is not "50/km")
    density_pen  = 40 * min(1, density / 20)                     20+ defects per km = full penalty
    risk_pen     = 35 * (0.6 * max_risk + 0.4 * mean_risk) / 100 risk_score is 0-100; a defect without a risk
                                                                 score uses its severity_score instead
    severity_pen = 25 * worst_severity / 100                     severity_score 0-100 (label used if score missing)
    health       = clamp(100 - density_pen - risk_pen - severity_pen, 0, 100)
  Label: Good >= 70, Fair 40-69, Poor < 40.

Never raises on network errors: Overpass down -> approx segments from cache / defect positions only.
"""
from __future__ import annotations

import json
import logging
import math
import time

import httpx
from fastapi import APIRouter

from . import db

log = logging.getLogger("roadsense.roads")
router = APIRouter(prefix="/api")

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "NagrikRoads-hackathon/1.0"
TIMEOUT_S = 20.0
CACHE_DIR = db.DATA_DIR / "cache"          # backend/data/cache in the real app, isolated in tests
CACHE_TTL_S = 30 * 24 * 3600               # roads do not move: a month is fine
NEG_CACHE_S = 300                          # after a failed Overpass call, do not retry for 5 min
_down_until = 0.0

TILE_DEG = 0.01                            # ~1.1 km cache tile
TILE_PAD_DEG = 0.0006                      # ~65 m so roads just outside the tile can still be snapped to
MAX_TILES_PER_REQUEST = 40                 # keep the Overpass query small; the rest is fetched on later calls
SNAP_M = 30.0
MIN_LEN_KM = 0.1
GRID_M = 150.0                             # fallback cell for defects without a road name
GAP_SPLIT_M = 400.0                        # same road_name but > 400 m apart -> separate approx segments
HIGHWAYS = ("motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service|road|"
            "motorway_link|trunk_link|primary_link|secondary_link|tertiary_link")
SEVERITY_FALLBACK = {"low": 25.0, "medium": 55.0, "high": 80.0, "critical": 95.0}
M_PER_DEG = 111_320.0


# ---------------------------------------------------------------- geometry (metres, small areas)
def _hav_m(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6_371_000.0 * math.asin(min(1.0, math.sqrt(h)))


def _length_m(coords) -> float:
    return sum(_hav_m(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def _xy(lat, lon, lat0, lon0):
    """Local equirectangular metres around (lat0, lon0)."""
    return (lon - lon0) * M_PER_DEG * math.cos(math.radians(lat0)), (lat - lat0) * M_PER_DEG


def point_to_polyline_m(lat: float, lon: float, coords) -> float:
    """Shortest distance in metres from a point to a polyline [[lat, lon], ...]."""
    if not coords:
        return float("inf")
    pts = [_xy(c[0], c[1], lat, lon) for c in coords]
    if len(pts) == 1:
        return math.hypot(*pts[0])
    best = float("inf")
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        dx, dy = x2 - x1, y2 - y1
        den = dx * dx + dy * dy
        t = 0.0 if den == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / den))
        best = min(best, math.hypot(x1 + t * dx, y1 + t * dy))
    return best


# ---------------------------------------------------------------- Overpass + tile cache
def _tile_of(lat: float, lon: float) -> tuple[int, int]:
    return math.floor(lat / TILE_DEG), math.floor(lon / TILE_DEG)


def _tile_bbox(tile) -> tuple[float, float, float, float]:
    """(south, west, north, east) of a tile, padded."""
    i, j = tile
    return (i * TILE_DEG - TILE_PAD_DEG, j * TILE_DEG - TILE_PAD_DEG,
            (i + 1) * TILE_DEG + TILE_PAD_DEG, (j + 1) * TILE_DEG + TILE_PAD_DEG)


def _cache_path(tile):
    return CACHE_DIR / f"overpass_{tile[0] * TILE_DEG:.2f}_{tile[1] * TILE_DEG:.2f}.json"


def _cache_get(tile):
    try:
        obj = json.loads(_cache_path(tile).read_text(encoding="utf-8"))
        if time.time() - obj.get("_ts", 0) > CACHE_TTL_S:
            return None
        return obj["data"]
    except Exception:
        return None


def _cache_put(tile, data) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(tile).write_text(json.dumps({"_ts": time.time(), "data": data}, ensure_ascii=False),
                                     encoding="utf-8")
    except Exception:
        pass


def fetch_overpass(bboxes: list[tuple[float, float, float, float]]) -> list[dict]:
    """Drivable OSM ways inside any of the (south, west, north, east) boxes.

    Returns [{"id", "name", "highway", "coords": [[lat, lon], ...]}]. Raises on any network/HTTP problem
    (callers catch). Tests monkeypatch this function.
    """
    parts = "".join(f'way["highway"~"^({HIGHWAYS})$"]({s:.5f},{w:.5f},{n:.5f},{e:.5f});' for s, w, n, e in bboxes)
    query = f"[out:json][timeout:{int(TIMEOUT_S)}];({parts});out tags geom;"
    r = httpx.post(OVERPASS_URL, data={"data": query}, timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    ways = []
    for el in r.json().get("elements", []):
        geom = [[g["lat"], g["lon"]] for g in el.get("geometry") or [] if g]
        if el.get("type") == "way" and len(geom) >= 2:
            tags = el.get("tags") or {}
            ways.append({"id": el["id"], "name": tags.get("name") or tags.get("ref"),
                         "highway": tags.get("highway"), "coords": geom})
    return ways


def _way_touches(way: dict, bbox) -> bool:
    s, w, n, e = bbox
    c = way["coords"]
    for (la1, lo1), (la2, lo2) in zip(c, c[1:]):
        if max(la1, la2) >= s and min(la1, la2) <= n and max(lo1, lo2) >= w and min(lo1, lo2) <= e:
            return True
    return False


def _ways_for(tiles: dict) -> dict:
    """{tile: [ways]} for every tile we have (cache or a fresh Overpass call). Missing tiles are simply absent.

    `tiles` maps tile -> number of defects in it (busiest tiles are fetched first).
    """
    global _down_until
    out, missing = {}, []
    for t in tiles:
        cached = _cache_get(t)
        if cached is None:
            missing.append(t)
        else:
            out[t] = cached
    if missing and time.time() >= _down_until:
        batch = sorted(missing, key=lambda t: -tiles[t])[:MAX_TILES_PER_REQUEST]
        try:
            ways = fetch_overpass([_tile_bbox(t) for t in batch])
            for t in batch:
                bb = _tile_bbox(t)
                out[t] = [w for w in ways if _way_touches(w, bb)]
                _cache_put(t, out[t])
        except Exception as e:  # offline, timeout, 429, bad JSON...
            _down_until = time.time() + NEG_CACHE_S
            log.warning("Overpass unavailable (%s): using approximate road segments", str(e)[:160])
    return out


# ---------------------------------------------------------------- scoring
def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _severity(d: dict) -> float | None:
    s = _num(d.get("severity_score"))
    return s if s is not None else SEVERITY_FALLBACK.get(str(d.get("severity_label") or "").lower())


def health_score(defect_count: int, length_m: float, max_risk: float, mean_risk: float, worst_severity: float):
    """(health 0-100, label, density per km). See the module docstring for the formula."""
    density = defect_count / max(length_m / 1000.0, MIN_LEN_KM)
    pen = (40.0 * min(1.0, density / 20.0)
           + 35.0 * (0.6 * max_risk + 0.4 * mean_risk) / 100.0
           + 25.0 * worst_severity / 100.0)
    health = max(0.0, min(100.0, 100.0 - pen))
    label = "Good" if health >= 70 else "Fair" if health >= 40 else "Poor"
    return round(health, 1), label, round(density, 1)


def _segment(seg_id: str, name, highway, coords, defects: list[dict], geometry: str) -> dict:
    length = _length_m(coords)
    sev = [s for s in (_severity(d) for d in defects) if s is not None]
    risks = []
    for d in defects:
        r = _num(d.get("risk_score"))
        r = r if r is not None else _severity(d)
        if r is not None:
            risks.append(r)
    max_risk = max(risks, default=0.0)
    mean_risk = sum(risks) / len(risks) if risks else 0.0
    worst = max(sev, default=0.0)
    worst_d = max(defects, key=lambda d: _severity(d) or 0.0)
    by_class: dict[str, int] = {}
    for d in defects:
        k = d.get("damage_class") or "unknown"
        by_class[k] = by_class.get(k, 0) + 1
    health, label, density = health_score(len(defects), length, max_risk, mean_risk, worst)
    return {
        "id": seg_id, "name": name, "highway": highway, "geometry": geometry,
        "length_m": round(length, 1), "coordinates": [[round(c[0], 6), round(c[1], 6)] for c in coords],
        "defect_count": len(defects), "density_per_km": density,
        "max_risk": round(max_risk, 1), "mean_risk": round(mean_risk, 1),
        "worst_severity": round(worst, 1), "worst_severity_label": worst_d.get("severity_label"),
        "by_class": by_class, "health": health, "label": label,
        "ward_name": next((d["ward_name"] for d in defects if d.get("ward_name")), None),
        "defect_ids": [d["id"] for d in defects],
    }


# ---------------------------------------------------------------- grouping
def _snap(defects: list[dict], ways_by_tile: dict):
    """-> ({way_id: (way, [defects])}, [unsnapped defects])"""
    hit: dict = {}
    rest = []
    pad = SNAP_M / M_PER_DEG * 1.6  # generous lat/lon prefilter box
    boxes: dict = {}
    for d in defects:
        best, best_m = None, SNAP_M
        for w in ways_by_tile.get(_tile_of(d["lat"], d["lon"]), ()):
            bb = boxes.get(w["id"])
            if bb is None:
                la = [c[0] for c in w["coords"]]
                lo = [c[1] for c in w["coords"]]
                bb = boxes[w["id"]] = (min(la), min(lo), max(la), max(lo))
            if not (bb[0] - pad <= d["lat"] <= bb[2] + pad and bb[1] - pad <= d["lon"] <= bb[3] + pad):
                continue
            m = point_to_polyline_m(d["lat"], d["lon"], w["coords"])
            if m <= best_m:
                best, best_m = w, m
        if best is None:
            rest.append(d)
        else:
            hit.setdefault(best["id"], (best, []))[1].append(d)
    return hit, rest


def _order_along_axis(defects: list[dict]) -> list[dict]:
    """Sort defects along their principal axis (2x2 PCA in local metres)."""
    if len(defects) < 2:
        return list(defects)
    lat0 = sum(d["lat"] for d in defects) / len(defects)
    lon0 = sum(d["lon"] for d in defects) / len(defects)
    pts = [_xy(d["lat"], d["lon"], lat0, lon0) for d in defects]
    sxx = sum(x * x for x, _ in pts)
    syy = sum(y * y for _, y in pts)
    sxy = sum(x * y for x, y in pts)
    ang = 0.5 * math.atan2(2 * sxy, sxx - syy)
    ux, uy = math.cos(ang), math.sin(ang)
    return [d for _, d in sorted(zip((x * ux + y * uy for x, y in pts), defects), key=lambda p: p[0])]


def _approx_segments(defects: list[dict]) -> list[dict]:
    """Offline fallback: group by road_name (else ~150 m grid cell), polyline through the defects."""
    groups: dict = {}
    for d in defects:
        name = (d.get("road_name") or "").strip()
        if name:
            key = ("name", name.lower())
        else:
            cell_lat = GRID_M / M_PER_DEG
            cell_lon = cell_lat / max(0.2, math.cos(math.radians(d["lat"])))
            key = ("cell", math.floor(d["lat"] / cell_lat), math.floor(d["lon"] / cell_lon))
        groups.setdefault(key, []).append(d)
    segs = []
    for key, ds in groups.items():
        ordered = _order_along_axis(ds)
        runs = [[ordered[0]]]
        for a, b in zip(ordered, ordered[1:]):
            if key[0] == "name" and _hav_m((a["lat"], a["lon"]), (b["lat"], b["lon"])) > GAP_SPLIT_M:
                runs.append([])
            runs[-1].append(b)
        for run in runs:
            name = next((d["road_name"] for d in run if d.get("road_name")), None)
            rtype = next((d["road_type"] for d in run if d.get("road_type") and d["road_type"] != "unknown"), None)
            coords = [[d["lat"], d["lon"]] for d in run]
            if len(coords) == 1:  # one pin: a zero-length polyline so the client can still draw/zoom to it
                coords = coords * 2
            segs.append(_segment(f"approx-{min(d['id'] for d in run)}", name, rtype, coords, run, "approx"))
    return segs


def open_defects() -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, code, damage_class, lat, lon, status, severity_score, severity_label, risk_score, "
            "risk_label, road_type, road_name, workflow, ward_name FROM defects "
            "WHERE lat IS NOT NULL AND lon IS NOT NULL "
            "AND COALESCE(status,'open') NOT IN ('repaired','rejected') "
            "AND COALESCE(workflow,'reported') NOT IN ('repaired','closed','rejected')").fetchall()
    return [dict(r) for r in rows]


def road_health(use_osm: bool = True) -> dict:
    defects = open_defects()
    segments, rest, source = [], defects, "approx"
    if defects and use_osm:
        tiles: dict = {}
        for d in defects:
            t = _tile_of(d["lat"], d["lon"])
            tiles[t] = tiles.get(t, 0) + 1
        try:
            ways_by_tile = _ways_for(tiles)
        except Exception as e:  # belt and braces: this endpoint must never 500 because of the network
            log.warning("road lookup failed: %s", e)
            ways_by_tile = {}
        if ways_by_tile:
            hit, rest = _snap(defects, ways_by_tile)
            for way, ds in hit.values():
                segments.append(_segment(f"osm-{way['id']}", way.get("name"), way.get("highway"),
                                         way["coords"], ds, "osm"))
            if segments:
                source = "osm" if not rest else "mixed"
    segments += _approx_segments(rest)
    segments.sort(key=lambda s: (s["health"], -s["defect_count"], -s["max_risk"]))
    seg_of = {i: s["id"] for s in segments for i in s["defect_ids"]}
    labels = {"Good": 0, "Fair": 0, "Poor": 0}
    for s in segments:
        labels[s["label"]] += 1
    return {
        "source": source,  # osm | mixed | approx
        "segments": segments,
        "defects": [{"id": d["id"], "code": d["code"], "lat": d["lat"], "lon": d["lon"],
                     "damage_class": d["damage_class"], "risk_score": d["risk_score"],
                     "risk_label": d["risk_label"], "severity_label": d["severity_label"],
                     "segment_id": seg_of.get(d["id"])} for d in defects],
        "summary": {"segments": len(segments), "defects": len(defects), "by_label": labels,
                    "km_assessed": round(sum(s["length_m"] for s in segments) / 1000.0, 2)},
        "thresholds": {"Good": 70, "Fair": 40, "Poor": 0},
    }


@router.get("/roads/health")
def roads_health(osm: bool = True):
    """Road segments with a 0-100 health score, worst first. `?osm=false` skips OpenStreetMap (offline mode)."""
    return road_health(use_osm=osm)
