"""GPS embedded inside dashcam / action-cam / phone videos.

Many cameras store a GPS sample stream inside the MP4/MOV itself: GoPro (GPMF), Novatek-based
dashcams such as Viofo and 70mai ('gps ' / 'free' atoms), Garmin, BlackVue, Insta360, DJI, and
phones (one location for the whole clip). ExifTool (https://exiftool.org, free) decodes all of
these with its `-ee` (extract embedded) option, so this module shells out to it when it is on
PATH (or at env ROADSENSE_EXIFTOOL) and converts the result to the track format used by geo.py:
[{t, lat, lon, ...}] with t = seconds from video start.

Without ExifTool the function returns [] and the pipeline falls back to the uploaded GPS track
file (GPX/JSON/CSV/NMEA) or the form latitude/longitude.

Future work (not implemented): OCR of the coordinates some dashcams burn into the picture.
"""
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("roadsense.dashcam")

TIMEOUT_S = 60
TAGS = ("GPSLatitude", "GPSLongitude", "GPSDateTime", "SampleTime", "GPSSpeed", "GPSAltitude")
_warned_missing = False


def exiftool_path() -> str | None:
    """ExifTool executable (env ROADSENSE_EXIFTOOL or PATH), or None."""
    env = os.environ.get("ROADSENSE_EXIFTOOL")
    if env and Path(env).exists():
        return env
    return shutil.which("exiftool")


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _datetime_s(v) -> float | None:
    """ExifTool 'YYYY:MM:DD HH:MM:SS(.sss)(Z|+hh:mm)' -> epoch seconds (UTC assumed if no zone)."""
    if not v:
        return None
    s = str(v).strip()
    try:
        date, _, rest = s.partition(" ")
        d = datetime.fromisoformat(date.replace(":", "-") + "T" + rest.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except ValueError:
        return None


def parse_exiftool_json(data) -> list[dict]:
    """Output of `exiftool -ee -n -j -G3` -> track. Never raises.

    With -G3 every embedded sample is its own sub-document, so keys look like
    'Doc12:GPSLatitude'; tags of the file itself are 'Main:...' (or have no prefix).
    t comes from SampleTime (seconds from video start) when every sample has it, else from
    GPSDateTime relative to the first sample, else one sample per second is assumed.
    A video with only a single file-level location (phones) gives a one-point track.
    """
    try:
        if isinstance(data, (str, bytes)):
            data = json.loads(data)
        if isinstance(data, list):
            data = data[0] if data else {}
        if not isinstance(data, dict):
            return []
        docs: dict[int, dict] = {}  # 0 = main document
        for key, val in data.items():
            group, _, tag = str(key).rpartition(":")
            if tag not in TAGS:
                continue
            n = 0
            for g in group.split(":"):
                if g.startswith("Doc") and g[3:].isdigit():
                    n = int(g[3:])
            docs.setdefault(n, {})[tag] = val
        pts = []
        for n in sorted(docs):
            d = docs[n]
            lat, lon = _num(d.get("GPSLatitude")), _num(d.get("GPSLongitude"))
            if lat is None or lon is None or abs(lat) > 90 or abs(lon) > 180 or (lat == 0 and lon == 0):
                continue  # no fix yet
            p = {"doc": n, "lat": lat, "lon": lon, "st": _num(d.get("SampleTime")),
                 "dt": _datetime_s(d.get("GPSDateTime"))}
            for src, dst in (("GPSSpeed", "speed_raw"), ("GPSAltitude", "alt")):  # speed unit depends on camera
                v = _num(d.get(src))
                if v is not None:
                    p[dst] = v
            pts.append(p)
        samples = [p for p in pts if p["doc"] > 0]
        if not samples:
            pts = pts[:1]  # only a file-level location: one point for the whole clip
            return [{"t": 0.0, "lat": pts[0]["lat"], "lon": pts[0]["lon"]}] if pts else []
        if all(p["st"] is not None for p in samples):
            for p in samples:
                p["t"] = p["st"]
        elif all(p["dt"] is not None for p in samples):
            t0 = min(p["dt"] for p in samples)
            for p in samples:
                p["t"] = p["dt"] - t0
        else:
            for i, p in enumerate(samples):
                p["t"] = float(i)
        samples.sort(key=lambda p: p["t"])
        out = []
        for p in samples:
            if out and p["t"] <= out[-1]["t"]:
                continue  # duplicate timestamp
            q = {"t": round(float(p["t"]), 3), "lat": p["lat"], "lon": p["lon"]}
            for k in ("speed_raw", "alt"):
                if k in p:
                    q[k] = p[k]
            if p["dt"] is not None:
                q["time"] = datetime.fromtimestamp(p["dt"], tz=timezone.utc).isoformat(timespec="seconds")
            out.append(q)
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("could not parse ExifTool output: %s", e)
        return []


def extract_embedded_track(video_path) -> list[dict]:
    """GPS track embedded in a video as [{t, lat, lon, ...}], or [] (no ExifTool / no GPS / any error)."""
    global _warned_missing
    try:
        p = Path(video_path)
        if not p.is_file():
            return []
        exe = exiftool_path()
        if not exe:
            if not _warned_missing:
                _warned_missing = True
                log.warning("ExifTool not found: GPS embedded in dashcam videos is not read. Install ExifTool "
                            "(https://exiftool.org, free) and put it on PATH to enable embedded-GPS dashcams.")
            return []
        cmd = [exe, "-ee", "-n", "-j", "-G3", "-charset", "filename=utf8", "-api", "LargeFileSupport=1",
               *("-" + t for t in TAGS), str(p)]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_S, creationflags=flags)
        text = r.stdout.decode("utf-8", errors="ignore").strip()
        if not text:
            return []
        track = parse_exiftool_json(text)
        if track:
            log.info("embedded GPS: %d points from %s", len(track), p.name)
        return track
    except subprocess.TimeoutExpired:
        log.warning("ExifTool timed out after %d s on %s", TIMEOUT_S, video_path)
        return []
    except Exception as e:  # noqa: BLE001
        log.warning("embedded GPS extraction failed: %s", e)
        return []
