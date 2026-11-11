"""audio.cpp で使える声のモデルの一覧。セットアップと設定の画面は、ここから選ばせる。
どれも Hugging Face の audio-cpp/audio.cpp-gguf にある 1 ファイルの GGUF で、Kataribe のフォルダーの models/ に置く。
ここに無い GGUF を設定で指定した時は、Irodori-TTS として読む。"""
from pathlib import Path

from . import i18n
from .config import ROOT

REPO = "audio-cpp/audio.cpp-gguf"


def _voices(names, language="", gender=""):
    return [{"name": n, "language": language, "gender": gender, "description": ""} for n in names]


# voices: モデルに入っている声(選んで使う)。design: 声を文章で決められる。clone: WAV を参照の声にできる(保存した声を使える)
CATALOG = [
    {"id": "irodori-mf", "name": "Irodori-TTS v4.1", "dir": "Irodori-TTS-v4-Small-GGUF", "file": "irodori-tts-v4.1-small-mf-q8_0.gguf",
     "family": "irodori_tts", "gb": 1.4, "languages": ["ja"], "voices": [], "design": True, "clone": True, "steps": True},
    {"id": "irodori-anime", "name": "Irodori-TTS v4.1 Anime", "dir": "Irodori-TTS-v4-Small-GGUF", "file": "irodori-tts-v4.1-anime-q8_0.gguf",
     "family": "irodori_tts", "gb": 1.1, "languages": ["ja"], "voices": [], "design": True, "clone": True, "steps": False},
    {"id": "supertonic-3", "name": "Supertonic 3", "dir": "Supertonic-3-GGUF", "file": "supertonic-3-q8_0.gguf",
     "family": "supertonic", "gb": 0.5, "design": False, "clone": False, "steps": False,
     "languages": ["en", "ko", "ja", "ar", "bg", "cs", "da", "de", "el", "es", "et", "fi", "fr", "hi", "hr", "hu", "id", "it", "lt", "lv", "nl",
                   "pl", "pt", "ro", "ru", "sk", "sl", "sv", "tr", "uk", "vi"],
     "voices": _voices(["F1", "F2", "F3", "F4", "F5"], gender="female") + _voices(["M1", "M2", "M3", "M4", "M5"], gender="male")},
    {"id": "qwen3-tts", "name": "Qwen3-TTS 1.7B", "dir": "Qwen3-TTS-12Hz-1.7B-CustomVoice-GGUF", "file": "qwen3-tts-12hz-1.7b-customvoice-q8_0.gguf",
     "family": "qwen3_tts", "gb": 2.8, "design": False, "clone": False, "steps": False,
     "languages": ["zh", "en", "ja", "ko", "de", "fr", "ru", "pt", "es", "it"],
     "voices": _voices(["Vivian", "Serena"], "zh", "female") + _voices(["Uncle_Fu", "Dylan", "Eric"], "zh", "male")
     + _voices(["Ryan", "Aiden"], "en", "male") + _voices(["Ono_Anna"], "ja", "female") + _voices(["Sohee"], "ko", "female")},
]
BY_ID = {m["id"]: m for m in CATALOG}


def default_pick():
    """最初に選ぶモデル。日本語は Irodori-TTS、中国語は Qwen3-TTS、ほかの言語は Supertonic 3。"""
    lang = i18n.detect()
    return "irodori-mf" if lang == "ja" else "qwen3-tts" if lang.startswith("zh") else "supertonic-3"


def current(cfg):
    """いま使うモデル(一覧の 1 件)と、そのファイルの場所。設定にファイルの場所があればそれ、無ければ選んだモデルを models/ から。"""
    custom = Path(cfg["audio_model"]).expanduser() if cfg.get("audio_model") else None
    if custom:
        if not custom.is_absolute():
            custom = ROOT / custom
        entry = next((m for m in CATALOG if m["file"] == custom.name), None)
        # 一覧に無い GGUF は Irodori-TTS として読む(段数は、名前に -mf がある時だけ渡す)
        return entry or dict(BY_ID["irodori-anime"], id="", name=custom.stem, steps="-mf" in custom.stem.lower()), custom
    entry = BY_ID.get(cfg.get("audio_pick")) or BY_ID[default_pick()]
    return entry, ROOT / "models" / entry["file"]


def listing(cfg):
    """画面に出す一覧: 名前・話せる言語・大きさ・入っているか・選んでいるか。"""
    picked = (BY_ID.get(cfg.get("audio_pick")) or BY_ID[default_pick()])["id"]
    return [{"id": m["id"], "name": m["name"], "gb": m["gb"], "languages": m["languages"], "voices": len(m["voices"]), "design": m["design"],
             "installed": (ROOT / "models" / m["file"]).is_file(), "picked": m["id"] == picked} for m in CATALOG]


def url(entry):
    return f"https://huggingface.co/{REPO}/resolve/main/{entry['dir']}/{entry['file']}"


def info_url(entry):
    return f"https://huggingface.co/api/models/{REPO}/tree/main/{entry['dir']}"
