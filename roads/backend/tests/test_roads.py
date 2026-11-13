"""Tests for roads.py (road health segments).

Run: .venv\\Scripts\\python.exe backend/tests/test_roads.py   (pytest also works if installed)
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["ROADSENSE_DATA_DIR"] = tempfile.mkdtemp(prefix="rs_roads_")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app import db, roads  # noqa: E402

db.init_db()
CALLS = []

# Road A "Bad Road": 6 high-risk potholes in ~250 m (runs north-south at lon 77.6408)
BAD = [(12.9700 + i * 0.00045, 77.6408, 85, 90) for i in range(6)]
# Road B "Good Road": 3 low-risk cracks spread over ~700 m (runs east-west at lat 12.9900), 2 km from road A
GOOD = [(12.9900, 77.6500, 15, 20), (12.9900, 77.6532, 20, 25), (12.9900, 77.6564, 18, 22)]


def _offline(bboxes):
    CALLS.append(bboxes)
    raise OSError("network unreachable (test)")


def _fake_ways(bboxes):
    CALLS.append(bboxes)
    return [
        {"id": 101, "name": "Bad Road OSM", "highway": "primary",
         "coords": [[12.9695, 77.64081], [12.9712, 77.64081], [12.9730, 77.64081]]},
        {"id": 102, "name": "Good Road OSM", "highway": "residential",
         "coords": [[12.99001, 77.6490], [12.99001, 77.6560], [12.99001, 77.6630]]},
        {"id": 103, "name": "Untouched Lane", "highway": "service",   # ~110 m east of road A: nothing snaps to it
         "coords": [[12.9695, 77.6418], [12.9730, 77.6418]]},
    ]


def _seed(named=True):
    roads._down_until = 0.0
    for f in roads.CACHE_DIR.glob("overpass_*.json") if roads.CACHE_DIR.exists() else []:
        f.unlink()
    CALLS.clear()
    with db.connect() as c:
        c.execute("DELETE FROM defects")
        n = 0
        for road, pts, cls, sev in (("Bad Road", BAD, "pothole", "High"), ("Good Road", GOOD, "longitudinal_crack", "Low")):
            for la, lo, risk, s in pts:
                n += 1
                c.execute("INSERT INTO defects (code, damage_class, lat, lon, risk_score, severity_score, "
                          "severity_label, road_name, workflow, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (f"R-{n}", cls, la, lo, risk, s, sev, road if named else None, "reported", "open"))
        # must all be ignored: repaired, rejected, closed, no GPS
        c.execute("INSERT INTO defects (code, damage_class, lat, lon, risk_score, road_name, workflow, status) "
                  "VALUES ('R-rep','pothole',12.9701,77.6408,99,'Bad Road','repaired','repaired')")
        c.execute("INSERT INTO defects (code, damage_class, lat, lon, risk_score, road_name, workflow, status) "
                  "VALUES ('R-rej','pothole',12.9702,77.6408,99,'Bad Road','rejected','open')")
        c.execute("INSERT INTO defects (code, damage_class, lat, lon, risk_score, road_name, workflow, status) "
                  "VALUES ('R-cls','pothole',12.9703,77.6408,99,'Bad Road','closed','open')")
        c.execute("INSERT INTO defects (code, damage_class, risk_score, road_name, workflow, status) "
                  "VALUES ('R-nogps','pothole',99,'Bad Road','reported','open')")


def test_point_to_polyline_distance():
    line = [[12.97, 77.64], [12.98, 77.64]]
    d = roads.point_to_polyline_m(12.975, 77.6401, line)       # 0.0001 deg of longitude east of the line
    assert 10.0 < d < 11.5, d
    assert roads.point_to_polyline_m(12.975, 77.64, line) < 0.01
    end = roads.point_to_polyline_m(12.9699, 77.64, line)      # beyond the end: distance to the end point
    assert 10.5 < end < 11.7, end


def test_health_formula_and_labels():
    h, label, dens = roads.health_score(1, 1000, 10, 10, 10)   # 1 small defect on 1 km
    assert label == "Good" and dens == 1.0 and h == round(100 - 2 - 3.5 - 2.5, 1), (h, label, dens)
    h, label, _ = roads.health_score(4, 1000, 50, 40, 50)      # 8 + 16.1 + 12.5 = 36.6 penalty
    assert label == "Fair" and 40 <= h < 70, h
    h, label, _ = roads.health_score(30, 500, 90, 80, 95)
    assert label == "Poor" and h < 40, h
    _, _, dens = roads.health_score(1, 10, 0, 0, 0)            # length floored at 100 m
    assert dens == 10.0, dens


def test_offline_fallback_groups_by_road_name():
    _seed()
    roads.fetch_overpass = _offline
    out = roads.road_health()
    assert out["source"] == "approx" and len(CALLS) == 1
    segs = out["segments"]
    assert len(segs) == 2, [s["name"] for s in segs]
    assert all(s["geometry"] == "approx" for s in segs)
    bad, good = segs                                            # worst first
    assert bad["name"] == "Bad Road" and bad["defect_count"] == 6 and bad["label"] == "Poor", bad
    assert good["name"] == "Good Road" and good["defect_count"] == 3 and good["label"] == "Good", good
    assert bad["health"] < good["health"]
    assert bad["by_class"] == {"pothole": 6} and good["by_class"] == {"longitudinal_crack": 3}
    assert 230 < bad["length_m"] < 270 and 650 < good["length_m"] < 730, (bad["length_m"], good["length_m"])
    lats = [c[0] for c in bad["coordinates"]]
    assert lats == sorted(lats) or lats == sorted(lats, reverse=True)   # ordered along the road
    assert bad["max_risk"] == 85 and bad["worst_severity"] == 90 and bad["worst_severity_label"] == "High"
    assert out["summary"]["defects"] == 9 and len(out["defects"]) == 9  # repaired/rejected/closed/no-GPS excluded
    assert out["summary"]["by_label"] == {"Good": 1, "Fair": 0, "Poor": 1}
    roads.road_health()                                         # failure is negative-cached: no second request
    assert len(CALLS) == 1


def test_offline_fallback_grid_when_no_road_name():
    _seed(named=False)
    roads.fetch_overpass = _offline
    segs = roads.road_health()["segments"]
    assert sum(s["defect_count"] for s in segs) == 9
    assert all(s["name"] is None and s["geometry"] == "approx" for s in segs)
    ids_bad = {d for s in segs for d in s["defect_ids"] if s["max_risk"] == 85}
    ids_good = {d for s in segs for d in s["defect_ids"] if s["max_risk"] <= 20}
    assert len(ids_bad) == 6 and len(ids_good) == 3             # the two roads never share a grid cell
    assert segs[0]["max_risk"] == 85                            # worst first


def test_osm_snapping_uses_way_geometry_and_caches():
    _seed()
    roads.fetch_overpass = _fake_ways
    out = roads.road_health()
    assert out["source"] == "osm", out["source"]
    segs = out["segments"]
    assert [s["id"] for s in segs] == ["osm-101", "osm-102"], [s["id"] for s in segs]   # way 103 has no defects
    bad, good = segs
    assert bad["geometry"] == "osm" and bad["name"] == "Bad Road OSM" and bad["highway"] == "primary"
    assert bad["defect_count"] == 6 and good["defect_count"] == 3
    assert bad["coordinates"] == [[12.9695, 77.64081], [12.9712, 77.64081], [12.973, 77.64081]]
    assert 380 < bad["length_m"] < 400, bad["length_m"]        # length of the OSM way, not of the defect run
    assert bad["label"] == "Poor" and good["label"] == "Good" and bad["health"] < good["health"]
    assert all(d["segment_id"] in ("osm-101", "osm-102") for d in out["defects"])
    n = len(CALLS)
    again = roads.road_health()                                 # second call is served from the disk cache
    assert len(CALLS) == n and [s["id"] for s in again["segments"]] == ["osm-101", "osm-102"]
    roads.fetch_overpass = _offline                             # and keeps working when Overpass then goes down
    assert roads.road_health()["source"] == "osm"


def test_far_defect_falls_back_to_approx_segment():
    _seed()
    with db.connect() as c:   # 300+ m from every fake way
        c.execute("INSERT INTO defects (code, damage_class, lat, lon, risk_score, severity_score, workflow, status) "
                  "VALUES ('R-far','pothole',12.9760,77.6450,50,50,'reported','open')")
    roads.fetch_overpass = _fake_ways
    out = roads.road_health()
    assert out["source"] == "mixed"
    kinds = sorted(s["geometry"] for s in out["segments"])
    assert kinds == ["approx", "osm", "osm"], kinds
    far = next(s for s in out["segments"] if s["geometry"] == "approx")
    assert far["defect_count"] == 1 and len(far["coordinates"]) == 2


def test_empty_db_and_api_route():
    _seed()
    with db.connect() as c:
        c.execute("DELETE FROM defects")
    roads.fetch_overpass = _offline
    out = roads.roads_health()
    assert out["segments"] == [] and out["defects"] == [] and CALLS == []
    assert any(getattr(r, "path", "") == "/api/roads/health" for r in roads.router.routes)


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
