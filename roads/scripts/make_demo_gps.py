"""Synthetic GPS track for a demo video (for POST /api/inspections `gps_track`).

Usage (from repo root):
    .venv\\Scripts\\python.exe scripts/make_demo_gps.py demo.mp4
    .venv\\Scripts\\python.exe scripts/make_demo_gps.py demo.mp4 --start 12.9784,77.6408 --end 12.9634,77.6411
    .venv\\Scripts\\python.exe scripts/make_demo_gps.py demo.mp4 --waypoints "12.97,77.64;12.96,77.64;12.96,77.65"
    .venv\\Scripts\\python.exe scripts/make_demo_gps.py --duration 60 -o track.json

Default route: 100 Feet Road, Indiranagar, Bengaluru (12.9784,77.6408 -> 12.9634,77.6411).
Output: JSON list [{t, lat, lon, speed, accuracy}] with t = seconds since video start (one point per --step s),
constant speed along the polyline: --speed-kmh (default 25, a realistic city ride; the route is cut short
if the video is shorter) or --fit to stretch the whole route over the video's duration.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

DEFAULT_START = (12.9784, 77.6408)
DEFAULT_END = (12.9634, 77.6411)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def video_duration(path: str) -> float:
    import cv2

    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
    cap.release()
    if fps <= 0 or n <= 0:
        raise SystemExit(f"could not read duration of {path}; pass --duration")
    return n / fps


def parse_pt(s: str) -> tuple[float, float]:
    lat, lon = (float(v) for v in s.replace(" ", "").split(","))
    return lat, lon


def make_track(points: list[tuple[float, float]], duration: float, step: float = 1.0,
               speed_kmh: float | None = 25.0) -> list[dict]:
    """Move along the polyline `points` at `speed_kmh` (None = cover the whole route in `duration` s)."""
    seg = [haversine_m(*points[i], *points[i + 1]) for i in range(len(points) - 1)]
    route = sum(seg) or 1e-9
    total = route if speed_kmh is None else min(route, speed_kmh / 3.6 * duration)
    speed = total / duration if duration > 0 else 0.0
    n = max(int(math.floor(duration / step)), 1)
    times = [round(i * step, 3) for i in range(n + 1)]
    if times[-1] < duration:
        times.append(round(duration, 3))
    out = []
    for t in times:
        d = min(total * t / duration, total) if duration > 0 else 0.0
        i, acc = 0, 0.0
        while i < len(seg) - 1 and acc + seg[i] < d:
            acc += seg[i]
            i += 1
        f = (d - acc) / seg[i] if seg[i] else 0.0
        (a_lat, a_lon), (b_lat, b_lon) = points[i], points[i + 1]
        out.append({"t": t, "lat": round(a_lat + (b_lat - a_lat) * f, 7), "lon": round(a_lon + (b_lon - a_lon) * f, 7),
                    "speed": round(speed, 2), "accuracy": 5.0})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", nargs="?", help="video file (duration read with OpenCV)")
    ap.add_argument("--duration", type=float, help="seconds (overrides the video)")
    ap.add_argument("--start", type=parse_pt, default=DEFAULT_START, help="lat,lon")
    ap.add_argument("--end", type=parse_pt, default=DEFAULT_END, help="lat,lon")
    ap.add_argument("--waypoints", help='"lat,lon;lat,lon;..." (overrides --start/--end)')
    ap.add_argument("--speed-kmh", type=float, default=25.0, help="vehicle speed (default 25)")
    ap.add_argument("--fit", action="store_true", help="stretch the whole route over the video duration")
    ap.add_argument("--step", type=float, default=1.0, help="seconds between points")
    ap.add_argument("-o", "--out", help="output JSON (default: <video>.track.json or track.json)")
    args = ap.parse_args()

    if args.duration is None and not args.video:
        ap.error("give a video or --duration")
    duration = args.duration if args.duration is not None else video_duration(args.video)
    pts = [parse_pt(p) for p in args.waypoints.split(";") if p.strip()] if args.waypoints else [args.start, args.end]
    if len(pts) < 2:
        ap.error("need at least two points")
    track = make_track(pts, duration, args.step, None if args.fit else args.speed_kmh)
    out = Path(args.out) if args.out else (Path(args.video).with_suffix(".track.json") if args.video else Path("track.json"))
    out.write_text(json.dumps(track, indent=1), encoding="utf-8")
    length = sum(haversine_m(track[i]["lat"], track[i]["lon"], track[i + 1]["lat"], track[i + 1]["lon"])
                 for i in range(len(track) - 1))
    print(f"{len(track)} points over {duration:.1f} s, covered {length:.0f} m (~{track[0]['speed'] * 3.6:.1f} km/h) -> {out}")


if __name__ == "__main__":
    main()
