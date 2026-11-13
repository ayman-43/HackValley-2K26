"""Tests for backend/app/dedup.py (duplicate detection + progress monitoring).

Uses real pothole crops from the Pothole Pilot sample video and a throw-away SQLite DB
(never the real backend/data/roadsense.db). Written as unittest.TestCase so it runs under
pytest or plain `python -m unittest backend.tests.test_dedup`.
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.app import db, dedup  # noqa: E402

VIDEO = ROOT / "reference" / "pothole-pilot" / "ai_model" / "sample_video.mp4"
# hand-picked pothole regions in frame 30 (1280x720): (x1, y1, x2, y2)
BOXES = {
    "big": (20, 330, 480, 440),
    "right": (800, 205, 1040, 265),
    "leftsmall": (140, 265, 290, 320),
    "bottom": (100, 580, 380, 720),
    "mid": (320, 340, 600, 420),
}
LAT0, LON0 = 12.9719, 77.6412      # Indiranagar 100 Ft Rd
M_PER_DEG_LAT = 111_320.0

FRAME = None


def setUpModule():
    global FRAME
    if not VIDEO.exists():
        raise unittest.SkipTest("sample video missing")
    cap = cv2.VideoCapture(str(VIDEO))
    for _ in range(31):
        ok, f = cap.read()
        if not ok:
            raise unittest.SkipTest("could not read sample video")
    cap.release()
    FRAME = f


def crop(name, dx=0, dy=0, pad=0):
    x1, y1, x2, y2 = BOXES[name]
    return FRAME[y1 + dy - pad:y2 + dy + pad, x1 + dx - pad:x2 + dx + pad].copy()


def north(m):
    return LAT0 + m / M_PER_DEG_LAT


def ts(days):
    return (datetime(2026, 11, 1, 9, 0) + timedelta(days=days)).isoformat(timespec="seconds")


class DbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(os.path.join(self.tmp.name, "t.db"))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(db.SCHEMA)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def add_inspection(self, day, lat=LAT0, lon=LON0):
        cur = self.conn.execute("INSERT INTO inspections (created_at, source_type, lat, lon) VALUES (?,?,?,?)",
                                (ts(day), "image", lat, lon))
        return cur.lastrowid

    def add_defect(self, cls="pothole", lat=LAT0, lon=LON0, img=None, **kw):
        cur = self.conn.execute("INSERT INTO defects (damage_class, lat, lon, first_seen) VALUES (?,?,?,?)",
                                (cls, lat, lon, ts(0)))
        did = cur.lastrowid
        self.conn.execute("UPDATE defects SET code=? WHERE id=?", (f"PH-{did:04d}", did))
        for k, v in kw.items():
            self.conn.execute(f"UPDATE defects SET {k}=? WHERE id=?", (v, did))
        if img is not None:
            dedup.save_fingerprint(self.conn, did, dedup.fingerprint(img))
        return did

    def add_obs(self, did, insp, day, sev=40.0, area=0.02, lat=LAT0, lon=LON0):
        self.conn.execute(
            "INSERT INTO observations (defect_id, inspection_id, observed_at, damage_class, severity_score, "
            "area_ratio, lat, lon) VALUES (?,?,?,?,?,?,?,?)", (did, insp, ts(day), "pothole", sev, area, lat, lon))

    def defect(self, did):
        return dict(self.conn.execute("SELECT * FROM defects WHERE id=?", (did,)).fetchone())


class TestFingerprint(unittest.TestCase):
    def test_json_safe_and_empty(self):
        fp = dedup.fingerprint(crop("big"))
        self.assertEqual(json.loads(json.dumps(fp)), fp)
        self.assertEqual(len(fp["phash"]), 16)
        self.assertEqual(len(fp["hist"]), 128)
        self.assertEqual(dedup.fingerprint(FRAME[0:0, 0:0]), {})
        self.assertEqual(dedup.similarity({}, fp), 0.0)

    def test_identity(self):
        fp = dedup.fingerprint(crop("big"))
        self.assertAlmostEqual(dedup.similarity(fp, fp), 1.0, places=3)

    def test_robust_to_photometric_and_scale(self):
        b = crop("big")
        fb = dedup.fingerprint(b)
        variants = {
            "scale": cv2.resize(b, None, fx=0.6, fy=0.6),
            "bright": cv2.convertScaleAbs(b, alpha=1.0, beta=35),
            "dark": cv2.convertScaleAbs(b, alpha=0.75, beta=0),
            "jpeg": cv2.imdecode(cv2.imencode(".jpg", b, [cv2.IMWRITE_JPEG_QUALITY, 40])[1], 1),
        }
        for name, v in variants.items():
            self.assertGreaterEqual(dedup.similarity(fb, dedup.fingerprint(v)), 0.92, name)

    def test_robust_to_shift_and_padding(self):
        fb = dedup.fingerprint(crop("big"))
        self.assertGreaterEqual(dedup.similarity(fb, dedup.fingerprint(crop("big", dx=15, dy=8))), dedup.SIM_FAR)
        self.assertGreaterEqual(dedup.similarity(fb, dedup.fingerprint(crop("big", pad=12))), dedup.SIM_FAR)

    def test_different_regions_low(self):
        fps = {k: dedup.fingerprint(crop(k)) for k in BOXES}
        names = list(fps)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                self.assertLess(dedup.similarity(fps[a], fps[b]), dedup.SIM_NEAR, f"{a} vs {b}")

    def test_multi_fingerprint_embedding(self):
        emb = dedup.merge_fingerprints(None, dedup.fingerprint(crop("right")))
        emb = dedup.merge_fingerprints(json.dumps(emb), dedup.fingerprint(crop("big")))
        self.assertEqual(len(emb["fps"]), 2)
        self.assertGreater(dedup.similarity(dedup.fingerprint(crop("right")), emb), 0.99)

    def test_haversine(self):
        self.assertAlmostEqual(dedup.haversine_m(LAT0, LON0, north(10), LON0), 10.0, delta=0.05)


class TestMatch(DbCase):
    def test_gps_3m_matches_40m_not(self):
        did = self.add_defect(img=crop("big"))
        fp = dedup.fingerprint(crop("big", dx=15, dy=8))
        got, info = dedup.match_defect(self.conn, "pothole", north(3), LON0, fp)
        self.assertEqual(got, did)
        self.assertAlmostEqual(info["distance_m"], 3.0, delta=0.1)
        self.assertEqual(info["candidates"], 1)
        self.assertTrue(set(["reason", "distance_m", "similarity", "candidates"]) <= set(info))

        got, info = dedup.match_defect(self.conn, "pothole", north(40), LON0, fp)
        self.assertIsNone(got)
        self.assertEqual(info["candidates"], 0)

    def test_10m_needs_high_similarity(self):
        did = self.add_defect(img=crop("big"))
        got, _ = dedup.match_defect(self.conn, "pothole", north(10), LON0,
                                    dedup.fingerprint(crop("big", dx=15, dy=8)))
        self.assertEqual(got, did)
        got, info = dedup.match_defect(self.conn, "pothole", north(10), LON0, dedup.fingerprint(crop("right")))
        self.assertIsNone(got)
        self.assertEqual(info["candidates"], 1)

    def test_nearby_different_pothole_not_merged(self):
        self.add_defect(img=crop("big"))
        got, info = dedup.match_defect(self.conn, "pothole", north(3), LON0, dedup.fingerprint(crop("mid")))
        self.assertIsNone(got)
        self.assertIn("look different", info["reason"])

    def test_best_candidate_wins(self):
        self.add_defect(img=crop("right"), lat=north(2))
        target = self.add_defect(img=crop("big"), lat=north(4))
        got, info = dedup.match_defect(self.conn, "pothole", LAT0, LON0, dedup.fingerprint(crop("big", pad=10)))
        self.assertEqual(got, target)
        self.assertEqual(info["candidates"], 2)

    def test_no_gps(self):
        did = self.add_defect(img=crop("big"))
        b = crop("big")
        jpeg = cv2.imdecode(cv2.imencode(".jpg", b, [cv2.IMWRITE_JPEG_QUALITY, 50])[1], 1)
        got, _ = dedup.match_defect(self.conn, "pothole", None, None, dedup.fingerprint(jpeg))
        self.assertEqual(got, did)
        got, info = dedup.match_defect(self.conn, "pothole", None, None, dedup.fingerprint(crop("big", dx=15, dy=8)))
        self.assertIsNone(got)
        self.assertIn("no GPS", info["reason"])

    def test_class_compatibility(self):
        did = self.add_defect(img=crop("big"))
        fp = dedup.fingerprint(crop("big", pad=5))
        self.assertIsNone(dedup.match_defect(self.conn, "transverse_crack", north(3), LON0, fp)[0])
        got, info = dedup.match_defect(self.conn, "alligator_crack", north(3), LON0, fp)
        self.assertEqual(got, did)
        self.assertIn("compatible class", info["reason"])

    def test_rejected_and_excluded_skipped(self):
        did = self.add_defect(img=crop("big"), status="rejected", workflow="rejected")
        fp = dedup.fingerprint(crop("big"))
        self.assertIsNone(dedup.match_defect(self.conn, "pothole", LAT0, LON0, fp)[0])
        did2 = self.add_defect(img=crop("big"))
        self.assertEqual(dedup.match_defect(self.conn, "pothole", LAT0, LON0, fp)[0], did2)
        self.assertIsNone(dedup.match_defect(self.conn, "pothole", LAT0, LON0, fp, exclude_ids={did2})[0])
        self.assertNotEqual(did, did2)

    def test_missing_fingerprint_gps_only(self):
        did = self.add_defect()  # no embedding stored
        self.assertEqual(dedup.match_defect(self.conn, "pothole", north(2), LON0, {})[0], did)
        self.assertIsNone(dedup.match_defect(self.conn, "pothole", north(8), LON0, {})[0])


class TestProgress(DbCase):
    def test_new_worsening_improving_stable(self):
        did = self.add_defect()
        i1 = self.add_inspection(0)
        self.add_obs(did, i1, 0, sev=40, area=0.020)
        self.assertEqual(dedup.update_progress(self.conn, did), "new")
        i2 = self.add_inspection(7)
        self.add_obs(did, i2, 7, sev=52, area=0.021)
        self.assertEqual(dedup.update_progress(self.conn, did), "worsening")
        self.assertEqual(self.defect(did)["trend"], "worsening")
        i3 = self.add_inspection(14)
        self.add_obs(did, i3, 14, sev=50, area=0.030)        # area +43 % -> worsening
        self.assertEqual(dedup.update_progress(self.conn, did), "worsening")
        i4 = self.add_inspection(21)
        self.add_obs(did, i4, 21, sev=38, area=0.024)
        self.assertEqual(dedup.update_progress(self.conn, did), "improving")
        i5 = self.add_inspection(28)
        self.add_obs(did, i5, 28, sev=41, area=0.025)
        self.assertEqual(dedup.update_progress(self.conn, did), "stable")

    def test_same_inspection_not_compared(self):
        did = self.add_defect()
        i1 = self.add_inspection(0)
        self.add_obs(did, i1, 0, sev=30)
        self.add_obs(did, i1, 0, sev=60)
        self.assertEqual(dedup.update_progress(self.conn, did), "new")

    def test_compute_trend_thresholds(self):
        ct = dedup.compute_trend
        self.assertEqual(ct({"severity_score": 50}, {"severity_score": 58}), "stable")
        self.assertEqual(ct({"severity_score": 50}, {"severity_score": 58.5}), "worsening")
        self.assertEqual(ct({"area_ratio": 0.1}, {"area_ratio": 0.084}), "improving")
        self.assertEqual(ct({"severity_score": 50, "area_ratio": 0.1},
                            {"severity_score": 40, "area_ratio": 0.2}), "improving")


class TestAbsenceAndRepair(DbCase):
    def test_absent_then_repaired_then_bad_repair(self):
        did = self.add_defect()
        far = self.add_defect(lat=north(50))
        i1 = self.add_inspection(0)
        self.add_obs(did, i1, 0)
        self.add_obs(far, i1, 0, lat=north(50))

        i2 = self.add_inspection(10)
        self.assertEqual(dedup.mark_absent(self.conn, north(5), LON0, i2, set()), [did])
        self.assertEqual(self.defect(did)["trend"], "possibly_repaired")
        self.assertEqual(self.defect(far)["trend"], "new")
        # idempotent for the same inspection; update_progress keeps the absence trend
        self.assertEqual(dedup.mark_absent(self.conn, north(5), LON0, i2, set()), [])
        self.assertEqual(dedup.update_progress(self.conn, did), "possibly_repaired")

        i3 = self.add_inspection(20)
        self.assertEqual(dedup.mark_absent(self.conn, LAT0, LON0, i3, set()), [did])
        d = self.defect(did)
        self.assertEqual((d["trend"], d["status"]), ("repaired", "repaired"))
        self.assertEqual(d["repaired_at"], ts(20))
        n_abs = self.conn.execute("SELECT COUNT(*) FROM observations WHERE defect_id=? AND absent=1",
                                  (did,)).fetchone()[0]
        self.assertEqual(n_abs, 2)

        # pothole is back 30 days after the repair -> bad repair, reopened
        i4 = self.add_inspection(50)
        self.add_obs(did, i4, 50, sev=55)
        self.assertTrue(dedup.check_bad_repair(self.conn, did))
        d = self.defect(did)
        self.assertEqual((d["bad_repair"], d["status"], d["workflow"]), (1, "open", "reported"))
        self.assertEqual(dedup.update_progress(self.conn, did), "worsening")  # 55 vs 40 pre-repair

    def test_seen_defect_not_marked_and_reappearance(self):
        did = self.add_defect()
        i1 = self.add_inspection(0)
        self.add_obs(did, i1, 0)
        i2 = self.add_inspection(5)
        self.add_obs(did, i2, 5)
        self.assertEqual(dedup.mark_absent(self.conn, LAT0, LON0, i2, {did}), [])
        i3 = self.add_inspection(6)
        dedup.mark_absent(self.conn, LAT0, LON0, i3, set())
        self.assertEqual(self.defect(did)["trend"], "possibly_repaired")
        i4 = self.add_inspection(8)
        self.add_obs(did, i4, 8, sev=41)
        self.assertEqual(dedup.update_progress(self.conn, did), "stable")  # absence is ignored

    def test_workflow_repaired_single_absence(self):
        did = self.add_defect(workflow="repaired", repaired_at=ts(3))
        i1 = self.add_inspection(0)
        self.add_obs(did, i1, 0)
        i2 = self.add_inspection(5)
        dedup.mark_absent(self.conn, LAT0, LON0, i2, set())
        d = self.defect(did)
        self.assertEqual((d["trend"], d["repaired_at"]), ("repaired", ts(3)))

    def test_reappearance_after_90_days_not_bad(self):
        did = self.add_defect(status="repaired", workflow="repaired", repaired_at=ts(0))
        i1 = self.add_inspection(120)
        self.add_obs(did, i1, 120)
        self.assertFalse(dedup.check_bad_repair(self.conn, did))
        d = self.defect(did)
        self.assertEqual((d["bad_repair"], d["status"]), (0, "open"))

    def test_not_repaired_no_flag(self):
        did = self.add_defect()
        i1 = self.add_inspection(0)
        self.add_obs(did, i1, 0)
        self.assertFalse(dedup.check_bad_repair(self.conn, did))
        self.assertEqual(self.defect(did)["status"], "open")


if __name__ == "__main__":
    unittest.main()
