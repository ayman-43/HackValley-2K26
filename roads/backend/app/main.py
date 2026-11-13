"""Nagrik Roads FastAPI server: uploads, background processing queue, defect/inspection API, static UI.

Run from repo root:  .venv\\Scripts\\python.exe -m uvicorn backend.app.main:app --port 8000
"""
import json
import logging
import queue
import threading
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import db, detector, pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("roadsense.main")

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
MODELS_DIR = ROOT / "backend" / "models"
EVAL_DIR = db.DATA_DIR / "eval"
EVAL_DIR.mkdir(exist_ok=True)

# ------------------------------------------------------------------ job queue
JOBS: dict[str, dict] = {}
_jobs_q: "queue.Queue[str]" = queue.Queue()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()


def _work():
    while True:
        job_id = _jobs_q.get()
        job = JOBS[job_id]
        job.update(status="running", started_at=pipeline.now_iso())

        def cb(p, job=job):
            job["progress"] = round(max(job["progress"], min(1.0, float(p))), 3)

        try:
            job["result"] = pipeline.process_inspection(job["inspection_id"], progress_cb=cb)
            job.update(status="done", progress=1.0)
        except Exception as e:
            log.error("job %s failed: %s\n%s", job_id, e, traceback.format_exc())
            job.update(status="error", error=str(e))
        finally:
            job["finished_at"] = pipeline.now_iso()
            _jobs_q.task_done()


def _ensure_worker():
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, name="roadsense-worker", daemon=True)
            _worker.start()


def enqueue(inspection_id: int) -> str:
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"job_id": job_id, "inspection_id": inspection_id, "status": "queued", "progress": 0.0,
                    "result": None, "error": None, "created_at": pipeline.now_iso()}
    _ensure_worker()
    _jobs_q.put(job_id)
    return job_id


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    _ensure_worker()
    yield


