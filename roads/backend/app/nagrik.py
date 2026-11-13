"""Civic data from Nagrik (https://nagrik.in.net/api/v1, public, rate-limited -> everything cached).

  resolve(lat, lon)            -> ward, corporation, relevant officials (road engineers, control room)
  ward_works(city, ward_id)    -> road / pothole tenders for that ward (city-wide pothole works as fallback)

Never raises: offline or unsupported city -> None / [].
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx

BASE = "https://nagrik.in.net/api/v1"
CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "cache"
USER_AGENT = "NagrikRoads-hackathon/0.1"
TIMEOUT_S = 8.0
CITIES = ("BLR", "DEL", "GGN", "GZB", "MUM", "PUN")
WORKS_TTL_S = 24 * 3600
MAX_OFFICIALS = 4
MAX_WORKS = 8
NEG_CACHE_S = 600   # after a failed request, skip Nagrik entirely for 10 min (returns cache / None / [])
_down_until = 0.0
ROAD_WORDS = re.compile(r"pothole|road|asphalt|tarring|resurfac|white.?top|footpath|carriageway|street|junction|"
                        r"flyover|drain|culvert|patch", re.I)

# Offline fallback for /cities bounds (copied from the live API, 2026-11)
_FALLBACK_BOUNDS = {
    "BLR": {"min_lng": 77.42988, "min_lat": 12.803491, "max_lng": 77.814064, "max_lat": 13.17262},
    "DEL": {"min_lng": 76.8, "min_lat": 28.36, "max_lng": 77.39, "max_lat": 28.92},
    "GGN": {"min_lng": 76.85, "min_lat": 28.29, "max_lng": 77.19, "max_lat": 28.56},
}


# ---------------------------------------------------------------- cache + http
def _cache_get(name: str, ttl_s: float | None = None):
    try:
        obj = json.loads((CACHE_DIR / f"nagrik_{name}.json").read_text(encoding="utf-8"))
        if ttl_s is not None and time.time() - obj.get("_ts", 0) > ttl_s:
            return None
        return obj["data"]
    except Exception:
        return None


def _cache_put(name: str, data) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / f"nagrik_{name}.json").write_text(
            json.dumps({"_ts": time.time(), "data": data}, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _request(method: str, path: str, **kw):
    """One HTTP call (<=8 s). Network errors/5xx/429 trip a 10-min global backoff."""
    global _down_until
    if time.time() < _down_until:
        return None
    try:
        r = httpx.request(method, BASE + path, timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT}, **kw)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429 or r.status_code >= 500:
            _down_until = time.time() + NEG_CACHE_S
    except Exception:
        _down_until = time.time() + NEG_CACHE_S
    return None


def _bounds() -> dict:
    cached = _cache_get("cities")
    if cached is None:
        data = _request("GET", "/cities")
        if isinstance(data, list):
            cached = {c["city"]: c["bounds"] for c in data if c.get("city") in CITIES and c.get("bounds")}
            _cache_put("cities", cached)
    return cached or _FALLBACK_BOUNDS


def city_for(lat: float, lon: float) -> str | None:
    """Nagrik city code whose bounding box contains the point (only supported cities)."""
    for code, b in _bounds().items():
        if b["min_lat"] <= lat <= b["max_lat"] and b["min_lng"] <= lon <= b["max_lng"]:
            return code
    return None


# ---------------------------------------------------------------- resolve
def _pick_officials(officials: list[dict], ward: dict) -> list[dict]:
    """Road engineers whose source area names this ward, then control room / commissioner."""
    name = (ward.get("name") or "").lower()
    num = ward.get("ward_number")
    tag = f"{num}: {name}" if num is not None else None

    def covers(o):
        a = (o.get("address") or "").lower()
        return bool(name) and (name in a or (tag and tag in a))

    def pack(o):
        return {"department": o.get("department"), "name": o.get("name"), "phone": o.get("phone")}

    out, seen = [], set()

    def push(o):
        k = (o.get("name"), o.get("phone"))
        if o.get("phone") and k not in seen and len(out) < MAX_OFFICIALS:
            seen.add(k)
            out.append(pack(o))

    road = [o for o in officials if "road" in (o.get("department") or "").lower()]
    for o in road:
        if covers(o):
            push(o)
    for dept in ("control room", "executive engineer", "commissioner"):
        for o in officials:
            if (o.get("department") or "").lower().startswith(dept):
                push(o)
                break
    if not out:  # nothing matched the ward: fall back to first road engineers
        for o in road[:2]:
            push(o)
    return out


def resolve(lat: float, lon: float) -> dict | None:
    """{city, ward_id, ward_name, corporation, officials: [{department, name, phone}]} or None."""
    try:
        if lat is None or lon is None:
            return None
        city = city_for(lat, lon)
        if city is None:
            return None
        key = f"resolve_{city}_{round(lat, 4):.4f}_{round(lon, 4):.4f}"
        cached = _cache_get(key)
        if cached is not None:
            return cached
        data = _request("POST", "/resolve", json={"lat": lat, "lng": lon, "city": city})
        if not isinstance(data, dict) or not data.get("ward"):
            return None
        ward = data["ward"]
        out = {
            "city": city,
            "ward_id": ward.get("ward_id"),
            "ward_name": ward.get("name"),
            "ward_number": ward.get("ward_number"),
            "corporation": data.get("corporation_name") or ward.get("corporation"),
            "officials": _pick_officials(data.get("officials") or [], ward),
            "helpline": next((h.get("phone") for h in data.get("helplines") or [] if "GBA" in (h.get("name") or "")
                              or "general" in (h.get("description") or "").lower()), None),
        }
        _cache_put(key, out)
        return out
    except Exception:
        return None


# ---------------------------------------------------------------- works / tenders
def _pack_work(p: dict, scope: str) -> dict:
    return {
        "title": p.get("title"),
        "agency": p.get("executing_agency"),
        "tender_ref": p.get("tender_ref"),
        "status": p.get("status"),
        "tender_date": p.get("tender_date"),
        "budget": p.get("budget") or p.get("awarded_budget"),
        "contractor_name": p.get("contractor_name"),
        "source_url": p.get("source_url"),
        "ward_name": p.get("ward_name"),
        "scope": scope,  # "ward" = this ward, "city" = city-wide fallback
    }


def _projects(city: str, **params) -> list[dict] | None:
    data = _request("GET", "/projects", params={"city": city, "per_page": 20, **params})
    if isinstance(data, dict):
        return data.get("items") or []
    return None


def ward_works(city: str, ward_id: int | None, query: str = "pothole") -> list[dict]:
    """Road/pothole works for a ward: q=<query> -> issue=roads -> road-keyword filter of all ward works
    -> city-wide q=<query> (scope="city"). Cached 24 h (stale cache used offline)."""
    try:
        if not city or city not in CITIES:
            return []
        key = f"works_{city}_{ward_id}_{re.sub(r'[^a-z0-9]+', '_', (query or '').lower())}"
        fresh = _cache_get(key, ttl_s=WORKS_TTL_S)
        if fresh is not None:
            return fresh
        result, online = [], False
        if ward_id:
            for params in ({"q": query}, {"issue": "roads"}, {}):
                items = _projects(city, ward_id=ward_id, **params)
                if items is None:
                    break
                online = True
                items = [p for p in items if ROAD_WORDS.search((p.get("title") or "") + " " + (p.get("scope_of_work") or ""))]
                if items:
                    result = [_pack_work(p, "ward") for p in items]
                    break
        if not result:
            items = _projects(city, q=query, per_page=MAX_WORKS)
            if items is not None:
                online = True
                result = [_pack_work(p, "city") for p in items]
        if not online:
            stale = _cache_get(key)
            return stale if stale is not None else []
        result = result[:MAX_WORKS]
        _cache_put(key, result)
        return result
    except Exception:
        return []
