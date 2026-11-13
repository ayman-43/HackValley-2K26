"""Tests for risk scoring, forecast, offline fallbacks and (live, skippable) external APIs.

Run with pytest, or directly: python backend/tests/test_risk.py"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import nagrik, risk  # noqa: E402

PRIMARY_NEAR_SCHOOL = {
    "road_type": "primary", "road_name": "100 Feet Road", "speed_limit": 60, "speed_source": "default",
    "lanes": 4, "source": "osm",
    "sensitive": [{"kind": "school", "name": "Test School", "distance_m": 80},
                  {"kind": "bus_stop", "name": "Test Stop", "distance_m": 40}],
}
RESIDENTIAL = {"road_type": "residential", "road_name": "3rd Cross", "speed_limit": 30, "speed_source": "default",
               "lanes": None, "sensitive": [], "source": "osm"}
RAIN = {"rain_next_72h_mm": 24.0, "rain_days": 2, "temp_c": 24.0, "source": "open-meteo"}
DRY = {"rain_next_72h_mm": 0.0, "rain_days": 0, "temp_c": 30.0, "source": "open-meteo"}


def _obs(days_ago: float, sev: float) -> dict:
    return {"observed_at": (datetime(2026, 11, 13) - timedelta(days=days_ago)).isoformat(), "severity_score": sev}


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(risk, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(nagrik, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(risk, "_overpass_down_until", 0.0)
    monkeypatch.setattr(risk, "_weather_down_until", 0.0)
    monkeypatch.setattr(risk, "_neg_cache", {})
    monkeypatch.setattr(nagrik, "_down_until", 0.0)
    return tmp_path


@pytest.fixture
def offline(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("offline")
    for name in ("get", "post", "request"):
        monkeypatch.setattr(httpx, name, boom)


# ---------------------------------------------------------------- compute_risk
def test_pothole_primary_school_rain_is_high_or_critical():
    d = {"damage_class": "pothole", "severity_score": 65, "severity_label": "High", "report_count": 3}
    r = risk.compute_risk(d, PRIMARY_NEAR_SCHOOL, RAIN)
    assert r["risk_label"] in ("High", "Critical"), r
    assert 0 <= r["risk_score"] <= 100
    assert abs(sum(f["points"] for f in r["risk_factors"]) - r["risk_score"]) < 0.5
    assert {f["factor"] for f in r["risk_factors"]} >= {"severity", "road_class", "speed", "sensitive_places",
                                                       "reports", "rain_forecast"}


def test_small_crack_residential_is_low():
    d = {"damage_class": "longitudinal_crack", "severity_score": 22, "report_count": 1}
    assert risk.compute_risk(d, RESIDENTIAL, DRY)["risk_label"] == "Low"


def test_risk_ordering_and_modifiers():
    base = {"damage_class": "pothole", "severity_score": 50, "report_count": 1}
    lo = risk.compute_risk(base, RESIDENTIAL, DRY)["risk_score"]
    hi = risk.compute_risk(base, PRIMARY_NEAR_SCHOOL, DRY)["risk_score"]
    rain = risk.compute_risk(base, PRIMARY_NEAR_SCHOOL, RAIN)["risk_score"]
    worse = risk.compute_risk({**base, "trend": "worsening", "bad_repair": 1, "report_count": 6},
                              PRIMARY_NEAR_SCHOOL, RAIN)["risk_score"]
    assert lo < hi < rain < worse
    crack = risk.compute_risk({**base, "damage_class": "longitudinal_crack"}, RESIDENTIAL, DRY)["risk_score"]
    assert crack < lo


def test_max_case_clipped_to_100_critical():
    d = {"damage_class": "pothole", "severity_score": 100, "report_count": 50, "trend": "worsening", "bad_repair": True}
    ctx = {**PRIMARY_NEAR_SCHOOL, "road_type": "trunk", "speed_limit": 80,
           "sensitive": [{"kind": "hospital", "name": "H", "distance_m": 10}] * 3}
    r = risk.compute_risk(d, ctx, {"rain_next_72h_mm": 80})
    assert r["risk_score"] == 100 and r["risk_label"] == "Critical"


def test_compute_risk_tolerates_missing_inputs():
    r = risk.compute_risk({}, None, None)
    assert r["risk_label"] in ("Low", "Medium") and r["risk_factors"]


# ---------------------------------------------------------------- forecast
def test_forecast_growing():
    f = risk.forecast([_obs(14, 30), _obs(7, 37), _obs(0, 44)], DRY)  # +7/week
    assert f["growth_per_week"] == pytest.approx(7.0, abs=0.1)
    assert f["days_to_high"] == 16  # (60-44)/1 per day
    assert "High severity" in f["text"]


def test_forecast_rain_boosts_growth():
    obs = [_obs(14, 30), _obs(7, 37), _obs(0, 44)]
    dry, wet = risk.forecast(obs, DRY), risk.forecast(obs, RAIN)
    assert wet["days_to_high"] < dry["days_to_high"]
    assert "24 mm" in wet["text"]


def test_forecast_shrinking():
    f = risk.forecast([_obs(10, 50), _obs(0, 40)], RAIN)
    assert f["days_to_high"] is None and f["growth_per_week"] < 0
    assert "Not growing" in f["text"]


def test_forecast_already_high():
    f = risk.forecast([_obs(7, 55), _obs(0, 70)], DRY)
    assert f["days_to_high"] is None and "Already High" in f["text"]


def test_forecast_single_observation():
    f = risk.forecast([_obs(0, 40)], RAIN)
    assert f["days_to_high"] is None and f["growth_per_week"] is None
    assert "rain" in f["text"].lower()
    assert risk.forecast([], DRY)["days_to_high"] is None


def test_forecast_ignores_absent_and_undated():
    obs = [_obs(7, 30), {"severity_score": 99}, {**_obs(3, 0), "absent": 1}, _obs(0, 37)]
    assert risk.forecast(obs, DRY)["growth_per_week"] == pytest.approx(7.0, abs=0.1)


# ---------------------------------------------------------------- offline fallbacks
def test_road_context_offline_default(tmp_cache, offline):
    c = risk.road_context(12.9, 77.6)
    assert c["source"] == "default" and c["road_type"] == "unknown" and c["sensitive"] == []
    r = risk.compute_risk({"damage_class": "pothole", "severity_score": 50}, c, risk.weather(12.9, 77.6))
    assert r["risk_label"] in ("Low", "Medium", "High")


def test_road_context_offline_uses_nearby_cache(tmp_cache, offline):
    risk._cache_put("osm", risk._key(12.9719, 77.6412), {**PRIMARY_NEAR_SCHOOL})
    c = risk.road_context(12.97205, 77.64125)  # ~17 m away, different cache key
    assert c["road_type"] == "primary"


def test_negative_cache_and_total_timeout(tmp_cache, monkeypatch):
    calls = []

    def slow_fail(url, *a, timeout=None, **k):
        calls.append(getattr(timeout, "read", timeout))
        raise httpx.ReadTimeout("slow")
    monkeypatch.setattr(httpx, "post", slow_fail)
    monkeypatch.setattr(httpx, "get", slow_fail)
    monkeypatch.setattr(httpx, "request", lambda *a, timeout=None, **k: slow_fail(None, timeout=timeout))
    assert risk.road_context(12.9, 77.6)["source"] == "default"
    assert calls and max(calls) <= risk.TIMEOUT_S  # each mirror gets only the remaining budget
    n = len(calls)
    assert risk.road_context(12.9, 77.6)["source"] == "default"
    assert risk.road_context(13.0, 77.5)["source"] == "default"
    risk.weather(12.9, 77.6)
    m = len(calls)
    risk.weather(12.9, 77.6)
    risk.weather(13.1, 77.5)
    assert m == n + 1 and len(calls) == m  # failed lookups are remembered -> no new network calls
    nagrik.resolve(12.97, 77.64)
    k = len(calls)
    nagrik.resolve(12.97, 77.64)
    nagrik.ward_works("BLR", 1)
    assert len(calls) == k == m + 1


def test_weather_offline_default_and_stale(tmp_cache, offline):
    w = risk.weather(12.9, 77.6)
    assert w["rain_next_72h_mm"] == 0.0 and w["source"] == "default"
    risk._cache_put("weather", "12.90_77.60", {**RAIN})
    p = tmp_cache / "weather_12.90_77.60.json"
    p.write_text(p.read_text().replace('"_ts": ', '"_ts": 1, "_old": '))  # make it expired
    assert risk.weather(12.9, 77.6)["rain_next_72h_mm"] == 24.0


def test_nagrik_offline_never_crashes(tmp_cache, offline):
    assert nagrik.city_for(12.97, 77.64) == "BLR"  # built-in fallback bounds
    assert nagrik.city_for(51.5, -0.1) is None
    assert nagrik.resolve(12.97, 77.64) is None
    assert nagrik.resolve(51.5, -0.1) is None
    assert nagrik.ward_works("BLR", 123) == []
    assert nagrik.ward_works("XXX", 1) == []


def test_shipped_demo_cache_works_offline(offline, monkeypatch):
    """The 3 demo points are pre-cached in backend/data/cache and must work with no network."""
    monkeypatch.setattr(risk, "_overpass_down_until", 0.0)
    for lat, lon in [(12.9719, 77.6412), (12.9352, 77.6245), (12.9767, 77.5713)]:
        r = nagrik.resolve(lat, lon)
        assert r and r["city"] == "BLR" and r["ward_id"] and r["corporation"]
        assert isinstance(nagrik.ward_works(r["city"], r["ward_id"]), list)
        assert risk.road_context(lat, lon)["source"] in ("osm", "default")


# ---------------------------------------------------------------- live smoke tests (skip offline)
def _online(url: str) -> bool:
    try:
        httpx.get(url, timeout=5)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _online("https://api.open-meteo.com"), reason="offline")
def test_live_open_meteo(tmp_cache):
    w = risk.weather(12.9719, 77.6412)
    assert w["source"] == "open-meteo" and w["rain_next_72h_mm"] >= 0


@pytest.mark.skipif(not _online("https://nagrik.in.net/api/v1/healthz"), reason="offline")
def test_live_nagrik(tmp_cache):
    r = nagrik.resolve(12.9352, 77.6245)
    assert r and r["city"] == "BLR" and r["ward_name"]
    assert isinstance(nagrik.ward_works("BLR", r["ward_id"]), list)


@pytest.mark.skipif(not _online("https://overpass-api.de/api/status"), reason="offline")
def test_live_overpass(tmp_cache):
    c = risk.road_context(12.9719, 77.6412, timeout_s=25)  # Overpass is often slow
    if c["source"] == "default":
        pytest.skip("Overpass busy/timed out")
    assert c["road_type"] != "unknown"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
