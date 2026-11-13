# %% [markdown]
# # Nagrik Roads: train YOLO26s on Kaggle (free GPU)
#
# Same data, same 4 classes and the **same validation split** as the local YOLO26n run
# (`scripts/prepare_dataset.py`, seed 42), so the two models can be compared fairly.
#
# **Before running**
# 1. *Add Input* → your private dataset made from `roadsense_kaggle.zip` (`scripts/pack_for_kaggle.py`).
# 2. *Settings* → Accelerator **GPU T4 x2** (or P100) · Internet **On** (to install Ultralytics and download `yolo26s.pt`).
# 3. Use **Save Version → Save & Run All (Commit)** so it keeps running with the browser closed.
#    Training stops itself after 10.5 h (Kaggle's limit is 12 h).
# 4. When finished: *Output* tab → download `roadsense_y26s_results.zip`.

# %%
# 1) Setup: same Ultralytics version as the laptop
import subprocess, sys
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "ultralytics==8.4.211"], check=True)

import glob, json, shutil, time
from pathlib import Path
import torch
from ultralytics import YOLO

NGPU = torch.cuda.device_count()
print("GPUs:", NGPU, [torch.cuda.get_device_name(i) for i in range(NGPU)])
assert NGPU > 0, "No GPU: Settings -> Accelerator -> GPU"

# %%
# 2) Find the uploaded dataset and write a data.yaml + per-source val lists with Kaggle paths
def _find(root):
    return sorted({Path(p).parent for p in glob.glob(f"{root}/**/data.yaml", recursive=True)
                   if (Path(p).parent / "images" / "train").is_dir()})


cands = _find("/kaggle/input")
if not cands:  # uploaded via API as a .zip that Kaggle did not unpack: unpack it to the working disk
    import zipfile
    zips = glob.glob("/kaggle/input/**/*.zip", recursive=True)
    assert zips, "Dataset not found: Add Input -> your roadsense dataset"
    print("unzipping", zips[0])
    zipfile.ZipFile(zips[0]).extractall("/kaggle/tmp_ds")
    cands = _find("/kaggle/tmp_ds")
assert cands, "Dataset not found: Add Input -> your roadsense dataset"
DS = cands[0]
WORK = Path("/kaggle/working")
NAMES = ["longitudinal_crack", "transverse_crack", "alligator_crack", "pothole"]

(WORK / "data.yaml").write_text(
    f"path: {DS.as_posix()}\ntrain: images/train\nval: images/val\nnames:\n"
    + "".join(f"  {i}: {n}\n" for i, n in enumerate(NAMES)))

VAL_LISTS = {}
for src in ("in", "bp", "us", "kg"):
    f = DS / f"val_{src}.txt"
    if f.exists():
        rel = [l.strip() for l in f.read_text().splitlines() if l.strip()]
        out = WORK / f"val_{src}.txt"
        out.write_text("\n".join(str(DS / r) for r in rel) + "\n")
        VAL_LISTS[src] = out
print("dataset:", DS)
print({k: len(v.read_text().split()) for k, v in VAL_LISTS.items()}, "val images per source")
print(len(list((DS / "images" / "train").iterdir())), "train images")

# %%
# 3) Train YOLO26s (bigger than the laptop's YOLO26n; 4 GB VRAM can't train it well)
RUN = "roadsense_y26s"
args = dict(data=str(WORK / "data.yaml"), epochs=60, time=10.5, imgsz=640,
            batch=32 if NGPU > 1 else 16, workers=4, patience=15, cos_lr=True, close_mosaic=10,
            project=str(WORK / "runs"), name=RUN, exist_ok=True, seed=42, plots=True)
t0 = time.time()
try:
    YOLO("yolo26s.pt").train(device=list(range(NGPU)) if NGPU > 1 else 0, **args)
except Exception as e:  # multi-GPU (DDP) can fail in some notebook setups: fall back to one GPU
    if NGPU < 2:
        raise
    print("multi-GPU failed, retrying on one GPU:", e)
    args["batch"] = 16
    YOLO("yolo26s.pt").train(device=0, **args)
print(f"training took {(time.time() - t0) / 3600:.1f} h")

# %%
# 4) Evaluate exactly like scripts/train.py: all val + each source (India first)
BEST = WORK / "runs" / RUN / "weights" / "best.pt"
model = YOLO(str(BEST))


def f1(p, r):
    return 2 * p * r / (p + r) if p + r else 0.0


def evaluate(name, val_list=None):
    data = WORK / "data.yaml"
    if val_list:
        data = WORK / f"data_{name}.yaml"
        data.write_text((WORK / "data.yaml").read_text().replace("val: images/val", f"val: {val_list}"))
    m = model.val(data=str(data), imgsz=640, batch=16, device=0, plots=True, verbose=False,
                  project=str(WORK / "runs"), name=f"val_{name}", exist_ok=True)
    p, r = float(m.box.mp), float(m.box.mr)
    res = {"set": name, "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1(p, r), 4),
           "mAP50": round(float(m.box.map50), 4), "mAP50_95": round(float(m.box.map), 4),
           "per_class_mAP50": {model.names[int(c)]: round(float(v), 4)
                               for c, v in zip(m.box.ap_class_index, m.box.ap50)}}
    print(json.dumps(res, indent=2))
    return res


results = [evaluate("all")]
for src, label in (("in", "rdd_india"), ("bp", "bharatpothole"), ("us", "rdd_usa"), ("kg", "kaggle_potholes")):
    if src in VAL_LISTS:
        results.append(evaluate(label, VAL_LISTS[src]))

# %%
# 5) Export + package everything for download (Output tab)
OUT = WORK / "export"
OUT.mkdir(exist_ok=True)
shutil.copy2(BEST, OUT / "roadsense_y26s.pt")
onnx = model.export(format="onnx", imgsz=640, simplify=True)
shutil.copy2(onnx, OUT / "roadsense_y26s.onnx")
(OUT / "roadsense_y26s_metrics.json").write_text(json.dumps(results, indent=2))
shutil.copy2(WORK / "runs" / RUN / "results.csv", OUT / "results.csv")
for png in ("results.png", "confusion_matrix.png", "BoxPR_curve.png"):
    p = WORK / "runs" / RUN / png
    if p.exists():
        shutil.copy2(p, OUT / png)
shutil.make_archive(str(WORK / "roadsense_y26s_results"), "zip", OUT)
print("Download: Output tab -> roadsense_y26s_results.zip")
print("Then copy roadsense_y26s.pt / .onnx / _metrics.json into backend/models/ on the laptop.")
