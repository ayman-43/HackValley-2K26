"""End-to-end smoke test of the core pipeline + API (CPU only, small inputs).

Run: .venv\\Scripts\\python.exe backend/tests/test_pipeline.py   (pytest also works if installed)
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("ROADSENSE_DEVICE", "cpu")
os.environ.setdefault("ROADSENSE_DEPTH", "0")
# isolated database + media + uploads (must be set before backend.app.db is imported)
os.environ.setdefault("ROADSENSE_DATA_DIR", tempfile.mkdtemp(prefix="roadsense_test_"))

import cv2  # noqa: E402

try:
    import pytest
except ImportError:  # run directly via __main__
    pytest = None

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SAMPLE = ROOT / "reference" / "pothole-pilot" / "ai_model" / "sample_video.mp4"
LAT, LON = 12.9719, 77.6412  # Indiranagar 100 Ft Rd (demo point)


def _client_gen(tmp: Path):
    from backend.app import db
    old = db.DB_PATH
    db.DB_PATH = tmp / "test.db"  # fresh DB even if db was imported earlier (media: ROADSENSE_DATA_DIR)
    db.init_db()
    from fastapi.testclient import TestClient
    from backend.app.main import app
    with TestClient(app) as c:
        yield c
    db.DB_PATH = old


def _make_media(tmp: Path) -> dict:
    assert SAMPLE.exists(), "sample video missing"
    # read sequentially (seeking with cap.set() can fail under memory pressure)
    cap = cv2.VideoCapture(str(SAMPLE))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    frames = []
    for i in range(100 + int(fps * 3)):
        ok, f = cap.read()
        if not ok:
            break
        if i >= 100:
            frames.append(f)
    cap.release()
    assert frames, "could not read sample video"
    img = tmp / "frame.jpg"
    cv2.imwrite(str(img), frames[0])
    h, w = frames[0].shape[:2]
    clip = tmp / "clip.mp4"  # 3-second clip
    wr = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for f in frames:
        wr.write(f)
    wr.release()
    track = tmp / "track.json"
    track.write_text(json.dumps([{"t": 0, "lat": LAT, "lon": LON}, {"t": 3, "lat": LAT + 0.0003, "lon": LON}]))
    return {"image": img, "clip": clip, "track": track}


if pytest is not None:
    @pytest.fixture(scope="module")
    def client(tmp_path_factory):
        yield from _client_gen(tmp_path_factory.mktemp("rs"))

    @pytest.fixture(scope="module")
    def media(tmp_path_factory):
        return _make_media(tmp_path_factory.mktemp("media"))


def _approx(a, b, eps=1e-9):
    return all(abs(x - y) < eps for x, y in zip(a, b))


def _wait(client, job_id, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "error"):
            return j
        time.sleep(0.5)
    raise TimeoutError(job_id)


def test_geo():
    from backend.app import geo
    d = geo.haversine_m(LAT, LON, LAT + 0.001, LON)
    assert 100 < d < 120
    tr = [{"t": 0, "lat": 0.0, "lon": 0.0}, {"t": 10, "lat": 1.0, "lon": 2.0}]
    assert _approx(geo.interpolate(tr, 5), (0.5, 1.0))
    assert geo.interpolate(tr, 99) == (1.0, 2.0)
    assert geo.interpolate([], 1) is None


def test_load_track_formats(tmp_path):
    from backend.app import geo
    (tmp_path / "a.csv").write_text("t,lat,lon\n0,12.97,77.64\n2,12.98,77.65\n")
    (tmp_path / "b.json").write_text(json.dumps([{"t": 1700000000000, "lat": 1, "lon": 2},
                                                 {"t": 1700000002000, "lat": 3, "lon": 4}]))
    (tmp_path / "c.gpx").write_text(
        '<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><trkseg>'
        '<trkpt lat="12.9" lon="77.6"><time>2026-01-01T00:00:00Z</time></trkpt>'
        '<trkpt lat="12.91" lon="77.61"><time>2026-01-01T00:00:04Z</time></trkpt>'
        '</trkseg></trk></gpx>')
    assert geo.load_track(tmp_path / "a.csv")[1] == {"t": 2.0, "lat": 12.98, "lon": 77.65}
    assert [p["t"] for p in geo.load_track(tmp_path / "b.json")] == [0.0, 2.0]
    assert [p["t"] for p in geo.load_track(tmp_path / "c.gpx")] == [0.0, 4.0]


def test_image_and_video_end_to_end(client, media):
    # image with form location
    with open(media["image"], "rb") as f:
        r = client.post("/api/inspections", files={"file": ("frame.jpg", f, "image/jpeg")},
                        data={"lat": str(LAT), "lon": str(LON), "road_type": "primary"})
    assert r.status_code == 200, r.text
    j = _wait(client, r.json()["job_id"])
    assert j["status"] == "done", j.get("error")
    res = j["result"]
    assert res["raw_detections"] >= 1 and res["unique_defects"] >= 1
    first_unique = res["unique_defects"]

    insp = client.get(f"/api/inspections/{res['inspection_id']}").json()
    assert insp["media_url"].startswith("/media/")
    assert client.get(insp["media_url"]).status_code == 200
    assert insp["observations"] and insp["observations"][0]["crop_url"]
    assert client.get(insp["observations"][0]["crop_url"]).status_code == 200

    # same photo again -> should be recognised as duplicates (no new defects), report_count grows
    with open(media["image"], "rb") as f:
        r2 = client.post("/api/inspections", files={"file": ("frame.jpg", f, "image/jpeg")},
                         data={"lat": str(LAT), "lon": str(LON)})
    j2 = _wait(client, r2.json()["job_id"])
    assert j2["status"] == "done", j2.get("error")
    assert j2["result"]["new_defects"] <= first_unique // 2  # most re-sightings matched

    # 3-second video with GPS track
    with open(media["clip"], "rb") as f, open(media["track"], "rb") as g:
        r3 = client.post("/api/inspections", files={"file": ("clip.mp4", f, "video/mp4"),
                                                    "gps_track": ("track.json", g, "application/json")})
    j3 = _wait(client, r3.json()["job_id"])
    assert j3["status"] == "done", j3.get("error")
    v = j3["result"]
    assert v["frames_processed"] > 10
    assert v["raw_detections"] >= v["unique_defects"]
    assert v["gps_points"] == 2

    defects = client.get("/api/defects").json()
    assert len(defects) >= 1
    risks = [d["risk_score"] for d in defects]
    assert all(x is not None for x in risks) and risks == sorted(risks, reverse=True)
    assert all(d["code"][:3] in ("PH-", "CR-") for d in defects)
    assert any(d["report_count"] >= 2 for d in defects)

    det = client.get(f"/api/defects/{defects[0]['id']}").json()
    assert det["observations"]

    s = client.get("/api/stats").json()
    assert s["inspections"] == 3 and s["total_defects"] == len(defects)
    assert s["raw_detections"] >= s["unique_defects"]
    assert client.get("/api/metrics").status_code == 200
    assert client.get("/api/defects?min_risk=101").json() == []


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        test_geo()
        (d / "lt").mkdir()
        test_load_track_formats(d / "lt")
        gen = _client_gen(d)
        c = next(gen)
        try:
            test_image_and_video_end_to_end(c, _make_media(d))
        finally:
            gen.close()
    print("all pipeline tests passed")
