import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .config import IS_WINDOWS


def save(root, wav_path, meta):
    """★を付けた声を outputs/ に WAV と JSON で残す。ファイル名は YYYYMMDD_HHMMSS_<seed>。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    base = f"{datetime.now():%Y%m%d_%H%M%S}_{meta['seed']}"
    stem, n = base, 1
    while (root / f"{stem}.wav").exists() or (root / f"{stem}.json").exists():
        n += 1
        stem = f"{base}_{n}"
    try:
        shutil.copyfile(wav_path, root / f"{stem}.wav")
        (root / f"{stem}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        remove(root, stem)   # 片方だけ残さない
        raise
    return stem


def set_favorite(root, stem, value):
    path = Path(root) / f"{stem}.json"
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    meta["favorite"] = bool(value)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def remove(root, stem):
    for ext in (".wav", ".json"):
        try:
            os.remove(Path(root) / f"{stem}{ext}")
        except OSError:
            pass


def open_folder(path):
    Path(path).mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
