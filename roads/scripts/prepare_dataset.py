"""Build one YOLO dataset from:
  - RDD2022 United States + India (Pascal VOC XML -> YOLO txt)
  - Kaggle "Potholes-Detection-YOLOv8" (already YOLO; single class -> pothole)

Same 4 classes as Pothole Pilot (YOLO26n):
  0 longitudinal_crack (D00) | 1 transverse_crack (D10) | 2 alligator_crack (D20) | 3 pothole (D40)

Usage:  python scripts/prepare_dataset.py --raw <zips dir> --out <dataset dir>
"""
import argparse
import random
import shutil
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path

NAMES = ["longitudinal_crack", "transverse_crack", "alligator_crack", "pothole"]
RDD_MAP = {"D00": 0, "D10": 1, "D20": 2, "D40": 3}
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp"}
VAL_FRAC = 0.1
random.seed(42)


def unzip(zp: Path, dest: Path):
    marker = dest / f".unzipped_{zp.stem}"
    if marker.exists():
        return
    print(f"unzipping {zp.name} ...")
    with zipfile.ZipFile(zp) as z:
        z.extractall(dest)
    marker.touch()


def voc_to_yolo(xml_path: Path):
    root = ET.parse(xml_path).getroot()
    size = root.find("size")
    w, h = float(size.findtext("width")), float(size.findtext("height"))
    lines = []
    for obj in root.iter("object"):
        cls = RDD_MAP.get((obj.findtext("name") or "").strip())
        if cls is None:
            continue  # D43, D44, Repair, etc. are not in our 4 classes
        b = obj.find("bndbox")
        x1, y1, x2, y2 = (float(b.findtext(k)) for k in ("xmin", "ymin", "xmax", "ymax"))
        x1, x2 = max(0, min(x1, x2)), min(w, max(x1, x2))
        y1, y2 = max(0, min(y1, y2)), min(h, max(y1, y2))
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        lines.append(f"{cls} {(x1 + x2) / 2 / w:.6f} {(y1 + y2) / 2 / h:.6f} {(x2 - x1) / w:.6f} {(y2 - y1) / h:.6f}")
    return lines


def put(img: Path, lines, split: str, out: Path, prefix: str, stats: Counter):
    name = f"{prefix}_{img.stem}"
    dst_img = out / "images" / split / f"{name}{img.suffix.lower()}"
    dst_lbl = out / "labels" / split / f"{name}.txt"
    shutil.copy2(img, dst_img)
    dst_lbl.write_text("\n".join(lines) + ("\n" if lines else ""))
    for ln in lines:
        stats[(prefix, split, NAMES[int(ln.split()[0])])] += 1
    stats[(prefix, split, "_images")] += 1
    return dst_img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--with-czech", action="store_true", help="also add RDD2022 Czech (2,829 images)")
    a = ap.parse_args()
    raw, out = Path(a.raw), Path(a.out)
    work = out.parent / "extracted"
    work.mkdir(parents=True, exist_ok=True)
    for s in ("train", "val"):
        (out / "images" / s).mkdir(parents=True, exist_ok=True)
        (out / "labels" / s).mkdir(parents=True, exist_ok=True)
    stats = Counter()
    val_by_source = {}

    # --- RDD2022 countries (Pascal VOC, 4 damage classes) -----------------
    # (zip, prefix, folder name of the country inside the zip)
    rdd = [("RDD2022_India_kaggle.zip", "in", "India"),
           ("RDD2022_US_CZ_kaggle.zip", "us", "United_States")]
    if a.with_czech:
        rdd.append(("RDD2022_US_CZ_kaggle.zip", "cz", "Czech"))
    for zname, prefix, country in rdd:
        zp = raw / zname
        if not zp.exists():
            print(f"missing {zname}, skipping {country}")
            continue
        unzip(zp, work)
        xml_dirs = [d for d in work.rglob("xmls") if d.parent.parent.name == "train" and d.parent.parent.parent.name == country]
        xmls = sorted(x for d in xml_dirs for x in d.glob("*.xml"))
        random.shuffle(xmls)
        n_val = int(len(xmls) * VAL_FRAC)
        val_by_source[prefix] = []
        for i, x in enumerate(xmls):
            img_dir = x.parent.parent.parent / "images"  # <country>/train/images
            img = next((img_dir / f"{x.stem}{e}" for e in (".jpg", ".png", ".jpeg") if (img_dir / f"{x.stem}{e}").exists()), None)
            if img is None:
                continue
            split = "val" if i < n_val else "train"
            p = put(img, voc_to_yolo(x), split, out, prefix, stats)
            if split == "val":
                val_by_source[prefix].append(p)
        print(f"{country}: {len(xmls)} annotated images")

    # --- YOLO-format pothole-only sets (class 0 = pothole -> our class 3) --
    for zname, prefix in (("kaggle_potholes_yolov8.zip", "kg"), ("bharatpothole.zip", "bp")):
        zp = raw / zname
        if not (zp.exists() and zipfile.is_zipfile(zp)):
            print(f"missing {zname}, skipping")
            continue
        kdir = work / prefix
        unzip(zp, kdir)
        val_by_source[prefix] = []
        for img in sorted(kdir.rglob("*")):
            if img.suffix.lower() not in IMG_EXT or img.parent.name != "images":
                continue
            lbl = img.parent.parent / "labels" / f"{img.stem}.txt"
            lines = []
            if lbl.exists():
                for ln in lbl.read_text().split("\n"):
                    parts = ln.split()
                    if len(parts) == 5:  # boxes only (skip polygons)
                        lines.append("3 " + " ".join(parts[1:]))
            split = "val" if img.parent.parent.name.lower() in ("valid", "val", "test") else "train"
            p = put(img, lines, split, out, prefix, stats)
            if split == "val":
                val_by_source[prefix].append(p)

    # --- per-source validation lists (to report India accuracy separately) -
    for src, paths in val_by_source.items():
        (out / f"val_{src}.txt").write_text("\n".join(str(p) for p in paths))

    (out / "data.yaml").write_text(
        f"path: {out.as_posix()}\ntrain: images/train\nval: images/val\n"
        f"names:\n" + "".join(f"  {i}: {n}\n" for i, n in enumerate(NAMES))
    )
    print("\nsource split  " + "  ".join(f"{n[:12]:>12}" for n in ["_images"] + NAMES))
    for src in sorted({k[0] for k in stats}):
        for split in ("train", "val"):
            print(f"{src:6} {split:5} " + "  ".join(f"{stats[(src, split, n)]:>12}" for n in ["_images"] + NAMES))
    print(f"\ndata.yaml -> {out / 'data.yaml'}")


if __name__ == "__main__":
    main()
