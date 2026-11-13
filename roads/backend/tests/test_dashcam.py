"""Tests for dashcam support: NMEA tracks in geo.load_track and dashcam.extract_embedded_track.

Run: .venv\\Scripts\\python.exe backend/tests/test_dashcam.py   (pytest also works if installed)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="rs_dashcam_"))
os.environ["ROADSENSE_DATA_DIR"] = str(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app import dashcam, geo  # noqa: E402


def nmea(body: str) -> str:
    """'GPRMC,...' -> '$GPRMC,...*CS' with a correct checksum."""
    x = 0
    for ch in body:
        x ^= ord(ch)
    return f"${body}*{x:02X}"


def _write(name: str, text: str) -> Path:
    p = TMP / name
    p.write_text(text, encoding="utf-8")
    return p


# Bengaluru, heading north at ~36 km/h (19.44 kn), one fix per second
LINES = [
    nmea("GPRMC,101500.00,A,1258.7040,N,07738.4480,E,19.44,0.0,131126,,,A"),
    nmea("GPGGA,101500.00,1258.7040,N,07738.4480,E,1,09,0.9,905.0,M,-86.0,M,,"),   # same instant: merged
    nmea("GNRMC,101501.00,A,1258.7094,N,07738.4480,E,19.44,0.0,131126,,,A"),
    nmea("GPRMC,101502.00,V,1258.9999,N,07738.9999,E,0.0,0.0,131126,,,N"),         # status V: skipped
    "$GPRMC,101503.00,A,1258.9999,N,07738.9999,E,19.44,0.0,131126,,,A*00",         # bad checksum: skipped
    nmea("GPGGA,101503.50,1200.0000,N,07700.0000,E,0,00,99.9,,M,,M,,"),            # fix quality 0: skipped
    nmea("GNGGA,101504.00,1258.7256,N,07738.4480,E,1,10,0.8,906.0,M,-86.0,M,,"),   # GGA only: date carried
    "$GPRMC,101506.00,A,1258.7364,N,07738.4480,E,19.44,0.0,131126,,,A",            # no checksum: accepted
    "$GPGSV,3,1,11,03,03,111,00,04,15,270,00,06,01,010,00,13,06,292,00*74",        # other sentence: ignored
]


def test_nmea_known_sentences():
    """Classic reference sentences with published checksums."""
    p = _write("ref.nmea", "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A\r\n"
                           "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47\r\n"
                           + nmea("GPGGA,123520,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,") + "\r\n")
    tr = geo.load_track(p)
    assert len(tr) == 2, tr
    assert abs(tr[0]["lat"] - 48.1173) < 1e-4 and abs(tr[0]["lon"] - 11.516667) < 1e-4
    assert tr[0]["t"] == 0.0 and tr[1]["t"] == 1.0
    assert abs(tr[0]["speed_kmh"] - 41.48) < 0.01 and tr[0]["alt"] == 545.4
    assert tr[0]["time"].startswith("1994-03-23T12:35:19")


def test_nmea_skips_invalid_and_merges():
    tr = geo.load_track(_write("drive.log", "\n".join(LINES)))
    assert [p["t"] for p in tr] == [0.0, 1.0, 4.0, 6.0], tr
    assert abs(tr[0]["lat"] - (12 + 58.7040 / 60)) < 1e-9 and abs(tr[0]["lon"] - (77 + 38.4480 / 60)) < 1e-9
    assert tr[0]["speed_kmh"] == 36.0 and tr[0]["alt"] == 905.0   # RMC + GGA merged into one point
    assert all(p["lat"] < 12.98 for p in tr)                         # V / bad-checksum / quality-0 fixes are gone
    assert tr[2]["time"].startswith("2026-11-13T10:15:04")           # GGA inherits the RMC date


def test_nmea_hemispheres_prefix_and_midnight():
    text = "\n".join([
        "[23:59:59] " + nmea("GPGGA,235959.00,3352.1280,S,15112.5580,W,1,08,1.0,10.0,M,,M,,"),
        "[00:00:01] " + nmea("GPGGA,000001.00,3352.1290,S,15112.5590,W,2,08,1.0,10.0,M,,M,,"),
    ])
    tr = geo.load_track(_write("south.txt", text))
    assert len(tr) == 2 and tr[1]["t"] == 2.0, tr     # midnight rollover, line prefixes tolerated
    assert tr[0]["lat"] < 0 and tr[0]["lon"] < 0 and abs(tr[0]["lat"] + 33.8688) < 1e-4
    assert "time" not in tr[0]                           # no RMC -> no date -> no absolute time


def test_interpolate_on_nmea_track():
    tr = geo.load_track(_write("drive2.nmea", "\n".join(LINES)))
    lat, lon = geo.interpolate(tr, 0.5)
    assert abs(lat - (tr[0]["lat"] + tr[1]["lat"]) / 2) < 1e-9 and abs(lon - tr[0]["lon"]) < 1e-9
    lat2, _ = geo.interpolate(tr, 5.0)                   # between the fixes at t=4 and t=6
    assert abs(lat2 - (tr[2]["lat"] + tr[3]["lat"]) / 2) < 1e-9
    assert geo.interpolate(tr, -5) == (tr[0]["lat"], tr[0]["lon"])
    assert geo.interpolate(tr, 99) == (tr[-1]["lat"], tr[-1]["lon"])
    assert 8 < geo.haversine_m(tr[0]["lat"], tr[0]["lon"], tr[1]["lat"], tr[1]["lon"]) < 12  # ~10 m in 1 s
    assert len(geo.sample_track(tr, 20)) >= 2


def test_existing_formats_still_work():
    csv_t = geo.load_track(_write("t.csv", "t,lat,lon\n0,12.97,77.64\n2,12.98,77.65\n"))
    json_t = geo.load_track(_write("t.json", json.dumps([{"t": 0, "lat": 12.97, "lon": 77.64},
                                                          {"t": 2, "lat": 12.98, "lon": 77.65}])))
    gpx_t = geo.load_track(_write("t.gpx", '<gpx><trk><trkseg>'
                                  '<trkpt lat="12.97" lon="77.64"><time>2026-11-13T10:00:00Z</time></trkpt>'
                                  '<trkpt lat="12.98" lon="77.65"><time>2026-11-13T10:00:02Z</time></trkpt>'
                                  '</trkseg></trk></gpx>'))
    want = [{"t": 0.0, "lat": 12.97, "lon": 77.64}, {"t": 2.0, "lat": 12.98, "lon": 77.65}]
    assert csv_t == want and json_t == want and gpx_t == want
    assert geo.load_track(_write("junk.txt", "hello\nworld\n")) == []
    assert geo.load_track(TMP / "missing.nmea") == []


def test_extract_embedded_track_graceful():
    assert dashcam.extract_embedded_track(TMP / "nope.mp4") == []            # missing file
    assert dashcam.extract_embedded_track(_write("fake.mp4", "not a video")) == []  # not a video
    old = os.environ.get("PATH", "")
    old_env = os.environ.pop("ROADSENSE_EXIFTOOL", None)
    os.environ["PATH"] = str(TMP)                                             # force the no-ExifTool branch
    try:
        assert dashcam.exiftool_path() is None
        assert dashcam.extract_embedded_track(TMP / "fake.mp4") == []
    finally:
        os.environ["PATH"] = old
        if old_env:
            os.environ["ROADSENSE_EXIFTOOL"] = old_env


def test_parse_exiftool_json():
    """Shape of `exiftool -ee -n -j -G3` output (no ExifTool needed)."""
    sample = [{"SourceFile": "drive.mp4", "Main:Duration": 3.0,
               "Doc1:SampleTime": 0, "Doc1:GPSLatitude": 0, "Doc1:GPSLongitude": 0,          # no fix yet
               "Doc2:SampleTime": 1.0, "Doc2:GPSDateTime": "2026:11:13 10:15:01.000Z",
               "Doc2:GPSLatitude": 12.9784, "Doc2:GPSLongitude": 77.6408, "Doc2:GPSSpeed": 36.0,
               "Doc3:SampleTime": 2.0, "Doc3:GPSDateTime": "2026:11:13 10:15:02.000Z",
               "Doc3:GPSLatitude": 12.9785, "Doc3:GPSLongitude": 77.6408,
               "Doc10:SampleTime": 3.0, "Doc10:GPSLatitude": 12.9786, "Doc10:GPSLongitude": 77.6408}]
    tr = dashcam.parse_exiftool_json(json.dumps(sample))
    assert [p["t"] for p in tr] == [1.0, 2.0, 3.0], tr
    assert tr[0]["speed_raw"] == 36.0 and tr[0]["time"].startswith("2026-11-13T10:15:01")
    assert abs(geo.interpolate(tr, 1.5)[0] - 12.97845) < 1e-9
    no_st = [{"Doc1:GPSDateTime": "2026:11:13 10:15:00Z", "Doc1:GPSLatitude": 12.0, "Doc1:GPSLongitude": 77.0,
              "Doc2:GPSDateTime": "2026:11:13 10:15:05Z", "Doc2:GPSLatitude": 12.1, "Doc2:GPSLongitude": 77.1}]
    assert [p["t"] for p in dashcam.parse_exiftool_json(no_st)] == [0.0, 5.0]   # GPSDateTime fallback
    phone = [{"Main:GPSLatitude": 12.97, "Main:GPSLongitude": 77.64}]             # one clip-level location
    assert dashcam.parse_exiftool_json(phone) == [{"t": 0.0, "lat": 12.97, "lon": 77.64}]
    assert dashcam.parse_exiftool_json("not json") == [] and dashcam.parse_exiftool_json([]) == []


def test_pipeline_has_dashcam_hook():
    from backend.app import pipeline
    assert pipeline.dashcam is dashcam


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as e:  # noqa: BLE001
                fails += 1
                print("FAIL", name, repr(e))
    print(f"{'all passed' if not fails else str(fails) + ' failed'}")
    sys.exit(1 if fails else 0)