app = FastAPI(title="Nagrik Roads API", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
db.init_db()

# Officials login. Loaded directly (not "optional" like the routers below): if it failed silently,
# every protected action would become public.
from . import auth  # noqa: E402
auth.init()
app.middleware("http")(auth.guard)
app.include_router(auth.router)

for _name in ("review", "reports", "crew", "live", "roads"):
    try:
        _mod = __import__(f"{__package__}.{_name}", fromlist=["router"])
        app.include_router(_mod.router)
    except Exception as _e:  # optional module missing/broken: keep the core API up
        log.warning("router %s not loaded: %s", _name, _e)


# ------------------------------------------------------------------ helpers
def _obs_urls(o: dict) -> dict:
    o["frame_url"] = o.get("frame_path")
    o["crop_url"] = o.get("crop_path")
    o["image_url"] = o.get("crop_path") or o.get("frame_path")
    return o


def _thumbs(conn, ids: list[int]) -> dict[int, dict]:
    """Latest non-absent observation image per defect."""
    if not ids:
        return {}
    q = (f"SELECT defect_id, crop_path, frame_path FROM observations WHERE absent=0 AND defect_id IN "
         f"({','.join('?' * len(ids))}) ORDER BY observed_at, id")
    out = {}
    for r in conn.execute(q, ids):
        out[r["defect_id"]] = {"thumb_url": r["crop_path"] or r["frame_path"], "frame_url": r["frame_path"]}
    return out


# ------------------------------------------------------------------ API
@app.post("/api/inspections")
async def create_inspection(file: UploadFile = File(...), lat: float | None = Form(None),
                            lon: float | None = Form(None), gps_track: UploadFile | None = File(None),
                            road_type: str | None = Form(None), notes: str | None = Form(None)):
    name = file.filename or "upload"
    ext = Path(name).suffix.lower()
    ctype = (file.content_type or "").lower()
    if ctype.startswith("image/") or ext in pipeline.IMAGE_EXT:
        source_type = "image"
        ext = ext or ".jpg"
    elif ctype.startswith("video/") or ext in {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".3gp"}:
        source_type = "video"
        ext = ext or ".mp4"
    else:
        raise HTTPException(400, f"Unsupported file type: {ctype or ext or 'unknown'}")
    with db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO inspections (created_at, source_type, filename, lat, lon, road_type, notes) "
            "VALUES (?,?,?,?,?,?,?)",
            (pipeline.now_iso(), source_type, name, lat, lon, road_type or None, notes))
        iid = cur.lastrowid
    d = pipeline.upload_dir(iid)
    with open(d / f"input{ext}", "wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)
    if gps_track is not None and gps_track.filename:
        gext = Path(gps_track.filename).suffix.lower() or ".json"
        (d / f"gps_track{gext}").write_bytes(await gps_track.read())
    job_id = enqueue(iid)
    return {"inspection_id": iid, "job_id": job_id, "source_type": source_type}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@app.get("/api/jobs")
def list_jobs():
    return sorted(JOBS.values(), key=lambda j: j["created_at"], reverse=True)


def _inspection(r) -> dict:
    d = db.row(r)
    d["media_url"] = d.get("media_path")
    return d


@app.get("/api/inspections")
def list_inspections(limit: int = 200):
    with db.connect() as conn:
        return [_inspection(r) for r in conn.execute("SELECT * FROM inspections ORDER BY id DESC LIMIT ?", (limit,))]


@app.get("/api/inspections/{inspection_id}")
def get_inspection(inspection_id: int):
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM inspections WHERE id=?", (inspection_id,)).fetchone()
        if r is None:
            raise HTTPException(404, "inspection not found")
        d = _inspection(r)
        d["observations"] = [_obs_urls(db.row(o)) for o in conn.execute(
            "SELECT o.*, d.code AS defect_code, d.road_name, d.ward_name, d.risk_label, d.trend "
            "FROM observations o LEFT JOIN defects d ON d.id=o.defect_id "
            "WHERE o.inspection_id=? ORDER BY o.id", (inspection_id,))]
    d["jobs"] = [j for j in JOBS.values() if j["inspection_id"] == inspection_id]
    return d


@app.get("/api/defects")
def list_defects(status: str | None = None, severity: str | None = None, damage_class: str | None = None,
                 ward: str | None = None, min_risk: float | None = None, workflow: str | None = None,
                 limit: int = 1000):
    q, args = "SELECT * FROM defects WHERE 1=1", []
    if status:
        q += " AND (status=? OR workflow=?)"
        args += [status, status]
    if workflow:
        q += " AND workflow=?"
        args.append(workflow)
    if severity:
        q += " AND LOWER(severity_label)=LOWER(?)"
        args.append(severity)
    if damage_class:
        if damage_class == "crack":
            q += " AND damage_class LIKE '%crack'"
        else:
            q += " AND damage_class=?"
            args.append(damage_class)
    if ward:
        q += " AND (LOWER(ward_name)=LOWER(?) OR CAST(ward_id AS TEXT)=?)"
        args += [ward, ward]
    if min_risk is not None:
        q += " AND COALESCE(risk_score,0)>=?"
        args.append(min_risk)
    q += " ORDER BY COALESCE(risk_score,-1) DESC, COALESCE(severity_score,0) DESC, id DESC LIMIT ?"
    args.append(limit)
    with db.connect() as conn:
        rows = [db.row(r) for r in conn.execute(q, args)]
        th = _thumbs(conn, [r["id"] for r in rows])
    for r in rows:
        r.update(th.get(r["id"], {"thumb_url": None, "frame_url": None}))
    return rows


@app.get("/api/defects/{defect_id}")
def get_defect(defect_id: int):
    with db.connect() as conn:
        r = conn.execute("SELECT * FROM defects WHERE id=?", (defect_id,)).fetchone()
        if r is None:
            raise HTTPException(404, "defect not found")
        d = db.row(r)
        d.update(_thumbs(conn, [defect_id]).get(defect_id, {"thumb_url": None, "frame_url": None}))
        d["observations"] = [_obs_urls(db.row(o)) for o in conn.execute(
            "SELECT o.*, i.source_type, i.filename FROM observations o LEFT JOIN inspections i "
            "ON i.id=o.inspection_id WHERE o.defect_id=? ORDER BY o.observed_at, o.id", (defect_id,))]
    return d


@app.get("/api/stats")
def stats():
    with db.connect() as conn:
        try:  # keep the "awaiting review" count honest: confident detections never need a person
            from . import review as _review
            _review.auto_approve(conn)
        except Exception as e:  # noqa: BLE001
            log.warning("auto_approve failed: %s", e)
        def group(col):
            return {k if k is not None else "unknown": n for k, n in conn.execute(
                f"SELECT {col}, COUNT(*) FROM defects GROUP BY {col}")}

        one = lambda q: conn.execute(q).fetchone()[0] or 0  # noqa: E731
        out = {
            "total_defects": one("SELECT COUNT(*) FROM defects"),
            "open_defects": one("SELECT COUNT(*) FROM defects WHERE COALESCE(status,'open')='open'"),
            "inspections": one("SELECT COUNT(*) FROM inspections"),
            "raw_detections": one("SELECT SUM(raw_detections) FROM inspections"),
            "unique_defects": one("SELECT SUM(unique_defects) FROM inspections"),
            "new_defects": one("SELECT SUM(new_defects) FROM inspections"),
            "duplicate_defects": one("SELECT SUM(duplicate_defects) FROM inspections"),
            "observations": one("SELECT COUNT(*) FROM observations WHERE absent=0"),
            "pending_review": one("SELECT COUNT(*) FROM observations WHERE review_status='pending' AND absent=0"),
            "bad_repairs": one("SELECT COUNT(*) FROM defects WHERE bad_repair=1"),
            "by_class": group("damage_class"),
            "by_severity": group("severity_label"),
            "by_risk": group("risk_label"),
            "by_workflow": group("workflow"),
            "by_trend": group("trend"),
        }
    out["jobs_running"] = sum(1 for j in JOBS.values() if j["status"] in ("queued", "running"))
    out["model"] = detector.model_info()
    return out


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": f"could not read {p.name}: {e}"}


@app.get("/api/metrics")
def metrics():
    """Flat {file_stem: json} of backend/models/*_metrics.json and backend/data/eval/*.json,
    plus `_images`: URLs of eval images (e.g. confusion matrices) served under /eval."""
    out = {}
    for p in sorted(MODELS_DIR.glob("*_metrics.json")):
        out[p.stem] = _read_json(p)
    for p in sorted(EVAL_DIR.glob("*.json")):
        out[p.stem if p.stem not in out else f"eval_{p.stem}"] = _read_json(p)
    out["_images"] = [f"/eval/{p.relative_to(EVAL_DIR).as_posix()}" for p in sorted(EVAL_DIR.rglob("*"))
                      if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".svg"}]
    return out


