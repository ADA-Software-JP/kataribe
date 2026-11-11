import copy
import json
import os
import shutil
import sys
import threading
from pathlib import Path

from . import i18n, tuning

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
FROZEN = getattr(sys, "frozen", False)
# BUNDLE は同梱物(static/)の場所、ROOT は設定・声・出力を置く場所。
# Windows の exe では ROOT を exe の隣にする。Mac の .app は中に書かず、ユーザーの Application Support に置く。
BUNDLE = Path(getattr(sys, "_MEIPASS", "")) if FROZEN else Path(__file__).resolve().parent.parent
# Microsoft Store 版(別のビルド、Kataribe-store.spec)は、声を作る speech.cpp と audio.cpp を同梱する(あとから実行ファイルを落とさない)。
# Store のアプリは自分のフォルダーに書けないので、設定・声・出力はユーザーの AppData に置く。
BUNDLED_SPEECH = BUNDLE / "engine" / "speech" / "speech.exe"
BUNDLED_AUDIO = BUNDLE / "engine" / "audio" / "audiocpp_server.exe"
STORE = FROZEN and IS_WINDOWS and BUNDLED_SPEECH.is_file()
if FROZEN and IS_MAC:
    ROOT = Path.home() / "Library" / "Application Support" / "Kataribe"
    ROOT.mkdir(parents=True, exist_ok=True)
elif STORE:
    ROOT = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Kataribe"
    ROOT.mkdir(parents=True, exist_ok=True)
else:
    ROOT = Path(sys.executable).resolve().parent if FROZEN else BUNDLE
CONFIG_PATH = ROOT / "config.json"

# Finder から開いた .app は PATH が最小限なので、speech・claude・codex がよく入る場所を足す。
if IS_MAC:
    _have = os.environ.get("PATH", "").split(os.pathsep)
    _more = [str(Path.home() / ".local" / "bin"), "/opt/homebrew/bin", "/usr/local/bin"]
    os.environ["PATH"] = os.pathsep.join(_have + [d for d in _more if d not in _have])

# 既定の LLM モデル。別名("haiku" など)ではなく ID を明示する。Codex は速さ重視で gpt-6-luna、思考の深さは low。
DEFAULT_MODELS = {"claude": "claude-haiku-5-5", "codex": "gpt-6-luna"}
DEFAULT_EFFORT = "low"
EFFORTS = ("low", "medium", "high")
# 設定画面の候補(自由入力もできる)
MODEL_CHOICES = {
    "claude": ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"],
    "codex": ["gpt-6-luna", "gpt-6.1-sol", "gpt-6-astra", "gpt-6.1-terra"],
}

# ショートカットの既定(操作 -> キー。"" は割り当てなし)。static/js/shortcuts.js の ACTIONS と同じ
SHORTCUTS = {"send": "Enter", "newline": "Shift+Enter", "pause": "Esc", "toggle": "Ctrl+Space", "help": "?", "restart": "", "voice": "",
             "settings": "Ctrl+,", "mic_start": "Alt+M", "mic_commit": "Ctrl+Enter", "mic_cancel": "Esc"}

# LLM に渡す文面の項目(既定の文は prompts.py)。空なら既定を使う。
PROMPT_KEYS = ("style", "format", "ready", "request", "steer", "handover", "search", "no_search", "more", "retry", "ping", "ping_system")


def official_speech():
    """公式のインストーラーが置く speech の場所。無ければ空。"""
    if STORE:
        return str(BUNDLED_SPEECH)
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA")
        path = Path(base) / "Programs" / "speech.cpp" / "current" / "speech.exe" if base else None
    else:
        path = Path.home() / ".local" / "bin" / "speech"
    return str(path) if path and path.is_file() else ""


def _find_speech():
    """speech.cpp の実行ファイル。PATH にあればそれ、なければ公式のインストーラーが置く場所。見つからなければ空。"""
    if STORE:
        return str(BUNDLED_SPEECH)
    found = shutil.which("speech")
    if found:
        return found
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA")
        path = Path(base) / "Programs" / "speech.cpp" / "current" / "speech.exe" if base else None
    else:
        path = Path.home() / ".local" / "bin" / "speech"
    return str(path) if path and path.is_file() else ""


def find_audio():
    """audio.cpp のサーバー(audiocpp_server)。Store 版は同梱のもの。ほかは PATH にあればそれ。見つからなければ空。"""
    if STORE:
        return str(BUNDLED_AUDIO) if BUNDLED_AUDIO.is_file() else ""
    return shutil.which("audiocpp_server") or ""


