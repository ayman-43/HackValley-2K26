"""Repair-crew tools: optimised repair route + "repaired by" crew ID.

Route planning
--------------
POST /api/crew/route picks defects (explicit ids, or the top-N open defects by
risk), then asks the free public OSRM server for the fastest driving order
(OSRM `trip` service = travelling-salesman heuristic on the real road graph).
If OSRM is unreachable (offline demo), it falls back to a nearest-neighbour
order with straight-line distances and says so (`engine: "offline-estimate"`).

Inspired by the Pothole Pilot route planner (TKU hackathon) - independent
implementation, no code copied.
"""
import logging
import math
from typing import Any, Optional
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Body, HTTPException

from . import db

log = logging.getLogger("roadsense.crew")
router = APIRouter(prefix="/api")

OSRM_URL = "https://router.project-osrm.org/trip/v1/driving/"
MAX_STOPS = 25                      # public OSRM demo server limit is generous, keep requests small
DEFAULT_DEPOT = {"lat": 12.9667, "lon": 77.5871, "name": "BBMP head office, N R Square"}
# rough on-site repair time per defect (minutes) for the crew day plan
REPAIR_MIN = {"pothole": 35, "alligator_crack": 30, "transverse_crack": 15, "longitudinal_crack": 15}
OFFLINE_SPEED_KMH = 18.0            # Bengaluru average traffic speed for the offline estimate
ROAD_FACTOR = 1.35                  # straight line -> road distance


# ---------------------------------------------------------------- schema
def ensure_columns() -> None:
    """Add crew columns to an existing database (idempotent)."""
    with db.connect() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(defects)")}
        if "repaired_by" not in cols:
            c.execute("ALTER TABLE defects ADD COLUMN repaired_by TEXT")


try:
    ensure_columns()
except Exception as e:  # never block API start-up
    log.warning("crew columns not added: %s", e)


# ---------------------------------------------------------------- helpers
def haversine_km(a: dict, b: dict) -> float:
    r = 6371.0
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _point(v: Any, name: str) -> Optional[dict]:
    if v is None:
        return None
    try:
        lat, lon = float(v["lat"]), float(v.get("lon", v.get("lng")))
    except (TypeError, KeyError, ValueError):
        raise HTTPException(400, f"{name} must be {{lat, lon}}")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise HTTPException(400, f"{name} out of range")
    return {"lat": lat, "lon": lon, "name": v.get("name") if isinstance(v, dict) else None}


def _select_defects(ids: Optional[list], top_n: int, include_unverified: bool) -> list[dict]:
    with db.connect() as c:
        if ids:
            ids = [int(i) for i in ids][:MAX_STOPS]
            q = f"SELECT * FROM defects WHERE id IN ({','.join('?' * len(ids))}) AND lat IS NOT NULL AND lon IS NOT NULL"
            rows = [db.row(r) for r in c.execute(q, ids)]
        else:
            flows = ("reported", "verified", "scheduled") if include_unverified else ("verified", "scheduled")
            q = (f"SELECT * FROM defects WHERE lat IS NOT NULL AND lon IS NOT NULL "
                 f"AND COALESCE(workflow,'reported') IN ({','.join('?' * len(flows))}) "
                 f"AND COALESCE(status,'open') NOT IN ('repaired','rejected') "
                 f"ORDER BY COALESCE(risk_score,-1) DESC, COALESCE(severity_score,0) DESC LIMIT ?")
            rows = [db.row(r) for r in c.execute(q, (*flows, max(1, min(top_n, MAX_STOPS))))]
    return rows


def _osrm_trip(points: list[dict], roundtrip: bool) -> dict:
    coords = ";".join(f"{p['lon']:.6f},{p['lat']:.6f}" for p in points)
    params = {"source": "first", "roundtrip": "true" if roundtrip else "false",
              "geometries": "geojson", "overview": "full", "steps": "false"}
    if not roundtrip:
        params["destination"] = "last"
    with httpx.Client(timeout=20, headers={"User-Agent": "NagrikRoads-hackathon/1.0"}) as cl:
        r = cl.get(OSRM_URL + coords, params=params)
    r.raise_for_status()
    js = r.json()
    if js.get("code") != "Ok":
        raise RuntimeError(js.get("message") or js.get("code"))
    trip = js["trips"][0]
    # waypoints[i].waypoint_index = position of input point i in the trip
    order = sorted(range(len(points)), key=lambda i: js["waypoints"][i]["waypoint_index"])
    legs = [{"km": l["distance"] / 1000, "min": l["duration"] / 60} for l in trip["legs"]]
    return {
        "order": order,
        "legs": legs,
        "path": [[lat, lon] for lon, lat in trip["geometry"]["coordinates"]],
        "km": trip["distance"] / 1000,
        "min": trip["duration"] / 60,
    }


def _offline_trip(points: list[dict], roundtrip: bool) -> dict:
    """Nearest-neighbour order from the depot; straight-line legs scaled to road distance."""
    n = len(points)
    last = n - 1 if not roundtrip else None
    todo = set(range(1, n)) - ({last} if last is not None else set())
    order, cur = [0], 0
    while todo:
        nxt = min(todo, key=lambda j: haversine_km(points[cur], points[j]))
        order.append(nxt)
        todo.remove(nxt)
        cur = nxt
    if last is not None:
        order.append(last)
    seq = order + ([0] if roundtrip else [])
    legs = []
    for a, b in zip(seq, seq[1:]):
        km = haversine_km(points[a], points[b]) * ROAD_FACTOR
        legs.append({"km": km, "min": km / OFFLINE_SPEED_KMH * 60})
    return {
        "order": order,
        "legs": legs,
        "path": [[points[i]["lat"], points[i]["lon"]] for i in seq],
        "km": sum(l["km"] for l in legs),
        "min": sum(l["min"] for l in legs),
    }


