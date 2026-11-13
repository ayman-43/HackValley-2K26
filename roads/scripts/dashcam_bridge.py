"""Dashcam -> Nagrik Roads live bridge.

Reads any dashcam video source and streams it to the Nagrik Roads server in real time, exactly like the
phone's Live scan: about 3 frames a second plus the current GPS position go to /api/live, the server
detects and tracks each defect, and it appears on the dashboard map within seconds.

Video sources (anything OpenCV can open):
  --source rtsp://192.168.1.254:554/live        Wi-Fi dashcam stream (RTSP)
  --source http://192.168.1.254:8080/video      MJPEG / HTTP stream (many dashcams, IP Webcam app)
  --source 0                                    USB dashcam / webcam (device index)
  --source ride.mp4                             recorded dashcam file, replayed at real speed

GPS (pick one; without GPS, defects are detected but not pinned on the map):
  --track ride.gpx                  GPX / NMEA / CSV / JSON track, matched to the video by time
                                    (dashcam SD cards usually store an NMEA or GPX file next to the video)
  --nmea-port COM5                  live USB GPS receiver (NMEA 0183, needs `pip install pyserial`)
  --lat 12.9716 --lon 77.5946       fixed position (parked camera, testing)

Examples:
  python scripts/dashcam_bridge.py --source rtsp://192.168.1.254/live --nmea-port COM5
  python scripts/dashcam_bridge.py --source backend/data/uploads/1/input.mp4 \
         --track backend/data/uploads/1/gps_track.json
  python scripts/dashcam_bridge.py --source 0 --lat 12.9716 --lon 77.5946 --server http://192.168.1.20:8000

Stop with Ctrl+C: the session is closed properly and a summary is printed.
"""
import argparse
import sys
import threading
import time
from pathlib import Path

import cv2
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app import geo  # noqa: E402  (GPX / NMEA / CSV / JSON track loading + interpolation)


# ---------------------------------------------------------------- video
class LatestFrame:
    """Reads a live stream on a background thread and keeps only the newest frame.

    Live streams buffer frames; if we read slower than the camera, we would fall further and further
    behind. Keeping only the latest frame keeps the map position and the picture in sync."""

    def __init__(self, cap):
        self.cap, self.frame, self.ok, self.lock = cap, None, True, threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while self.ok:
            ok, f = self.cap.read()
            if not ok:
                self.ok = False
                break
            with self.lock:
                self.frame = f

    def get(self):
        with self.lock:
            f, self.frame = self.frame, None
        return f


def open_source(src: str):
    cap = cv2.VideoCapture(int(src) if src.isdigit() else src)
    if not cap.isOpened():
        sys.exit(f"Could not open video source: {src}")
    is_file = Path(src).is_file()
    return cap, is_file


# ---------------------------------------------------------------- GPS
class Gps:
    def __init__(self, args):
        self.track, self.fixed, self.live = None, None, None
        if args.track:
            self.track = geo.load_track(args.track)
            if not self.track:
                sys.exit(f"Could not read a GPS track from {args.track}")
            print(f"GPS: track with {len(self.track)} points, {self.track[-1]['t']:.0f} s long")
        elif args.nmea_port:
            self._start_nmea(args.nmea_port, args.baud)
        elif args.lat is not None and args.lon is not None:
            self.fixed = (args.lat, args.lon)
            print(f"GPS: fixed position {args.lat}, {args.lon}")
        else:
            print("GPS: none. Defects will be detected but not pinned on the map.")

    def _start_nmea(self, port, baud):
        try:
            import serial  # pyserial
        except ImportError:
            sys.exit("--nmea-port needs pyserial:  .venv\\Scripts\\pip install pyserial")
        ser = serial.Serial(port, baud, timeout=1)
        self.live = None
        print(f"GPS: reading NMEA from {port} at {baud} baud")

        def run():
            while True:
                line = ser.readline().decode("ascii", "ignore").strip()
                pts = geo._parse_nmea(line) if line.startswith("$") else []
                if pts:
                    self.live = (pts[-1]["lat"], pts[-1]["lon"])
        threading.Thread(target=run, daemon=True).start()

    def at(self, t: float):
        """Position at t seconds since the start of the video / ride."""
        if self.track:
            return geo.interpolate(self.track, t)
        return self.live or self.fixed


