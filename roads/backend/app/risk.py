"""Risk scoring, deterioration forecast and road/weather context.

External data (free, no keys, everything cached in backend/data/cache/):
  * OpenStreetMap Overpass API -> road class, name, speed limit, lanes, nearby schools/hospitals/bus stops
  * Open-Meteo forecast API    -> rain in the next 72 h, current temperature

The risk score is a transparent ADDITIVE formula (0..100); every point is explained in
`risk_factors`, so an officer can see exactly why a defect ranks where it does.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime
from pathlib import Path

import httpx

CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "cache"
USER_AGENT = "NagrikRoads-hackathon/0.1"
TIMEOUT_S = 8.0
OVERPASS_URLS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
                 "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
NEG_CACHE_S = 600          # after a failed lookup, return defaults instantly for 10 min (no re-blocking)
OVERPASS_BACKOFF_S = NEG_CACHE_S
NEAR_CACHE_M = 60          # reuse a cached road context within this distance when offline
_overpass_down_until = 0.0  # global: all Overpass mirrors failed recently
_weather_down_until = 0.0   # global: Open-Meteo failed recently
_neg_cache: dict[str, float] = {}  # per grid cell: "osm:<key>" / "weather:<key>" -> retry-after time
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_TTL_S = 3600
ROAD_SEARCH_M = 50          # nearest carriageway within this distance (GPS error ~10-30 m)
SENSITIVE_RADIUS_M = 300

# ---------------------------------------------------------------- risk weights (max points per factor)
W_SEVERITY = 40       # severity_score 0..100 scaled linearly (x damage-class factor below)
W_ROAD_CLASS = 15     # by OSM highway class (ROAD_CLASS_POINTS)
W_SPEED = 10          # speed limit: <=20 km/h -> 0 ... >=80 km/h -> 10 (linear)
W_SENSITIVE = 10      # schools/hospitals/bus stops within 300 m (closer + more = more points)
W_REPORTS = 10        # report_count, log scale: 1 -> 0, 2 -> 3, 4 -> 6, 10+ -> 10
W_RAIN = 10           # rain in next 72 h: 0 mm -> 0, >=30 mm -> 10 (linear)
W_WORSENING = 5       # trend == worsening
W_BAD_REPAIR = 5      # defect reappeared within 90 days of a recorded repair
# total max = 40+15+10+10+10+10+5+5 = 105 -> clipped to 100

# Damage-class factor on the severity term (a pothole is a bigger hazard than a hairline crack
# at the same visual score). Severity module already weights class; this keeps cracks modest.
CLASS_FACTOR = {"pothole": 1.0, "alligator_crack": 0.85, "transverse_crack": 0.7, "longitudinal_crack": 0.6}

ROAD_CLASS_POINTS = {
    "motorway": 15, "trunk": 15, "primary": 13, "secondary": 10, "tertiary": 7,
    "unclassified": 5, "residential": 3, "service": 2, "living_street": 1, "unknown": 5,
}
# Indian urban defaults (km/h) when OSM has no maxspeed tag
DEFAULT_SPEED = {
    "motorway": 100, "trunk": 80, "primary": 60, "secondary": 50, "tertiary": 40,
    "unclassified": 30, "residential": 30, "service": 20, "living_street": 15, "unknown": 40,
}
SENSITIVE_WEIGHT = {"school": 5, "hospital": 5, "bus_stop": 3}

RISK_LABELS = [(75, "Critical"), (55, "High"), (35, "Medium"), (0, "Low")]

# Forecast heuristics (NOT a validated model): water infiltration accelerates pothole growth.
HIGH_SEVERITY = 60.0
RAIN_BOOST_MM = 10.0       # rain above this in next 72 h boosts growth
RAIN_BOOST_FACTOR = 1.5

_ROAD_MAP = {
    "motorway": "motorway", "motorway_link": "motorway", "trunk": "trunk", "trunk_link": "trunk",
    "primary": "primary", "primary_link": "primary", "secondary": "secondary", "secondary_link": "secondary",
    "tertiary": "tertiary", "tertiary_link": "tertiary", "residential": "residential",
    "living_street": "living_street", "service": "service", "unclassified": "unclassified", "road": "unclassified",
}
_ROAD_RANK = ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential",
              "living_street", "service"]


# ---------------------------------------------------------------- cache helpers
def _cache_path(kind: str, key: str) -> Path:
    return CACHE_DIR / f"{kind}_{key}.json"


def _cache_get(kind: str, key: str, ttl_s: float | None = None) -> dict | None:
    """Return cached payload, or None. ttl_s=None means never expires."""
    p = _cache_path(kind, key)
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        if ttl_s is not None and time.time() - obj.get("_ts", 0) > ttl_s:
            return None
        return obj["data"]
    except Exception:
        return None


def _cache_put(kind: str, key: str, data) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(kind, key).write_text(json.dumps({"_ts": time.time(), "data": data}, ensure_ascii=False),
                                          encoding="utf-8")
    except Exception:
        pass


def _key(lat: float, lon: float) -> str:
    return f"{round(lat, 4):.4f}_{round(lon, 4):.4f}"


def _dist_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _point_seg_m(lat, lon, a, b) -> float:
    """Distance (m) from point to segment a-b using a local equirectangular projection."""
    kx = 111320.0 * math.cos(math.radians(lat))
    ky = 110540.0
    ax, ay = (a[1] - lon) * kx, (a[0] - lat) * ky
    bx, by = (b[1] - lon) * kx, (b[0] - lat) * ky
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L))
    return math.hypot(ax + t * dx, ay + t * dy)


# ---------------------------------------------------------------- road context (Overpass)
def _default_context() -> dict:
    return {"road_type": "unknown", "road_name": None, "speed_limit": DEFAULT_SPEED["unknown"],
            "speed_source": "default", "lanes": None, "sensitive": [], "source": "default"}


def _parse_speed(v: str | None) -> int | None:
    if not v:
        return None
    try:
        num = float(v.split()[0].split(";")[0])
        return int(round(num * 1.609)) if "mph" in v else int(round(num))
    except Exception:
        return None


def _overpass(query: str, timeout_s: float = TIMEOUT_S) -> dict | None:
    """Try mirrors in order; `timeout_s` is the TOTAL budget across all mirrors."""
    global _overpass_down_until
    if time.time() < _overpass_down_until:
        return None
    deadline = time.monotonic() + timeout_s - 1.0  # httpx timeouts are per phase -> keep a safety margin
    for url in OVERPASS_URLS:
        left = deadline - time.monotonic()
        if left < 0.5:
            break
        try:
            r = httpx.post(url, data={"data": query}, timeout=httpx.Timeout(left, connect=min(3.0, left)),
                           headers={"User-Agent": USER_AGENT})
            if r.status_code == 200:
                return r.json()
        except Exception:
            continue
    _overpass_down_until = time.time() + OVERPASS_BACKOFF_S
    return None


def _nearby_cached_context(lat: float, lon: float) -> dict | None:
    """Closest cached road context within NEAR_CACHE_M (GPS jitter around a cached point)."""
    best, best_d = None, NEAR_CACHE_M
    try:
        for p in CACHE_DIR.glob("osm_*.json"):
            try:
                la, lo = (float(x) for x in p.stem[4:].split("_"))
            except ValueError:
                continue
            d = _dist_m(lat, lon, la, lo)
            if d <= best_d:
                best, best_d = p, d
        if best:
            return json.loads(best.read_text(encoding="utf-8"))["data"]
    except Exception:
        pass
    return None


def _parse_context(lat: float, lon: float, data: dict) -> dict:
    best, best_d = None, 1e9
    sensitive = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if el["type"] == "way" and "highway" in tags and el.get("geometry"):
            geom = [(g["lat"], g["lon"]) for g in el["geometry"]]
            d = min((_point_seg_m(lat, lon, geom[i], geom[i + 1]) for i in range(len(geom) - 1)),
                    default=_dist_m(lat, lon, *geom[0]))
            cls = _ROAD_MAP.get(tags["highway"])
            if cls is None:  # footway, path, cycleway... not a carriageway
                continue
            # prefer the closest; break near-ties (<5 m) toward the more important road
            rank = _ROAD_RANK.index(cls) if cls in _ROAD_RANK else 99
            score = d + rank * 0.5
            if d <= ROAD_SEARCH_M and score < best_d:
                best, best_d = (cls, tags, d), score
            continue
        kind = None
        a = tags.get("amenity")
        if a in ("school", "college", "university", "kindergarten"):
            kind = "school"
        elif a in ("hospital", "clinic"):
            kind = "hospital"
        elif tags.get("highway") == "bus_stop" or tags.get("public_transport") == "platform" and tags.get("bus") == "yes":
            kind = "bus_stop"
        if kind:
            plat = el.get("lat") or el.get("center", {}).get("lat")
            plon = el.get("lon") or el.get("center", {}).get("lon")
            if plat is None:
                continue
            d = _dist_m(lat, lon, plat, plon)
            if d <= SENSITIVE_RADIUS_M:
                sensitive.append({"kind": kind, "name": tags.get("name") or tags.get("name:en") or kind.replace("_", " ").title(),
                                  "distance_m": round(d)})
    # dedupe (same name+kind, e.g. bus stop on both sides of the road) keep nearest
    seen, uniq = set(), []
    for s in sorted(sensitive, key=lambda s: s["distance_m"]):
        k = (s["kind"], s["name"])
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    ctx = _default_context()
    ctx["source"] = "osm"
    ctx["sensitive"] = uniq[:12]
    if best:
        cls, tags, d = best
        sp = _parse_speed(tags.get("maxspeed"))
        lanes = None
        try:
            lanes = int(str(tags.get("lanes", "")).split(";")[0]) if tags.get("lanes") else None
        except ValueError:
            pass
        ctx.update(road_type=cls, road_name=tags.get("name") or tags.get("ref"),
                   speed_limit=sp or DEFAULT_SPEED.get(cls, 40), speed_source="osm" if sp else "default",
                   lanes=lanes, road_distance_m=round(d, 1))
    return ctx


def road_context(lat: float, lon: float, timeout_s: float = TIMEOUT_S) -> dict:
    """Nearest OSM road (<=50 m) + sensitive places (<=300 m). Cached forever by ~11 m grid.
    Overpass is often slow (20-30 s); on timeout we use a cached context within 60 m, else defaults.
    `timeout_s` can be raised for one-off cache pre-filling.

    Returns {road_type, road_name, speed_limit, lanes, sensitive: [{kind, name, distance_m}], source}.
    """
    if lat is None or lon is None:
        return _default_context()
    key = _key(lat, lon)
    cached = _cache_get("osm", key)
    if cached is not None:
        return cached
    if time.time() < _neg_cache.get("osm:" + key, 0):
        return _nearby_cached_context(lat, lon) or _default_context()
    q = f"""[out:json][timeout:25];
