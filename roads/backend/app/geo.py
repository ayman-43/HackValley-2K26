"""GPS helpers: EXIF location, GPS track loading (GPX/CSV/JSON/NMEA), interpolation, distance."""
import csv
import json
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _ratio(v) -> float:
    try:
        return float(v)
    except TypeError:
        n, d = v
        return float(n) / float(d)


def exif_gps(path) -> tuple[float, float] | None:
    """(lat, lon) from a photo's EXIF GPS tags, or None."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            gps = im.getexif().get_ifd(0x8825)
        if not gps or 2 not in gps or 4 not in gps:
            return None

        def dms(v):
            d, m, s = (_ratio(x) for x in v)
            return d + m / 60 + s / 3600

        lat, lon = dms(gps[2]), dms(gps[4])
        if str(gps.get(1, "N")).upper().startswith("S"):
            lat = -lat
        if str(gps.get(3, "E")).upper().startswith("W"):
            lon = -lon
        if lat == 0 and lon == 0 or abs(lat) > 90 or abs(lon) > 180:
            return None
        return (lat, lon)
    except Exception:
        return None


def _time_value(v) -> float | None:
    """Number of seconds, or ISO timestamp converted to epoch seconds."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _normalise(points: list[dict]) -> list[dict]:
    """Sort by time and make t relative to the first point (handles epoch s / ms)."""
    pts = [p for p in points if p.get("lat") is not None and p.get("lon") is not None]
    if not pts:
        return []
    if all(p.get("t") is None for p in pts):  # no timestamps: assume 1 point per second
        for i, p in enumerate(pts):
            p["t"] = float(i)
    pts = [p for p in pts if p.get("t") is not None]
    pts.sort(key=lambda p: p["t"])
    t0 = pts[0]["t"]
    if t0 > 1e11:  # epoch milliseconds
        for p in pts:
            p["t"] /= 1000.0
        t0 = pts[0]["t"]
    if t0 > 1e8:  # absolute epoch seconds -> relative
        for p in pts:
            p["t"] -= t0
    return [{"t": float(p["t"]), "lat": float(p["lat"]), "lon": float(p["lon"])} for p in pts]