@app.get("/api/health")
def health():
    return {"ok": True, "model": detector.model_info(), "worker_alive": bool(_worker and _worker.is_alive())}


# ------------------------------------------------------------------ training monitor
# Read-only view of the training runs for the /training.html page. Nothing here touches the pipeline.
import os as _os
import re as _re
import subprocess as _sp
import time as _time

TRAIN_HOME = Path(_os.environ.get("ROADSENSE_TRAIN_DIR", str(Path.home() / "roadsense-data")))
TRAIN_RUN = TRAIN_HOME / "runs" / "rdd_us_in_kaggle_y26n"
KAGGLE_KERNEL = "aymandehalvi/roadsense-y26s-training"
_SOURCES = {"in": "RDD2022 India", "us": "RDD2022 United States", "bp": "BharatPotHole", "kg": "Kaggle Potholes"}
_cache = {}


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and _time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (_time.time(), val)
    return val


def _dataset_counts():
    out = {}
    for split in ("train", "val"):
        d = TRAIN_HOME / "roadsense" / "images" / split
        counts = {}
        if d.is_dir():
            for name in _os.listdir(d):
                pre = name.split("_", 1)[0]
                counts[pre] = counts.get(pre, 0) + 1
        out[split] = counts
    return [{"key": k, "name": n, "train": out["train"].get(k, 0), "val": out["val"].get(k, 0)}
            for k, n in _SOURCES.items()]


