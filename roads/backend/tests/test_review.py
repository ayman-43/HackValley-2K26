"""Tests for admin review, workflow, corrections export and reports (Agent 4)."""
import io
import json
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import pytest
except ImportError:  # pytest not installed: run this file directly (see __main__ below)
    pytest = None

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.app import db, reports, review  # noqa: E402

JPEG = bytes.fromhex(  # tiny valid-enough image bytes; content irrelevant to the zip test
    "ffd8ffe000104a46494600010100000100010000ffd9")


def iso(days_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).replace(microsecond=0).isoformat()


def _make_env(tmp_path, setattr_):
    media = tmp_path / "media"
    (media / "frames").mkdir(parents=True)
    setattr_(db, "DB_PATH", tmp_path / "test.db")
    setattr_(db, "MEDIA_DIR", media)
    db.init_db()
    app = FastAPI()
    app.include_router(review.router)
    app.include_router(reports.router)
    return TestClient(app), media


if pytest is not None:
    @pytest.fixture()
    def env(tmp_path, monkeypatch):
        return _make_env(tmp_path, monkeypatch.setattr)


def add_defect(**kw) -> int:
    d = dict(code="PH-0001", damage_class="pothole", lat=12.9719, lon=77.6412, first_seen=iso(1),
             last_seen=iso(0), severity_score=70, severity_label="High", risk_score=80, risk_label="Critical",
             trend="new", report_count=1, workflow="reported", ward_name="Indiranagar")
    d.update(kw)
    for k in ("risk_factors", "forecast", "nagrik"):
        if k in d and not isinstance(d[k], str):
            d[k] = json.dumps(d[k])
    cols = ",".join(d)
    with db.connect() as c:
        return c.execute(f"INSERT INTO defects ({cols}) VALUES ({','.join('?' * len(d))})", list(d.values())).lastrowid


def add_obs(defect_id, **kw) -> int:
    o = dict(defect_id=defect_id, observed_at=iso(0), damage_class="pothole", confidence=0.5,
             bbox=json.dumps([100, 50, 300, 250]), frame_w=640, frame_h=480, severity_score=60, severity_label="High")
    o.update(kw)
    cols = ",".join(o)
    with db.connect() as c:
        return c.execute(f"INSERT INTO observations ({cols}) VALUES ({','.join('?' * len(o))})", list(o.values())).lastrowid


def get(table, i):
    with db.connect() as c:
        return db.row(c.execute(f"SELECT * FROM {table} WHERE id=?", (i,)).fetchone())


def test_queue_sorted_with_urls(env):
    client, media = env
    d = add_defect()
    (media / "frames" / "a.jpg").write_bytes(JPEG)
    add_obs(d, confidence=0.5)
    add_obs(d, confidence=0.3, frame_path=str(media / "frames" / "a.jpg"), crop_path="frames/a.jpg")
    add_obs(d, confidence=0.9)  # >= AUTO_SURE_CONF: auto-approved, never reaches the queue
    q = client.get("/api/review/queue?limit=10").json()
    assert [round(x["confidence"], 1) for x in q] == [0.3, 0.5]  # lowest confidence first
    assert q[0]["frame_url"] == "/media/frames/a.jpg"
    assert q[0]["crop_url"] == "/media/frames/a.jpg"
    assert q[0]["defect_code"] == "PH-0001"


