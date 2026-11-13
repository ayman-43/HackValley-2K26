"""Tests for crew.py (repair route) and privacy.py (face/plate blurring).

Run: .venv\\Scripts\\python.exe backend/tests/test_crew_privacy.py   (pytest also works if installed)
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["ROADSENSE_DATA_DIR"] = tempfile.mkdtemp(prefix="rs_crew_")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from backend.app import crew, db, privacy  # noqa: E402

db.init_db()
crew.ensure_columns()
crew.OSRM_URL = "http://127.0.0.1:9/offline/"  # force the offline fallback: tests must not need internet

POINTS = [  # (lat, lon, risk) around Indiranagar
    (12.9784, 77.6408, 80), (12.9720, 77.6410, 60), (12.9650, 77.6380, 40), (12.9900, 77.6500, 90)]


def _seed():
    with db.connect() as c:
        c.execute("DELETE FROM defects")
        for i, (la, lo, r) in enumerate(POINTS):
            c.execute("INSERT INTO defects (code, damage_class, lat, lon, risk_score, risk_label, workflow, status) "
                      "VALUES (?,?,?,?,?,?,?,?)", (f"T-{i}", "pothole", la, lo, r, "High", "reported", "open"))
        c.execute("INSERT INTO defects (code, damage_class, lat, lon, risk_score, workflow, status) "
                  "VALUES ('T-done','pothole',12.97,77.64,99,'repaired','repaired')")
        return [r[0] for r in c.execute("SELECT id FROM defects WHERE code LIKE 'T-_' ORDER BY code")]


def test_top_n_by_risk_offline():
    _seed()
    r = crew.plan_route({"top_n": 3})
    assert r["engine"] == "offline-estimate"
    ids = {s["id"] for s in r["stops"]}
    assert len(ids) == 3
    with db.connect() as c:
        risks = [c.execute("SELECT risk_score FROM defects WHERE id=?", (i,)).fetchone()[0] for i in ids]
    assert sorted(risks) == [60, 80, 90]  # highest risk, repaired defect excluded
    assert r["roundtrip"] and r["distance_km"] > 0 and r["total_min"] > r["drive_min"]
    assert [s["seq"] for s in r["stops"]] == [1, 2, 3]
    assert r["path"][0] == r["path"][-1]  # back to depot


def test_explicit_ids_and_verified_only():
    ids = _seed()
    r = crew.plan_route({"defect_ids": ids[:2]})
    assert sorted(s["id"] for s in r["stops"]) == sorted(ids[:2])
    try:
        crew.plan_route({"include_unverified": False})
        raise AssertionError("expected 404: nothing verified yet")
    except Exception as e:
        assert getattr(e, "status_code", None) == 404


def test_repaired_by():
    ids = _seed()
    out = crew.set_repaired_by(ids[0], {"crew_id": "BBMP-CREW-07"})
    assert out["repaired_by"] == "BBMP-CREW-07"


def test_privacy_blurs_people_and_keeps_size():
    import ultralytics
    img = cv2.imread(os.path.join(os.path.dirname(ultralytics.__file__), "assets", "bus.jpg"))
    regs = privacy.regions(img)
    kinds = [r[4] for r in regs]
    assert kinds.count("face") >= 3 and "plate" in kinds
    out = privacy.redact(img)
    assert out.shape == img.shape
    x1, y1, x2, y2, _ = next(r for r in regs if r[4] == "face")
    assert np.abs(out[y1:y2, x1:x2].astype(int) - img[y1:y2, x1:x2].astype(int)).mean() > 5  # changed
    blank = np.full((480, 640, 3), 120, np.uint8)
    assert privacy.redact(blank) is blank  # nothing sensitive -> untouched


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