def default_model():
    """最初に選ぶ声のモデル。日本語の環境は Irodori-TTS(日本語専用)、ほかの言語は Qwen3-TTS(英語・中国語など 10 言語)。"""
    return "irodori-tts-mf" if i18n.detect() == "ja" else "qwen3-tts-0.6b"


def _defaults():
    return {
        "tts_backend": "audio",   # 声を作るエンジン: audio(audio.cpp、新しく入れた時の既定)か speech(speech.cpp、以前から使っている人はこちらのまま)
        "audio_pick": "",            # audio.cpp で使うモデル(audio_models.py の一覧の id)。空なら言語に合わせて選ぶ
        "audio_exe": find_audio(),   # audio.cpp の audiocpp_server
        "audio_model": "",           # 一覧に無い GGUF を自分で指定する時の場所(空なら audio_pick のモデルを models/ から読む)
        "speech_exe": _find_speech(),
        "model_dir": "",   # 空なら speech.cpp の既定の保存先
        "model": default_model(),
        # 相対パスは ROOT から見る(フォルダーごと移動しても、隣の outputs/ と voices/ を指したままにする)
        "output_dir": "outputs",
        "voices_dir": "voices",
        "device": "auto",
        "language": i18n.AUTO,   # 画面とメッセージの言語(auto = OS に合わせる / en / ja / zh-Hans / zh-Hant)
        "language_chosen": False,   # ユーザーが設定画面で言語を選んだら True(印の無い "en" は、以前の版の既定なので auto に読み替える)
        "keep_all": False,
        "asr_check": False,
        "default_voice": "",
        "default_how": "",
        "speak_language": "auto",   # 読み上げる言語(auto = モデルが話せる言語のうち、画面の言語。合わなければ日本語)
        "tts_voice": "",            # 内蔵の声を持つモデル(Qwen3-TTS など)で使う声。空ならその言語の最初の声   # 依頼で声を指定しなかった時の声(文章で)
        "prompts": {key: "" for key in PROMPT_KEYS},
        "web_search": True,   # Claude Code と Codex に Web 検索を許す(LM Studio は検索できない)
        "shortcuts": dict(SHORTCUTS),   # キーボードショートカット
        "speech_speed": 1.15,   # 話す速さの倍率。設定で変えられる
        "tuning": tuning.defaults(),   # 詳しい設定(tuning.py)
        "llm_provider": "claude",
        "llm": {
            "claude": {"model": DEFAULT_MODELS["claude"]},
            "codex": {"model": DEFAULT_MODELS["codex"], "effort": DEFAULT_EFFORT},
            "lmstudio": {"base_url": "http://localhost:1234/v1", "model": ""},
        },
    }


def _merge(base, extra):
    for key, value in extra.items():
        if isinstance(base.get(key), dict):
            if isinstance(value, dict):   # 入れ物(辞書)を別の型で置き換えない(壊れた設定で落ちないように)
                _merge(base[key], value)
        elif key in base:
            base[key] = value
    return base


def _fix_llm(data, legacy=False):
    """モデル欄の空と、以前の既定("haiku")を今の既定に直す。ユーザーが入れた別の ID はそのまま。直したら True。
    legacy は読み込み時だけ: 以前の版が既定として書いた "haiku" も移す(画面から "haiku" と入れた時は尊重する)。"""
    changed = False
    llm = data["llm"]
    for provider in ("claude", "codex"):
        cfg = llm[provider]
        model = cfg.get("model")
        model = model.strip() if isinstance(model, str) else ""
        if not model or (legacy and provider == "claude" and model.lower() == "haiku"):
            model = DEFAULT_MODELS[provider]
        changed |= cfg.get("model") != model
        cfg["model"] = model
    effort = llm["codex"].get("effort")
    if effort not in EFFORTS:
        llm["codex"]["effort"] = DEFAULT_EFFORT
        changed = True
    return changed


def _fix_tuning(data):
    """詳しい設定の、正しくない値と足りない値を既定に直す。直したら True。"""
    if not isinstance(data.get("tuning"), dict):
        data["tuning"] = tuning.defaults()
        return True
    changed = tuning.repair(data["tuning"])
    # 響きの上限の以前の既定(-30 dB)は甘く、響いた声が通っていた。そのままの人は新しい既定に移す
    if data["tuning"].get("reverb_db") == -30.0:
        data["tuning"]["reverb_db"] = tuning.defaults()["reverb_db"]
        changed = True
    return changed