way(around:{ROAD_SEARCH_M},{lat},{lon})[highway~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|service|road)(_link)?$"];
out tags geom;
(
  nwr(around:{SENSITIVE_RADIUS_M},{lat},{lon})[amenity~"^(school|college|university|kindergarten|hospital|clinic)$"];
  node(around:{SENSITIVE_RADIUS_M},{lat},{lon})[highway=bus_stop];
);
out tags center;"""
    data = _overpass(q, timeout_s)
    if data is None:
        _neg_cache["osm:" + key] = time.time() + NEG_CACHE_S
        return _nearby_cached_context(lat, lon) or _default_context()
    ctx = _parse_context(lat, lon, data)
    _cache_put("osm", key, ctx)
    return ctx


# ---------------------------------------------------------------- weather (Open-Meteo)
def _default_weather() -> dict:
    return {"rain_next_72h_mm": 0.0, "rain_days": 0, "temp_c": None, "daily_mm": [], "source": "default"}


def weather(lat: float, lon: float) -> dict:
    """Rain forecast for the next 3 days + current temperature. Cached 1 h (stale cache used offline).

    Returns {rain_next_72h_mm, rain_days, temp_c, daily_mm, source}.
    """
    if lat is None or lon is None:
        return _default_weather()
    key = f"{round(lat, 2):.2f}_{round(lon, 2):.2f}"  # weather varies on km scale
    fresh = _cache_get("weather", key, ttl_s=WEATHER_TTL_S)
    if fresh is not None:
        return fresh
    global _weather_down_until
    try:
        if time.time() < max(_weather_down_until, _neg_cache.get("weather:" + key, 0)):
            raise RuntimeError("weather negative-cached")
        r = httpx.get(OPEN_METEO_URL, timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT}, params={
            "latitude": lat, "longitude": lon, "daily": "precipitation_sum",
            "current": "temperature_2m", "forecast_days": 3, "timezone": "Asia/Kolkata"})
        r.raise_for_status()
        j = r.json()
        daily = [float(x or 0.0) for x in j.get("daily", {}).get("precipitation_sum", [])][:3]
        w = {"rain_next_72h_mm": round(sum(daily), 1), "rain_days": sum(1 for x in daily if x >= 1.0),
             "temp_c": j.get("current", {}).get("temperature_2m"), "daily_mm": daily, "source": "open-meteo"}
        _cache_put("weather", key, w)
        return w
    except Exception as e:
        if not isinstance(e, RuntimeError):
            _weather_down_until = _neg_cache["weather:" + key] = time.time() + NEG_CACHE_S
        stale = _cache_get("weather", key)
        if stale is not None:
            return {**stale, "source": "cache_stale"}
        return _default_weather()


# ---------------------------------------------------------------- risk score
def risk_label(score: float) -> str:
    for th, lab in RISK_LABELS:
        if score >= th:
            return lab
    return "Low"


def compute_risk(defect: dict, context: dict | None, weather: dict | None) -> dict:
    """Additive, explainable 0..100 risk score. See W_* constants for the weight table."""
    context = context or _default_context()
    weather = weather or _default_weather()
    factors = []

    def add(name: str, detail: str, pts: float):
        factors.append({"factor": name, "detail": detail, "points": round(pts, 1)})

    # 1. severity
    sev = float(defect.get("severity_score") or 0.0)
    cls = defect.get("damage_class") or "pothole"
    cf = CLASS_FACTOR.get(cls, 0.8)
    add("severity", f"{cls.replace('_', ' ')} severity {sev:.0f}/100"
        f" ({defect.get('severity_label') or risk_label(sev)})", W_SEVERITY * min(sev, 100) / 100 * cf)

    # 2. road class
    rt = context.get("road_type") or "unknown"
    name = context.get("road_name")
    add("road_class", f"{rt} road" + (f" ({name})" if name else "") +
        (" - from OpenStreetMap" if context.get("source") == "osm" else " - road type unknown (default)"),
        ROAD_CLASS_POINTS.get(rt, ROAD_CLASS_POINTS["unknown"]))

    # 3. speed
    sp = context.get("speed_limit") or DEFAULT_SPEED.get(rt, 40)
    add("speed", f"speed limit {sp} km/h" + (" (default for road class)" if context.get("speed_source") != "osm" else ""),
        W_SPEED * max(0.0, min(1.0, (sp - 20) / 60)))

    # 4. sensitive places: weight / (1 + d/100), capped
    sens = context.get("sensitive") or []
    raw = sum(SENSITIVE_WEIGHT.get(s["kind"], 2) / (1 + s.get("distance_m", 300) / 100) for s in sens)
    if sens:
        near = sorted(sens, key=lambda s: s.get("distance_m", 999))[:3]
        detail = ", ".join(f"{s['kind'].replace('_', ' ')} '{s['name']}' {s.get('distance_m')} m" for s in near)
        if len(sens) > 3:
            detail += f" (+{len(sens) - 3} more)"
    else:
        detail = "no school/hospital/bus stop within 300 m"
    add("sensitive_places", detail, min(W_SENSITIVE, raw))

    # 5. report count (log scale)
    n = max(1, int(defect.get("report_count") or 1))
    add("reports", f"seen in {n} report{'s' if n != 1 else ''}", min(W_REPORTS, W_REPORTS * math.log10(n)))

    # 6. rain
    rain = float(weather.get("rain_next_72h_mm") or 0.0)
    add("rain_forecast", f"{rain:.0f} mm rain expected in next 72 h" +
        (" (forecast unavailable)" if weather.get("source") == "default" else ""), W_RAIN * min(1.0, rain / 30))

    # 7. trend / bad repair
    if defect.get("trend") == "worsening":
        add("worsening", "defect grew since last inspection", W_WORSENING)
    if defect.get("bad_repair"):
        add("bad_repair", "reappeared within 90 days of a recorded repair", W_BAD_REPAIR)

    score = round(max(0.0, min(100.0, sum(f["points"] for f in factors))), 1)
    return {"risk_score": score, "risk_label": risk_label(score), "risk_factors": factors}


# ---------------------------------------------------------------- forecast
def _parse_ts(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def forecast(observations: list[dict], weather: dict | None) -> dict:
    """Linear growth of severity_score over time (+ rain boost heuristic) -> days until High (60).

    Heuristic, not a validated model: rain > 10 mm in 72 h multiplies growth by 1.5 (water infiltration).
    """
    weather = weather or _default_weather()
    rain = float(weather.get("rain_next_72h_mm") or 0.0)
    rain_txt = f"rain expected: {rain:.0f} mm" if rain > 0 else "no rain forecast"
    pts = []
    for o in observations or []:
        if o.get("absent"):
            continue
        t = _parse_ts(o.get("observed_at") or o.get("ts") or o.get("date"))
        s = o.get("severity_score")
        if t is not None and s is not None:
            pts.append((t, float(s)))
    pts.sort()
    latest = pts[-1][1] if pts else None

    if len(pts) < 2 or pts[-1][0] - pts[0][0] < 3600:  # need two dated, distinct observations
        if latest is not None and latest >= HIGH_SEVERITY:
            text = f"Already High severity ({latest:.0f}/100)."
        elif rain > RAIN_BOOST_MM:
            text = f"Single observation; {rain:.0f} mm rain expected in 72 h - water infiltration may enlarge it quickly. Re-inspect after the rain."
        else:
            text = f"Single observation ({rain_txt}); re-inspect to measure growth."
        return {"days_to_high": None, "text": text, "growth_per_week": None}

    # least-squares slope (severity points per day)
    t0 = pts[0][0]
    xs = [(t - t0) / 86400 for t, _ in pts]
    ys = [s for _, s in pts]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 0.0
    boosted = slope > 0 and rain > RAIN_BOOST_MM
    eff = slope * (RAIN_BOOST_FACTOR if boosted else 1.0)
    gpw = round(eff * 7, 2)

    if latest >= HIGH_SEVERITY:
        return {"days_to_high": None, "growth_per_week": gpw,
                "text": f"Already High severity ({latest:.0f}/100); growing {gpw:+.1f} pts/week."}
    if eff <= 0.05 / 7:
        return {"days_to_high": None, "growth_per_week": gpw,
                "text": f"Not growing ({gpw:+.1f} pts/week, {rain_txt})."}
    days = max(1, math.ceil((HIGH_SEVERITY - latest) / eff))
    text = f"Likely to reach High severity in ~{days} days ({rain_txt})"
    if boosted:
        text += "; growth boosted x1.5 for rain (heuristic)"
    return {"days_to_high": days, "text": text + ".", "growth_per_week": gpw}