def _civic(d: dict) -> dict:
    """Ward engineer + latest road tender for a defect, from the Nagrik data stored by pipeline.py."""
    ng = d.get("nagrik") if isinstance(d.get("nagrik"), dict) else {}
    officials = ng.get("officials") or []
    road = [o for o in officials if "road" in (o.get("department") or "").lower()]
    officer = (road or officials or [None])[0]
    control = next((o for o in officials if "control room" in (o.get("department") or "").lower()), None)
    works = ng.get("works") or []
    tender = works[0] if works else None
    return {
        "officer": officer,
        "control_room": control,
        "tender": {k: tender.get(k) for k in ("title", "tender_ref", "status", "agency", "contractor_name",
                                              "source_url")} if tender else None,
    }


def _ward_summary(stops: list[dict]) -> list[dict]:
    """Group stops by ward so each ward engineer gets their own section of the crew sheet."""
    wards: dict[str, dict] = {}
    for s in stops:
        key = s.get("ward_name") or "Ward not resolved"
        w = wards.setdefault(key, {"ward_name": key, "corporation": s.get("corporation"),
                                   "officer": s.get("officer"), "control_room": s.get("control_room"),
                                   "tender": s.get("tender"), "stops": [], "repair_min": 0})
        w["stops"].append(s["seq"])
        w["repair_min"] += s["repair_min"]
        for k in ("officer", "control_room", "tender", "corporation"):
            w[k] = w[k] or s.get(k)
    return list(wards.values())


def _nav_url(start: dict, stops: list[dict], end: dict) -> str:
    """Google Maps directions link (no key). Google honours ~9 waypoints, so long routes are split by the UI."""
    pts = [start] + stops[:9] + [end]
    return "https://www.google.com/maps/dir/" + "/".join(quote(f"{p['lat']:.6f},{p['lon']:.6f}", safe=",") for p in pts)


# ---------------------------------------------------------------- endpoints
@router.post("/crew/route")
def plan_route(body: dict = Body(default={})) -> dict:
    """Plan an optimised repair round.

    body: {defect_ids?: [int], top_n?: int=10, include_unverified?: bool=true,
           start?: {lat, lon, name?}, end?: {lat, lon} (default = back to start)}
    """
    start = _point(body.get("start"), "start") or dict(DEFAULT_DEPOT)
    end = _point(body.get("end"), "end")
    roundtrip = end is None
    defects = _select_defects(body.get("defect_ids"), int(body.get("top_n") or 10),
                              bool(body.get("include_unverified", True)))
    if not defects:
        raise HTTPException(404, "no open defects with a GPS location match this selection")

    pts = [start] + [{"lat": d["lat"], "lon": d["lon"]} for d in defects] + ([end] if end else [])
    engine = "osrm"
    try:
        trip = _osrm_trip(pts, roundtrip)
    except Exception as e:
        log.warning("OSRM unavailable (%s) - using offline estimate", e)
        trip = _offline_trip(pts, roundtrip)
        engine = "offline-estimate"

    stops, t_drive, t_cum, km_cum = [], 0.0, 0.0, 0.0
    visit = [i for i in trip["order"] if 1 <= i <= len(defects)]
    for n, i in enumerate(visit):
        d = defects[i - 1]
        leg = trip["legs"][n] if n < len(trip["legs"]) else {"km": 0, "min": 0}
        t_drive += leg["min"]
        km_cum += leg["km"]
        work = REPAIR_MIN.get(d.get("damage_class"), 20)
        t_cum += leg["min"]
        stops.append({
            "seq": n + 1, "id": d["id"], "code": d.get("code"), "damage_class": d.get("damage_class"),
            "lat": d["lat"], "lon": d["lon"],
            "risk_score": d.get("risk_score"), "risk_label": d.get("risk_label"),
            "severity_label": d.get("severity_label"), "workflow": d.get("workflow") or "reported",
            "road_name": d.get("road_name"), "ward_name": d.get("ward_name"),
            "corporation": d.get("corporation"),
            "leg_km": round(leg["km"], 2), "leg_min": round(leg["min"], 1),
            "arrive_min": round(t_cum, 1), "repair_min": work,
            **_civic(d),  # Nagrik: ward road engineer, control room, latest road tender
        })
        t_cum += work

    repair_total = sum(s["repair_min"] for s in stops)
    return {
        "engine": engine,
        "roundtrip": roundtrip,
        "start": start,
        "end": end or start,
        "stops": stops,
        "wards": _ward_summary(stops),
        "path": trip["path"],
        "distance_km": round(trip["km"], 2),
        "drive_min": round(trip["min"], 1),
        "repair_min": repair_total,
        "total_min": round(trip["min"] + repair_total, 1),
        "nav_url": _nav_url(start, stops, end or start),
        "nav_note": "Google Maps link covers the first 9 stops" if len(stops) > 9 else None,
        "unverified_stops": sum(1 for s in stops if s["workflow"] == "reported"),
    }


@router.post("/defects/{defect_id}/repaired_by")
def set_repaired_by(defect_id: int, body: dict = Body(...)) -> dict:
    """Record which crew / employee ID did the repair (shown on the defect page)."""
    crew = str(body.get("crew_id") or "").strip()[:64]
    if not crew:
        raise HTTPException(400, "crew_id is required")
    with db.connect() as c:
        if c.execute("SELECT 1 FROM defects WHERE id=?", (defect_id,)).fetchone() is None:
            raise HTTPException(404, "defect not found")
        c.execute("UPDATE defects SET repaired_by=? WHERE id=?", (crew, defect_id))
    return {"id": defect_id, "repaired_by": crew}