def _fix_web_search(data):
    """web_search が真偽値でなければ既定(オン)にする。直したら True。"""
    if isinstance(data.get("web_search"), bool):
        return False
    data["web_search"] = True
    return True


def _fix_shortcuts(data):
    """shortcuts の、文字列でない値と足りない操作を既定に直す(知らない操作は _merge が捨てる)。直したら True。"""
    cur = data.get("shortcuts")
    if not isinstance(cur, dict):
        data["shortcuts"] = dict(SHORTCUTS)
        return True
    changed = False
    for action, key in SHORTCUTS.items():
        if not isinstance(cur.get(action), str):
            cur[action] = key
            changed = True
    return changed


def _fix_language(data, saved=None):
    """language が auto と言語のどれでもなければ auto にする。直したら True。
    saved(読み込んだ config)を渡した時だけ、印(language_chosen)が無い "en"(以前の版の既定)を auto に移し、
    印が無くても en 以外の言語が入っていればユーザーが選んだ物として印を付ける。"""
    changed = False
    if data.get("language") not in (i18n.AUTO, *i18n.LANGS):
        data["language"] = i18n.AUTO
        changed = True
    if not isinstance(data.get("language_chosen"), bool):
        data["language_chosen"] = False
        changed = True
    if saved is not None and "language_chosen" not in saved:
        if data["language"] == "en":
            data["language"] = i18n.AUTO
            changed = True
        elif data["language"] != i18n.AUTO:
            data["language_chosen"] = True
            changed = True
    return changed


def _fix_dirs(data):
    """以前の版が書いた ROOT の下の絶対パス(既定の outputs / voices)を相対パスに直す。直したら True。"""
    changed = False
    for key, name in (("output_dir", "outputs"), ("voices_dir", "voices")):
        if data.get(key) == str(ROOT / name):
            data[key] = name
            changed = True
    return changed


def find_speech():
    return _find_speech()


class Config:
    def __init__(self):
        self._lock = threading.Lock()
        self._data = _defaults()
        if CONFIG_PATH.exists():
            try:
                saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                _merge(self._data, saved)
                # 声を作るエンジンの項目が無い設定は、speech.cpp だけだった頃のもの。そのまま speech.cpp で使い続ける
                legacy_engine = "tts_backend" not in saved
                if legacy_engine:
                    self._data["tts_backend"] = "speech"
                # 以前の style_prompt は prompts.style に移す
                if isinstance(saved.get("style_prompt"), str) and not self._data["prompts"]["style"]:
                    self._data["prompts"]["style"] = saved["style_prompt"]
                if _fix_llm(self._data, legacy=True) | _fix_tuning(self._data) | _fix_language(self._data, saved) | _fix_web_search(self._data) | _fix_shortcuts(self._data) | _fix_dirs(self._data) | legacy_engine:
                    self._write()
            except (OSError, ValueError, AttributeError):
                pass
        else:
            try:
                self._write()
            except OSError as e:
                # 書き込めない場所に置かれても起動はする(設定はこの起動の間だけ持つ)
                print(f"Kataribe: could not write {CONFIG_PATH}: {e}", file=sys.stderr, flush=True)
        i18n.set_lang(self._data["language"])

    def _write(self, data=None):
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data if data is None else data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)

    def get(self):
        with self._lock:
            data = copy.deepcopy(self._data)
        if STORE:
            # Store 版は、いつも同梱の実行ファイルを使う(更新のたびに場所が変わるので、保存した値は使わない)
            data["speech_exe"], data["audio_exe"] = find_speech(), find_audio()
        return data

    def update(self, patch):
        # 写しに当てて、書き込めてから入れ替える(途中で失敗したら、動いている設定は前のまま)
        with self._lock:
            data = _merge(copy.deepcopy(self._data), patch)
            if "language" in patch:
                data["language_chosen"] = True
            _fix_llm(data)
            _fix_tuning(data)
            _fix_language(data)
            _fix_web_search(data)
            _fix_shortcuts(data)
            self._write(data)
            self._data = data
            i18n.set_lang(data["language"])
            return copy.deepcopy(data)

    def path(self, key):
        path = Path(self.get()[key]).expanduser()
        return path if path.is_absolute() else ROOT / path