def test_confirm_reject_correct(env):
    client, _ = env
    d = add_defect()
    o1 = add_obs(d, observed_at=iso(2))
    o2 = add_obs(d, observed_at=iso(0))
    r = client.post(f"/api/observations/{o1}/review", json={"action": "confirm", "reviewer": "asha"})
    assert r.status_code == 200 and r.json()["review_status"] == "confirmed"
    assert get("observations", o1)["reviewer"] == "asha" and get("observations", o1)["reviewed_at"]

    r = client.post(f"/api/observations/{o2}/review",
                    json={"action": "correct", "corrected_class": "alligator_crack", "corrected_bbox": [1, 2, 30, 40]})
    assert r.status_code == 200
    o = get("observations", o2)
    assert o["review_status"] == "corrected" and o["corrected_bbox"] == [1, 2, 30, 40]
    assert get("defects", d)["damage_class"] == "alligator_crack"  # o2 is the latest

    # correcting an older observation does not change the defect class
    client.post(f"/api/observations/{o1}/review", json={"action": "correct", "corrected_class": "pothole"})
    assert get("defects", d)["damage_class"] == "alligator_crack"

    # reject one of two -> defect not rejected; reject the other -> rejected
    client.post(f"/api/observations/{o1}/review", json={"action": "reject"})
    assert get("defects", d)["workflow"] == "reported"
    client.post(f"/api/observations/{o2}/review", json={"action": "reject"})
    assert get("defects", d)["workflow"] == "rejected"

    assert client.post(f"/api/observations/{o1}/review", json={"action": "nope"}).status_code == 400
    assert client.post(f"/api/observations/{o1}/review",
                       json={"action": "correct", "corrected_class": "tree"}).status_code == 400
    assert client.post("/api/observations/9999/review", json={"action": "confirm"}).status_code == 404


def test_auto_approve(env):
    d_many = add_defect(code="PH-0001", report_count=3)
    d_few = add_defect(code="PH-0002", report_count=1)
    a = add_obs(d_many, confidence=0.7)
    b = add_obs(d_many, confidence=0.4)
    c_ = add_obs(d_few, confidence=0.9)   # very confident on its own
    e = add_obs(d_few, confidence=0.7)    # fairly confident, but the defect has only 1 report
    with db.connect() as conn:
        assert review.auto_approve(conn) == 2
        assert review.auto_approve(conn) == 0  # idempotent
    assert get("observations", a)["review_status"] == "confirmed"
    assert get("observations", a)["reviewer"] == "auto"
    assert get("observations", b)["review_status"] == "pending"
    assert get("observations", c_)["review_status"] == "confirmed"
    assert get("observations", e)["review_status"] == "pending"