# ---------------------------------------------------------------- main loop
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True, help="RTSP/HTTP URL, USB device index or video file")
    ap.add_argument("--server", default="http://localhost:8000", help="Nagrik Roads server URL")
    ap.add_argument("--fps", type=float, default=3.0, help="frames per second sent to the server (default 3)")
    ap.add_argument("--width", type=int, default=960, help="resize frames to this width before sending")
    ap.add_argument("--track", help="GPS track file (GPX, NMEA, CSV or JSON)")
    ap.add_argument("--nmea-port", help="serial port of a USB GPS receiver, e.g. COM5 or /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--max-seconds", type=float, help="stop after this many seconds")
    args = ap.parse_args()

    server = args.server.rstrip("/")
    try:
        requests.get(server + "/api/health", timeout=5).raise_for_status()
    except Exception as e:
        sys.exit(f"Nagrik Roads server not reachable at {server}: {e}")

    cap, is_file = open_source(args.source)
    gps = Gps(args)
    reader = None if is_file else LatestFrame(cap)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    start_pos = gps.at(0)
    form = {"lat": start_pos[0], "lon": start_pos[1]} if start_pos else {}
    sid = requests.post(server + "/api/live/start", data=form, timeout=60).json()["session_id"]
    print(f"Live session {sid} started. Open {server}/dashboard#/map to watch. Ctrl+C to stop.\n")

    http = requests.Session()
    period = 1.0 / max(0.2, args.fps)
    t0 = time.time()
    next_send = t0
    sent = 0
    try:
        while True:
            now = time.time()
            if args.max_seconds and now - t0 > args.max_seconds:
                break
            if is_file:
                # replay a recorded file at real speed: skip ahead to the frame for "now"
                target = int((now - t0) * src_fps)
                pos = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
                if target > pos + 1:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, target)
                ok, frame = cap.read()
                if not ok:
                    print("End of video.")
                    break
                t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            else:
                if not reader.ok and reader.frame is None:
                    print("Stream ended or disconnected.")
                    break
                frame = reader.get()
                if frame is None:
                    time.sleep(0.01)
                    continue
                t = now - t0

            if args.width and frame.shape[1] > args.width:
                h = int(frame.shape[0] * args.width / frame.shape[1])
                frame = cv2.resize(frame, (args.width, h), interpolation=cv2.INTER_AREA)
            ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ok:
                continue
            pos = gps.at(t)
            data = {"lat": pos[0], "lon": pos[1], "accuracy": 5.0} if pos else {}
            t_req = time.time()
            try:
                r = http.post(f"{server}/api/live/{sid}/frame", files={"frame": ("f.jpg", jpg.tobytes(), "image/jpeg")},
                              data=data, timeout=30)
                r.raise_for_status()
                res = r.json()
            except Exception as e:
                print(f"  frame failed: {e}")
                time.sleep(1)
                continue
            sent += 1
            st = res.get("stats", {})
            where = f"{pos[0]:.5f},{pos[1]:.5f}" if pos else "no GPS"
            line = (f"t={t:6.1f}s  {where}  boxes={len(res.get('detections', [])):2d}  "
                    f"unique={st.get('unique_defects', 0)}  reply={1000 * (time.time() - t_req):.0f} ms")
            for s in res.get("saved", []):
                line += f"\n  + saved {s.get('code') or s.get('defect_id')} ({'new' if s.get('new') else 'seen before'})"
            print(line)

            # keep a steady rate; if the server is slower than --fps, just send as fast as it answers
            next_send = max(next_send + period, time.time())
            time.sleep(max(0.0, next_send - time.time()))
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        if reader:
            reader.ok = False
        cap.release()
        try:
            out = requests.post(f"{server}/api/live/{sid}/stop", timeout=60).json()
            print(f"\nSession {sid}: {sent} frames sent, {out.get('raw_detections')} raw detections -> "
                  f"{out.get('unique_defects')} unique defects ({out.get('new_defects')} new).")
        except Exception as e:
            print(f"Could not close the session cleanly: {e}")


if __name__ == "__main__":
    main()