def _kaggle_status():
    try:
        r = _sp.run(["kaggle", "kernels", "status", KAGGLE_KERNEL], capture_output=True, text=True, timeout=25)
        m = _re.search(r'status "(?:KernelWorkerStatus\.)?(\w+)"', r.stdout)
        return {"status": m.group(1).lower() if m else "unknown", "kernel": KAGGLE_KERNEL}
    except Exception as e:
        return {"status": "unknown", "kernel": KAGGLE_KERNEL, "error": str(e)[:120]}


RESULTS_DIR = ROOT / "docs" / "results"   # finished Kaggle YOLO26s run (results.csv, metrics, plots)
RESULT_IMAGES = {"training_curves.png", "confusion_matrix.png", "precision_recall_curve.png", "f1_curve.png",
                 "example_predictions.jpg", "example_ground_truth.jpg"}


def _epochs_csv(csv: Path) -> list:
    """Ultralytics results.csv -> one dict per epoch."""
    epochs = []
    if not csv.is_file():
        return epochs
    lines = csv.read_text().strip().splitlines()
    head = [h.strip() for h in lines[0].split(",")]
    for ln in lines[1:]:
        row = dict(zip(head, (v.strip() for v in ln.split(","))))
        try:
            epochs.append({"epoch": int(float(row["epoch"])),
                           "precision": float(row["metrics/precision(B)"]),
                           "recall": float(row["metrics/recall(B)"]),
                           "map50": float(row["metrics/mAP50(B)"]),
                           "map50_95": float(row["metrics/mAP50-95(B)"]),
                           "box_loss": float(row["train/box_loss"]),
                           "cls_loss": float(row["train/cls_loss"])})
        except (KeyError, ValueError):
            pass
    return epochs


def _kaggle_run() -> dict:
    epochs = _epochs_csv(RESULTS_DIR / "kaggle_y26s_results.csv")
    mfile = next((p for p in (MODELS_DIR / "roadsense_y26s_metrics.json", RESULTS_DIR / "roadsense_y26s_metrics.json")
                  if p.is_file()), None)
    metrics = _read_json(mfile) if mfile else None
    if epochs:  # finished: the results were downloaded, no need to ask the Kaggle CLI
        status = {"status": "complete", "kernel": KAGGLE_KERNEL}
    else:
        status = _cached("kaggle", 120, _kaggle_status)
    images = [n for n in sorted(RESULT_IMAGES) if (RESULTS_DIR / n).is_file()]
    return {"model": "YOLO26s", "device": "Kaggle GPU (Tesla T4)", "imgsz": 640, "max_epochs": 150, **status,
            "epochs": epochs, "metrics": metrics, "in_app": detector.model_info().get("name") == "roadsense_y26s.pt",
            "images": [f"/api/training/results/{n}" for n in images]}


@app.get("/api/training/results/{name}", include_in_schema=False)
def training_result_img(name: str):
    if name not in RESULT_IMAGES or not (RESULTS_DIR / name).is_file():
        raise HTTPException(404)
    return FileResponse(RESULTS_DIR / name)


@app.get("/api/training")
def training():
    epochs = _epochs_csv(TRAIN_RUN / "results.csv")
    live = {"running": False}
    log = TRAIN_HOME / "train_log.txt"
    if log.is_file():
        age = _time.time() - log.stat().st_mtime
        with open(log, "rb") as f:
            f.seek(max(0, log.stat().st_size - 300000))
            tail = f.read().decode("utf-8", "ignore").replace("\r", "\n")
        prog = _re.findall(r"(\d+)/(\d+)\s+[\d.]+G\s+([\d.]+)\s+([\d.]+).*?(\d+)%.*?(\d+)/(\d+)\s+([\d.]+)it/s\s+([\d:]+)<([\d:]+)", tail)
        live = {"running": age < 120, "updated_s_ago": int(age)}
        if prog:
            e, et, box, cls, pct, b, bt, ips, el, eta = prog[-1]
            last = dict(epoch=int(e), epochs=int(et), batch=int(b), batches=int(bt), pct=int(pct),
                        box_loss=float(box), cls_loss=float(cls), it_s=float(ips), eta=eta)
            # between epochs the log shows validation lines instead; remember the last training line
            _cache["live_last"] = last
            validating = tail.rfind("mAP50") > tail.rfind(f"{e}/{et}")
            live.update(last, phase="validating" if validating else "training")
        elif _cache.get("live_last"):
            live.update(_cache["live_last"], pct=100, phase="validating")
    return {
        "local": {"model": "YOLO26n", "device": "Laptop GPU (RTX 2050, 4 GB)", "imgsz": 640, "batch": 8,
                  "epochs": epochs, "live": live},
        "kaggle": _kaggle_run(),
        "dataset": _cached("ds", 3600, _dataset_counts),
        "classes": ["Longitudinal crack", "Transverse crack", "Alligator crack", "Pothole"],
    }


