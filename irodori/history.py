"""これまでの会話。1 つの会話を history/<id>.json(文章)と <id>.wav(その会話の声)に残し、一覧から開き直せるようにする。"""
import json
import os
import shutil
from pathlib import Path

from .config import ROOT

DIR = ROOT / "history"


def _path(hid, ext=".json"):
    # 一覧から届く id は、ファイル名に使える文字だけ(ほかのフォルダーを触らない)
    if not hid or not all(ch.isalnum() or ch == "-" for ch in hid):
        raise ValueError(hid)
    return DIR / f"{hid}{ext}"


def save(data):
    DIR.mkdir(parents=True, exist_ok=True)
    path = _path(data["id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def keep_voice(hid, wav):
    DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(wav, _path(hid, ".wav"))


def voice_file(hid):
    path = _path(hid, ".wav")
    return path if path.is_file() else None


def load(hid):
    return json.loads(_path(hid).read_text(encoding="utf-8"))


def remove(hid):
    for ext in (".json", ".wav"):
        _path(hid, ext).unlink(missing_ok=True)


def items():
    """新しい順の一覧。{id, title, updated, lines}。"""
    found = []
    for f in DIR.glob("*.json") if DIR.is_dir() else []:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        found.append({"id": data.get("id", f.stem), "title": data.get("title", ""), "updated": data.get("updated", 0),
                      "lines": sum(1 for e in data.get("log", []) if e.get("kind") == "line")})
    return sorted(found, key=lambda x: x["updated"], reverse=True)