def _pick(d: dict, *keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _from_dicts(items) -> list[dict]:
    out = []
    for d in items:
        if not isinstance(d, dict):
            continue
        d = {str(k).strip().lower(): v for k, v in d.items()}
        lat = _pick(d, "lat", "latitude")
        lon = _pick(d, "lon", "lng", "long", "longitude")
        if lat is None or lon is None:
            continue
        try:
            out.append({"t": _time_value(_pick(d, "t", "time", "timestamp", "ts", "seconds")),
                        "lat": float(lat), "lon": float(lon)})
        except (TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------- NMEA 0183 (dashcam / GPS logger text)
_NMEA_RE = re.compile(r"\$([A-Z]{2})(RMC|GGA),([^*\r\n$]*)(?:\*([0-9A-Fa-f]{2}))?")


def _nmea_coord(value: str, hemi: str) -> float | None:
    """'ddmm.mmmm' / 'dddmm.mmmm' + hemisphere -> signed decimal degrees."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    deg = int(v // 100)
    minutes = v - deg * 100
    if minutes >= 60:
        return None
    out = deg + minutes / 60.0
    return -out if hemi.strip().upper() in ("S", "W") else out


def _nmea_tod(value: str) -> float | None:
    """'hhmmss(.sss)' -> seconds of day."""
    try:
        if len(value) < 6:
            return None
        return int(value[0:2]) * 3600 + int(value[2:4]) * 60 + float(value[4:])
    except ValueError:
        return None


def _nmea_date(value: str) -> int | None:
    """'ddmmyy' -> days since the Unix epoch (UTC)."""
    try:
        if len(value) != 6:
            return None
        yy = int(value[4:6])
        d = datetime((1900 if yy >= 80 else 2000) + yy, int(value[2:4]), int(value[0:2]), tzinfo=timezone.utc)
        return int(d.timestamp() // 86400)
    except ValueError:
        return None


def _parse_nmea(text: str) -> list[dict]:
    """RMC/GGA sentences (any talker: GP, GN, GL, ...) -> [{t, lat, lon, speed_kmh?, alt?, time?}].

    t = seconds since the first valid fix. Sentences with a wrong checksum, RMC status 'V' or
    GGA fix quality 0 are skipped. The date comes from RMC; GGA-only logs use time of day and
    handle a midnight rollover. RMC and GGA of the same instant are merged into one point.
    """
    fixes = []  # {tod, day, lat, lon, speed_kmh, alt}
    for m in _NMEA_RE.finditer(text):
        kind, body, cks = m.group(2), m.group(3), m.group(4)
        if cks is not None:
            x = 0
            for ch in m.group(1) + kind + "," + body:
                x ^= ord(ch)
            if x != int(cks, 16):
                continue
        f = [""] + body.split(",")  # f[1] = first field after the sentence id
        fix = {"day": None, "speed_kmh": None, "alt": None}
        try:
            if kind == "RMC":
                if len(f) < 7 or f[2].strip().upper() != "A":
                    continue
                lat, lon = _nmea_coord(f[3], f[4]), _nmea_coord(f[5], f[6])
                if len(f) > 7 and f[7]:
                    fix["speed_kmh"] = round(float(f[7]) * 1.852, 2)  # knots -> km/h
                if len(f) > 9:
                    fix["day"] = _nmea_date(f[9])
            else:  # GGA
                if len(f) < 7 or not f[6].strip().isdigit() or int(f[6]) == 0:
                    continue
                lat, lon = _nmea_coord(f[2], f[3]), _nmea_coord(f[4], f[5])
                if len(f) > 9 and f[9]:
                    fix["alt"] = float(f[9])
        except ValueError:
            continue
        tod = _nmea_tod(f[1])
        if tod is None or lat is None or lon is None:
            continue
        if abs(lat) > 90 or abs(lon) > 180 or (lat == 0 and lon == 0):
            continue
        fixes.append({**fix, "tod": tod, "lat": lat, "lon": lon})
    if not fixes:
        return []
    # absolute seconds: carry the RMC date forward (and back to leading GGA), roll over at midnight
    first_dated = next((i for i, x in enumerate(fixes) if x["day"] is not None), None)
    day = fixes[first_dated]["day"] if first_dated is not None else 0
    if first_dated:  # GGA sentences before the first RMC: same day unless they are just before midnight
        for x in fixes[:first_dated]:
            x["day"] = day - 1 if x["tod"] - fixes[first_dated]["tod"] > 43200 else day
    prev_tod = None
    for x in fixes[first_dated or 0:]:
        if x["day"] is not None:
            day = x["day"]
        elif prev_tod is not None and prev_tod - x["tod"] > 43200:
            day += 1
        x["day"] = day
        prev_tod = x["tod"]
    merged: dict[float, dict] = {}
    for x in fixes:
        key = round(x["day"] * 86400 + x["tod"], 3)
        cur = merged.setdefault(key, x)
        for k in ("speed_kmh", "alt"):
            if cur.get(k) is None and x.get(k) is not None:
                cur[k] = x[k]
    keys = sorted(merged)
    out = []
    for k in keys:
        x = merged[k]
        p = {"t": round(k - keys[0], 3), "lat": x["lat"], "lon": x["lon"]}
        if x["speed_kmh"] is not None:
            p["speed_kmh"] = x["speed_kmh"]
        if x["alt"] is not None:
            p["alt"] = x["alt"]
        if first_dated is not None:
            p["time"] = datetime.fromtimestamp(k, tz=timezone.utc).isoformat(timespec="milliseconds")
        out.append(p)
    return out


def load_track(path) -> list[dict]:
    """Load a GPS track as [{t, lat, lon}] with t = seconds from video start. Empty list on failure.

    Formats: GPX, JSON, CSV and NMEA 0183 text (.nmea / .txt / .log with $GPRMC/$GNRMC/$GPGGA/$GNGGA).
    NMEA points may also carry speed_kmh / alt / time.
    """
    try:
        p = Path(path)
        text = p.read_text(encoding="utf-8-sig", errors="ignore").strip()
        if not text:
            return []
        if _NMEA_RE.search(text):  # NMEA sentences never appear in GPX/JSON/CSV tracks
            pts = _parse_nmea(text)
            if pts:
                return pts
        if text.startswith("<"):  # GPX
            root = ET.fromstring(text)
            pts = []
            for el in root.iter():
                tag = el.tag.split("}")[-1]
                if tag in ("trkpt", "rtept", "wpt") and "lat" in el.attrib:
                    t = None
                    for ch in el:
                        if ch.tag.split("}")[-1] == "time":
                            t = _time_value(ch.text)
                    pts.append({"t": t, "lat": float(el.attrib["lat"]), "lon": float(el.attrib["lon"])})
            return _normalise(pts)
        if text[0] in "[{":  # JSON
            data = json.loads(text)
            if isinstance(data, dict):
                data = _pick(data, "points", "track", "gps", "coords", "data") or []
            return _normalise(_from_dicts(data))
        # CSV with or without header
        rows = list(csv.reader(text.splitlines()))
        if not rows:
            return []
        try:
            [float(x) for x in rows[0][:3]]
            header = None
        except ValueError:
            header = [h.strip().lower() for h in rows[0]]
            rows = rows[1:]
        if header:
            return _normalise(_from_dicts([dict(zip(header, r)) for r in rows]))
        pts = []
        for r in rows:
            if len(r) >= 3:
                pts.append({"t": _time_value(r[0]), "lat": float(r[1]), "lon": float(r[2])})
            elif len(r) == 2:
                pts.append({"t": None, "lat": float(r[0]), "lon": float(r[1])})
        return _normalise(pts)
    except Exception:
        return []


def interpolate(track: list[dict], t: float) -> tuple[float, float] | None:
    """Linear interpolation of (lat, lon) at time t (seconds). Clamps to the track ends."""
    if not track:
        return None
    if t <= track[0]["t"]:
        return (track[0]["lat"], track[0]["lon"])
    if t >= track[-1]["t"]:
        return (track[-1]["lat"], track[-1]["lon"])
    lo, hi = 0, len(track) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if track[mid]["t"] <= t:
            lo = mid
        else:
            hi = mid
    a, b = track[lo], track[hi]
    span = b["t"] - a["t"]
    f = 0.0 if span <= 0 else (t - a["t"]) / span
    return (a["lat"] + f * (b["lat"] - a["lat"]), a["lon"] + f * (b["lon"] - a["lon"]))


def sample_track(track: list[dict], every_m: float = 20.0) -> list[tuple[float, float]]:
    """Points along the track roughly `every_m` metres apart (for re-inspection checks)."""
    out: list[tuple[float, float]] = []
    for p in track:
        if not out or haversine_m(out[-1][0], out[-1][1], p["lat"], p["lon"]) >= every_m:
            out.append((p["lat"], p["lon"]))
    if track and (track[-1]["lat"], track[-1]["lon"]) != out[-1]:
        out.append((track[-1]["lat"], track[-1]["lon"]))
    return out
