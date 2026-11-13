"""Zip the prepared Nagrik Roads dataset for upload as a private Kaggle dataset.

Keeps the exact train/val split built by prepare_dataset.py (same India validation
images), rewrites the per-source val lists to relative paths, and skips Ultralytics
*.cache files. Images are stored without recompression (fast, same size).

Usage:  python scripts/pack_for_kaggle.py [--src ~/roadsense-data/roadsense] [--out ~/roadsense-data/roadsense_kaggle.zip]
"""
import argparse
import zipfile
from pathlib import Path


def main():
    home = Path.home() / "roadsense-data"
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(home / "roadsense"))
    ap.add_argument("--out", default=str(home / "roadsense_kaggle.zip"))
    a = ap.parse_args()
    src, out = Path(a.src), Path(a.out)
    if not (src / "data.yaml").exists():
        raise SystemExit(f"{src} has no data.yaml - run prepare_dataset.py first")

    n = 0
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as z:
        for sub in ("images", "labels"):
            for p in sorted((src / sub).rglob("*")):
                if p.is_file() and p.suffix != ".cache":
                    z.write(p, f"roadsense/{p.relative_to(src).as_posix()}")
                    n += 1
        for lst in sorted(src.glob("val_*.txt")):
            rel = []
            for line in lst.read_text().splitlines():
                line = line.strip().replace("\\", "/")
                if line:
                    i = line.find("images/")
                    rel.append(line[i:] if i >= 0 else line)
            z.writestr(f"roadsense/{lst.name}", "\n".join(rel) + "\n")
        z.writestr("roadsense/data.yaml", (src / "data.yaml").read_text())
    print(f"{n} files -> {out} ({out.stat().st_size / 1e9:.2f} GB)")


if __name__ == "__main__":
    main()