@app.get("/api/training/img/{name}", include_in_schema=False)
def training_img(name: str):
    if name not in {"train_batch0.jpg", "train_batch1.jpg", "train_batch2.jpg", "labels.jpg"}:
        raise HTTPException(404)
    p = TRAIN_RUN / name
    if not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


# ------------------------------------------------------------------ ward boundaries (Nagrik)
WARDS_CACHE = Path(__file__).resolve().parents[1] / "data" / "cache"


@app.get("/api/wards/geojson")
def wards_geojson(city: str = "BLR"):
    """Ward boundaries from Nagrik's public API (nagrik.in.net, used with attribution).
    Fetched once and cached on disk, so the map never calls Nagrik per page view."""
    city = "".join(ch for ch in city.upper() if ch.isalpha())[:4] or "BLR"
    p = WARDS_CACHE / f"nagrik_wards_{city}.geojson"
    if not p.is_file():
        try:
            import httpx
            r = httpx.get("https://nagrik.in.net/api/v1/wards/geojson", params={"city": city}, timeout=30,
                          headers={"User-Agent": "NagrikRoads-hackathon/0.1"})
            r.raise_for_status()
            data = r.json()
            if data.get("type") != "FeatureCollection":
                raise ValueError("unexpected response")
            WARDS_CACHE.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data), encoding="utf-8")
        except Exception as e:
            raise HTTPException(503, f"ward boundaries unavailable: {e}") from e
    return FileResponse(p, media_type="application/geo+json", headers={"Cache-Control": "public, max-age=86400"})


# ------------------------------------------------------------------ static
app.mount("/media", StaticFiles(directory=str(db.MEDIA_DIR)), name="media")
app.mount("/eval", StaticFiles(directory=str(EVAL_DIR)), name="eval")


def _frontend_file(rel: str):
    p = (FRONTEND / rel).resolve()
    try:
        p.relative_to(FRONTEND.resolve())
    except ValueError:
        raise HTTPException(404) from None  # path escapes the frontend folder
    if p.is_file():
        return FileResponse(p)
    html = p.with_name(p.name + ".html")  # clean URLs (/roads/live -> live.html), same as the Vercel site
    if p.name and html.is_file():
        return FileResponse(html)
    raise HTTPException(404, f"{rel} not found (frontend not built yet?)")


# ---------------------------------------------------------------- web pages
# Every page lives under /roads/ so the app can sit behind the Nagrik website on one address
# (Nagrik's server forwards /roads, /api, /media and /eval here). Old top-level URLs redirect.
ROADS = "/roads"
_PAGES = {"": "home.html", "dashboard": "index.html", "app": "app.html", "capture": "capture.html"}


@app.get("/", include_in_schema=False)
def root_page():
    return RedirectResponse(ROADS + "/")


@app.get(ROADS, include_in_schema=False)
def roads_root():
    return RedirectResponse(ROADS + "/")


@app.get(ROADS + "/{path:path}", include_in_schema=False)
def roads_page(path: str):
    path = path.strip("/")
    return _frontend_file(_PAGES.get(path, path))


@app.get("/{path:path}", include_in_schema=False)
def legacy_page(path: str):
    # pre-/roads links (bookmarks, an installed phone app): send them to the same page under /roads/
    if path.startswith("api/"):
        raise HTTPException(404, "unknown API endpoint")
    return RedirectResponse(f"{ROADS}/{path}")
