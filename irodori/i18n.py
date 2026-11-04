"""画面とサーバーが使う文言の辞書(static/i18n/ の en / ja / zh-Hans / zh-Hant の .json)。画面の JS も同じ辞書を読む。
キーが辞書に無い時は英語、英語にも無ければキーそのものを返す。言語は設定(config.json の language)で選ぶ。
language が "auto"(既定)の時は OS の言語で決める(ja → 日本語、簡体字、繁体字、それ以外は英語)。"""
import json
import locale
import os
import subprocess
import sys

LANGS = ("en", "ja", "zh-Hans", "zh-Hant")
AUTO = "auto"
DEFAULT = "en"
_lang = DEFAULT
_words = {}
_os_lang = None


def from_tag(tag):
    """ja-JP / zh_TW.UTF-8 / zh-Hant-HK のような言語タグを LANGS のどれかにする。当てはまらなければ英語。"""
    parts = str(tag or "").split(".")[0].split("@")[0].replace("_", "-").lower().split("-")
    if parts[0] == "ja":
        return "ja"
    if parts[0] == "zh":
        rest = set(parts[1:])
        return "zh-Hant" if rest & {"hant", "tw", "hk", "mo"} else "zh-Hans"
    return DEFAULT


def _windows_tag():
    import ctypes
    lid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
    if lid & 0x3FF == 0x11:
        return "ja"
    if lid & 0x3FF == 0x04:
        return "zh-Hant" if lid in (0x0404, 0x0C04, 0x1404, 0x7C04) else "zh-Hans"
    return ""


def _mac_tag():
    out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True, timeout=5).stdout
    for line in out.splitlines():
        tag = line.strip().strip('(),"')
        if tag:
            return tag   # 先頭が第一言語
    return ""


def detect():
    """OS の言語に合う LANGS のどれか(最初に見つかった物を一度だけ決めて覚える)。"""
    global _os_lang
    if _os_lang is None:
        tag = ""
        try:
            tag = _windows_tag() if sys.platform == "win32" else _mac_tag() if sys.platform == "darwin" else ""
        except Exception:
            tag = ""
        if not tag:
            for name in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
                tag = (os.environ.get(name) or "").split(":")[0]
                if tag and tag not in ("C", "POSIX"):
                    break
                tag = ""
        if not tag:
            try:
                tag = locale.getlocale()[0] or locale.getdefaultlocale()[0] or ""
            except Exception:
                tag = ""
        _os_lang = from_tag(tag)
    return _os_lang


def _load():
    from .config import BUNDLE   # config.py より後で読むための遅延 import(config.py も tuning.py 経由でここを使う)
    for lang in LANGS:
        try:
            _words[lang] = json.loads((BUNDLE / "static" / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _words[lang] = {}


def resolve(setting):
    """設定の language("auto" か LANGS のどれか)を実際に使う言語にする。"""
    return detect() if setting == AUTO else setting if setting in LANGS else DEFAULT


def set_lang(setting):
    global _lang
    _lang = resolve(setting)


def lang():
    return _lang


def t(key, **values):
    """key の文言。{名前} を values で置き換える(辞書に無い名前はそのまま残す)。"""
    if not _words:
        _load()
    text = _words.get(_lang, {}).get(key) or _words.get(DEFAULT, {}).get(key) or key
    for name, value in values.items():
        text = text.replace("{" + name + "}", str(value))
    return text
