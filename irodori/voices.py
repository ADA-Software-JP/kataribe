import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from .i18n import t

GGUF = ".voice.gguf"
WAV = ".wav"


class VoiceError(Exception):
    pass


def safe_name(name):
    return re.sub(r"[^\w\-ぁ-んァ-ヶ一-龠ー]", "_", name).strip("_") or "voice"


def _stem(path):
    return path.name[: -len(GGUF)] if path.name.endswith(GGUF) else path.stem


def _files(folder):
    folder = Path(folder)
    if not folder.is_dir():
        return {}
    found = {f.stem: f for f in sorted(folder.glob(f"*{WAV}"))}
    found.update({_stem(f): f for f in sorted(folder.glob(f"*{GGUF}"))})
    return found


def unique_name(voices_dir, name):
    """まだ無い名前。Windows と macOS は大文字・小文字を区別しないので、区別せずに比べる(上書きしない)。"""
    base, n = safe_name(name), 1
    folder = Path(voices_dir)
    taken = {k.lower() for k in _files(folder)} | ({f.stem.lower() for f in folder.glob("*.json")} if folder.is_dir() else set())
    candidate = base
    while candidate.lower() in taken:
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def list_voices(voices_dir, loaded, default=""):
    return [{"name": name, "kind": "gguf" if f.name.endswith(GGUF) else "wav", "size": f.stat().st_size,
             "loaded": name in loaded, "default": name == default} for name, f in _files(voices_dir).items()]


def save_wav(voices_dir, wav, name, meta):
    """セッションの声(WAV)を voices/<名前>.wav と <名前>.json に残す。作った名前を返す。"""
    folder = Path(voices_dir)
    folder.mkdir(parents=True, exist_ok=True)
    final = unique_name(folder, name)
    try:
        shutil.copyfile(wav, folder / f"{final}{WAV}")
        (folder / f"{final}.json").write_text(
            json.dumps(dict(meta, name=final, created_at=datetime.now().isoformat(timespec="seconds")),
                       ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        for f in (folder / f"{final}{WAV}", folder / f"{final}.json"):
            f.unlink(missing_ok=True)   # 片方だけ残さない
        raise VoiceError(t("voices.save_failed", e=e))
    return final


def import_path(source, voices_dir):
    """声ファイル(*.voice.gguf / *.wav)1 つ、またはフォルダーの中の全部を voices/ にコピーする。同名は上書きしない。"""
    src = Path(source).expanduser()
    wanted = lambda f: f.is_file() and (f.name.endswith(GGUF) or f.suffix.lower() == WAV)
    files = [src] if wanted(src) else sorted(f for f in src.iterdir() if wanted(f)) if src.is_dir() else None
    if files is None:
        raise ValueError(t("voices.not_found", gguf=GGUF, wav=WAV, path=source))
    dest = Path(voices_dir)
    dest.mkdir(parents=True, exist_ok=True)
    taken = {k.lower() for k in _files(dest)}
    copied, skipped = [], []
    for f in files:
        name = safe_name(_stem(f))
        target = dest / f"{name}{GGUF if f.name.endswith(GGUF) else WAV}"
        if name.lower() in taken or target.exists():
            skipped.append(name)
            continue
        shutil.copy2(f, target)
        taken.add(name.lower())
        copied.append(name)
    return {"copied": copied, "skipped": skipped}


def delete(voices_dir, name):
    path = _files(voices_dir).get(safe_name(name))
    if not path:
        return False
    path.unlink()
    path.with_name(f"{safe_name(name)}.json").unlink(missing_ok=True)
    return True
