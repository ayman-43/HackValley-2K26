"""SQLite storage. One physical defect (pothole/crack) = one row in `defects`.
Every sighting of it (image, video frame, re-inspection) = one row in `observations`.
"""
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

# ROADSENSE_DATA_DIR lets tests use an isolated database + media folder
DATA_DIR = Path(os.environ.get("ROADSENSE_DATA_DIR") or Path(__file__).resolve().parents[1] / "data")
MEDIA_DIR = DATA_DIR / "media"
DB_PATH = DATA_DIR / "roadsense.db"
DATA_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_DIR.mkdir(exist_ok=True)

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS inspections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    source_type TEXT NOT NULL,          -- image | video
    filename TEXT,
    media_path TEXT,                    -- annotated output
    lat REAL, lon REAL,
    road_type TEXT,
    road_name TEXT,
    speed_limit INTEGER,
    frames_processed INTEGER DEFAULT 0,
    raw_detections INTEGER DEFAULT 0,   -- detections before tracking/dedup
    unique_defects INTEGER DEFAULT 0,   -- after tracking
    new_defects INTEGER DEFAULT 0,
    duplicate_defects INTEGER DEFAULT 0,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS defects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE,
    damage_class TEXT NOT NULL,
    lat REAL, lon REAL,
    first_seen TEXT, last_seen TEXT,
    status TEXT DEFAULT 'open',         -- open | repaired | rejected
    severity_score REAL, severity_label TEXT,
    risk_score REAL, risk_label TEXT, risk_factors TEXT,
    trend TEXT DEFAULT 'new',           -- new | worsening | improving | stable | possibly_repaired | repaired
    report_count INTEGER DEFAULT 1,
    road_type TEXT, road_name TEXT, speed_limit INTEGER,
    embedding TEXT,                     -- duplicate-detection fingerprint (JSON, owned by dedup.py)
    workflow TEXT DEFAULT 'reported',   -- reported | verified | scheduled | repaired | closed | rejected
    forecast TEXT,                      -- JSON from risk.forecast()
    ward_name TEXT, ward_id INTEGER, corporation TEXT, city TEXT,
    nagrik TEXT,                        -- JSON: officials + tenders from nagrik.py
    bad_repair INTEGER DEFAULT 0,       -- 1 = reappeared within 90 days of repair
    repaired_at TEXT, repair_photo TEXT
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    defect_id INTEGER REFERENCES defects(id),
    inspection_id INTEGER REFERENCES inspections(id),
    observed_at TEXT NOT NULL,
    damage_class TEXT,
    confidence REAL,
    bbox TEXT,                          -- [x1,y1,x2,y2] in frame pixels
    frame_w INTEGER, frame_h INTEGER,
    area_ratio REAL,
    depth_index REAL,
    severity_score REAL, severity_label TEXT,
    track_id INTEGER, frames_seen INTEGER,
    frame_path TEXT, crop_path TEXT,
    lat REAL, lon REAL,
    match_info TEXT,                    -- why it was matched to an existing defect
    absent INTEGER DEFAULT 0,           -- 1 = location re-inspected, defect not found
    review_status TEXT DEFAULT 'pending', -- pending | confirmed | rejected | corrected
    corrected_class TEXT, corrected_bbox TEXT,
    reviewed_at TEXT, reviewer TEXT
);
CREATE INDEX IF NOT EXISTS idx_obs_defect ON observations(defect_id);
CREATE INDEX IF NOT EXISTS idx_obs_review ON observations(review_status);
"""


def init_db():
    with connect() as c:
        c.executescript(SCHEMA)


@contextmanager
def connect():
    with _lock:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def row(r):
    if r is None:
        return None
    d = dict(r)
    for k in ("bbox", "corrected_bbox", "risk_factors", "match_info", "forecast", "nagrik"):
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    d.pop("embedding", None)
    return d
