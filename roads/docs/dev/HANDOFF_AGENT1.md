# Nagrik Roads – Agent 1 handoff (core pipeline + API)

Status as of 2026-11-13 14:30. Written so work can continue from another account/session.
Read `../ARCHITECTURE.md` first (the binding contract), then this file.

## State
- Agent 1 work is **DONE**. Test passes, server verified on port 8000, nothing is running.
- **Training STOPPED early**: `C:\Users\ayman\roadsense-data\runs\rdd_us_in_kaggle_y26n\` reached epoch 9 of 60
  (mAP50 0.45, still rising). No process running, GPU idle. `backend\models\roadsense_y26n.pt` was NOT created
  (`scripts/train.py` copies it only after training finishes). `weights\best.pt` and `last.pt` exist.
  Options: (1) copy `best.pt` -> `backend\models\roadsense_y26n.pt` after checking it with
  `scripts/compare_models.py` against `rdd_yolov8s.pt` (F1 0.66 on RDD India); (2) resume from `last.pt`
  (`resume=True`) for a few epochs (each takes ~7-15 min); (3) keep `rdd_yolov8s.pt` (the current default).
- User decision: **Nagrik enrichment OFF for now** (build the core first). Turn on with env `ROADSENSE_NAGRIK=1`.

## Run
- Server: `.venv\Scripts\python.exe -m uvicorn backend.app.main:app --port 8000`, then open http://localhost:8000
- Test (pytest is NOT installed, don't pip install): `.venv\Scripts\python.exe backend\tests\test_pipeline.py`
  -> "all pipeline tests passed" (~80 s, CPU, isolated temp data dir).

## Files owned by Agent 1 (all done)
`backend/app/main.py, detector.py, severity.py, geo.py, pipeline.py, db.py`, `backend/__init__.py`,
`backend/app/__init__.py`, `backend/tests/test_pipeline.py`.

## Env vars
| var | default | meaning |
|---|---|---|
| ROADSENSE_MODEL | – | force a model path |
| ROADSENSE_DEVICE | cpu | never use the GPU while training |
| ROADSENSE_DEPTH | 0 | 1 = Depth-Anything depth index (heavy, CPU) |
| ROADSENSE_NAGRIK | 0 | 1 = ward/officials/tenders enrichment |
| ROADSENSE_CONF | 0.20 | detection confidence threshold (tuned for roadsense_y26s) |
| ROADSENSE_DATA_DIR | backend/data | DB + media + uploads (tests use a temp dir) |

## Key behaviour
- Model order: env -> `roadsense_y26n.pt` -> `rdd_yolov8s.pt` -> Pothole Pilot ONNX (Agent 6 found the
  Pothole Pilot model scores ~0 on Indian data, so it is last; this differs from ARCHITECTURE.md).
- Video: ByteTrack with a fresh model per video (IDs never carry over), a track needs >=3 frames,
  counting line, best frame per track. Frames are read until the end (phone WebM frame counts are wrong).
  Annotated video is VP8 `.webm` (H.264 isn't available in this OpenCV build).
- Media: `backend/data/media/<inspection_id>/` (frame max 1280 px + crop with 20% margin, JPEG).
  DB stores `/media/...` URLs. Uploads go to `backend/data/uploads/<id>/input.<ext>` (+ `gps_track.<ext>`).
- Location: GPS track interpolation (video), EXIF (image), form lat/lon, or None.
- Pipeline order: detect -> severity -> media -> dedup.match_defect (exclude_ids for photos) ->
  create/update defect + observation -> save_fingerprint -> update_progress (trend) -> risk + forecast
  (+ nagrik if enabled) -> mark_absent along the track. Codes PH-0001 / CR-0001. UTC ISO timestamps.
- Other agents' modules are imported defensively; if one is missing or raises, a fallback is used
  (no dedup -> always a new defect, no risk -> risk from severity only).
- Endpoints: everything in the contract, plus `/api/jobs`, `/api/health`, `/eval/*`, and
  `/api/defects?workflow=&damage_class=crack`.

## Open items / asks for other agents
- Agent 3 (`risk.py`): `road_context` took about 22 s and returned defaults (Overpass probably unreachable).
  Nothing remembers that a location failed, so every inspection pays the timeout again. The pipeline now reuses
  one result per location within an inspection; a short-lived "failed" cache in risk.py would help more.
- Docs: add the model order and the ROADSENSE_NAGRIK / ROADSENSE_DATA_DIR variables to ARCHITECTURE.md / README.
- UI idea (Agent 5's file): put GPS track + Notes under an "Advanced" toggle on the Inspect page.