def test_workflow_and_repair(env):
    client, media = env
    d = add_defect()
    url = f"/api/defects/{d}/workflow"
    assert client.patch(url, json={"workflow": "scheduled"}).status_code == 409  # skip
    assert client.patch(url, json={"workflow": "bogus"}).status_code == 400
    assert client.patch(url, json={"workflow": "verified"}).json()["workflow"] == "verified"
    assert client.patch(url, json={"workflow": "scheduled", "note": "Q4"}).json()["note"] == "Q4"
    r = client.post(f"/api/defects/{d}/repair", files={"file": ("fixed.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["workflow"] == "repaired" and out["repaired_at"]
    assert out["repair_photo_url"].startswith("/media/repairs/")
    assert list((media / "repairs").glob("*.jpg"))
    assert client.patch(url, json={"workflow": "closed"}).json()["workflow"] == "closed"
    assert client.patch(url, json={"workflow": "rejected"}).json()["workflow"] == "rejected"
    assert client.patch("/api/defects/999/workflow", json={"workflow": "verified"}).status_code == 404


def test_export_corrections(env):
    client, media = env
    d = add_defect()
    for n in ("f1", "f2", "f3", "f4"):
        (media / "frames" / f"{n}.jpg").write_bytes(JPEG)
    o1 = add_obs(d, frame_path=str(media / "frames" / "f1.jpg"))
    o2 = add_obs(d, frame_path="frames/f2.jpg")
    o3 = add_obs(d, frame_path="frames/f3.jpg")
    add_obs(d, frame_path="frames/f4.jpg")  # pending -> excluded
    client.post(f"/api/observations/{o1}/review", json={"action": "confirm"})
    client.post(f"/api/observations/{o2}/review",
                json={"action": "correct", "corrected_class": "longitudinal_crack", "corrected_bbox": [0, 0, 320, 240]})
    client.post(f"/api/observations/{o3}/review", json={"action": "reject"})
    r = client.get("/api/export/corrections")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert "data.yaml" in names and "pothole" in z.read("data.yaml").decode()
    imgs = [n for n in names if n.startswith("images/")]
    labels = {n: z.read(n).decode() for n in names if n.startswith("labels/")}
    assert len(imgs) == 3 and len(labels) == 3
    vals = sorted(v.strip() for v in labels.values())
    assert vals[0] == ""  # rejected -> negative
    assert "0 0.250000 0.250000 0.500000 0.500000" in vals  # corrected bbox/class
    assert "3 0.312500 0.312500 0.312500 0.416667" in vals  # confirmed original


def test_complaint_full_and_minimal(env):
    client, media = env
    (media / "frames" / "c.jpg").write_bytes(JPEG)
    d = add_defect(
        risk_factors=[{"factor": "Near school", "detail": "St. Joseph's 80 m", "points": 15}],
        forecast={"text": "Likely to reach High in 12 days", "days_to_high": 12},
        road_name="100 Feet Road", report_count=4,
        nagrik={"ward_name": "Indiranagar", "corporation": "BBMP",
                "officials": [{"department": "Roads", "name": "A. Kumar", "phone": "080-1234"}],
                "works": [{"title": "Pothole filling Ward 80", "tender_ref": "BBMP/2025/77", "tender_date": "2025-06-01"}]})
    o = add_obs(d, crop_path="frames/c.jpg")
    html = client.get(f"/api/defects/{d}/complaint").text
    for s in ("PH-0001", "Pothole", "Near school", "Likely to reach High", "openstreetmap.org", "A. Kumar",
              "BBMP", "A road/pothole works tender exists for this ward", "BBMP/2025/77",
              "/media/frames/c.jpg", "pending verification", "@media print"):
        assert s in html, s
    client.post(f"/api/observations/{o}/review", json={"action": "confirm", "reviewer": "asha"})
    assert "verified by asha on" in client.get(f"/api/defects/{d}/complaint").text

    bare = add_defect(code="PH-0002", lat=None, lon=None, risk_score=None, risk_label=None, ward_name=None)
    r = client.get(f"/api/defects/{bare}/complaint")
    assert r.status_code == 200 and "PH-0002" in r.text and "No photos recorded" in r.text
    assert client.get("/api/defects/999/complaint").status_code == 404


def test_digest_and_csv(env):
    client, _ = env
    add_defect(code="PH-0001", risk_label="Critical", risk_score=90, trend="worsening", first_seen=iso(0.2))
    add_defect(code="PH-0002", risk_label="High", risk_score=65, first_seen=iso(30))
    add_defect(code="PH-0003", risk_label="Low", risk_score=10, ward_name="Koramangala", first_seen=iso(3))
    add_defect(code="PH-0004", workflow="rejected", risk_score=99, first_seen=iso(40))
    r = client.get("/api/digest?period=daily")
    assert r.status_code == 200
    t = r.text
    for s in ("Indiranagar", "Koramangala", "Overdue", "Worsening", "PH-0001", "/api/defects/1/complaint"):
        assert s in t, s
    assert "PH-0004" not in t  # rejected not in top list
    w = client.get("/api/digest?ward=Koramangala&period=weekly").text
    assert "Koramangala" in w and "Indiranagar" not in w.split("</h1>", 1)[1]
    assert client.get("/api/digest?ward=Nowhere").status_code == 200

    c = client.get("/api/report/summary.csv")
    assert c.status_code == 200 and c.headers["content-type"].startswith("text/csv")
    lines = c.text.strip().splitlines()
    assert lines[0].startswith("id,code,damage_class") and len(lines) == 5
    assert "PH-0004" in lines[1]  # sorted by risk


if __name__ == "__main__":  # fallback runner when pytest is unavailable
    import tempfile
    import traceback

    failed = 0
    tests = [(n, f) for n, f in list(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        saved = (db.DB_PATH, db.MEDIA_DIR)
        with tempfile.TemporaryDirectory() as td:
            try:
                fn(_make_env(Path(td), setattr))
                print("PASS", name)
            except Exception:  # noqa: BLE001
                failed += 1
                print("FAIL", name)
                traceback.print_exc()
            finally:
                db.DB_PATH, db.MEDIA_DIR = saved
    print(f"{len(tests) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
