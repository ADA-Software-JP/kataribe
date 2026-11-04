"""詳しい設定。LLM や Irodori に渡る文と、出力を左右する決め打ちの値を、すべて config.json の tuning に持つ。
SPEC が唯一の定義で、既定値・型・範囲をここに置く。画面の見出し・名前・説明は辞書(static/i18n)の tuning.group.<グループ>、
tuning.<キー>.label / tuning.<キー>.about にある。検証(clean)も画面の組み立ても SPEC から行う。
既定値は、設定を足す前にコードへ直書きしていた値のまま。"""
import functools
import math
import re

from .i18n import t

# (グループ, キー, 種類, 既定値, 範囲または選択肢)
# 種類: int / float / bool / text / regex / choice。regex の既定の空欄は「その処理をしない」。text・regex の範囲は最大の字数。
# choice の選択肢は値だけ(表示名は辞書の tuning.choice.<値>)。
GROUPS = ["tell", "write", "tidy", "play", "seed", "clip", "agent"]
SPEC = [
    ("tell", "instructions_always", "text", "", 400),
    ("tell", "instructions_sep", "text", "。", 8),
    ("tell", "steps", "int", 4, (1, 32)),
    ("write", "lines_per_turn", "int", 5, (1, 20)),
    ("write", "max_chars", "int", 120, (40, 400)),
    ("write", "emoji_keep", "bool", False, None),
    ("write", "how_max", "int", 40, (5, 400)),
    ("write", "feel_max", "int", 20, (5, 400)),
    ("write", "voice_max", "int", 60, (10, 400)),
    ("tidy", "stage_pattern", "regex", r"[(（【\[][^)）】\]]*[)）】\]]", 400),
    ("tidy", "kaomoji_pattern", "regex", r"[(（][^ぁ-んァ-ン一-龥]*[)）]", 400),
    ("tidy", "symbol_pattern", "regex", r"[\^*_=+<>|/\\;:'\"`@#$%&\[\]{}ω´｀°ﾟ∀▽≧≦]+", 400),
    ("tidy", "voice_words", "regex", r"声|男|女|少年|少女|子ども|老|\b(?:voice|male|female|man|woman|boy|girl|child|kid|elderly|old|young)\b", 400),
    ("tidy", "change_pattern", "regex",
     r"^\s*(?:/change|change(?=\s*(?:[,.!?、。！？]|$))|チェンジ)(?:で|して|お願い|\s*please)?(?:[、。,.!！？?\s]+|$)", 400),
    ("play", "preload", "int", 3, (1, 10)),
    ("play", "stock_low", "int", 2, (0, 10)),
    ("play", "lead_min", "float", 1.0, (0, 10)),
    ("play", "lead_factor", "float", 2.0, (0, 10)),
    ("play", "pause_default", "float", 0.0, (0, 10)),
    ("seed", "seed_mode", "choice", "random", ("random", "fixed")),
    ("seed", "seed_value", "int", 0, (0, 2147483647)),
    ("seed", "reverb_check", "bool", True, None),
    ("seed", "reverb_db", "float", -38.0, (-80, 0)),
    ("seed", "reverb_max_redo", "int", 4, (0, 10)),
    ("clip", "clip_keep_ms", "int", 40, (0, 500)),
    ("clip", "clip_fade_ms", "int", 12, (0, 100)),
    ("clip", "clip_threshold", "float", 0.05, (0.001, 0.5)),
    ("clip", "clip_peak_db", "float", -1.0, (-12, 0)),
    ("clip", "check_sec_min", "float", 0.08, (0.01, 1)),
    ("clip", "check_sec_max", "float", 0.6, (0.1, 3)),
    ("clip", "check_min_speech", "float", 0.4, (0, 1)),
    ("clip", "check_min_chars", "int", 4, (0, 50)),
    ("clip", "max_redo", "int", 2, (0, 5)),
    ("clip", "max_fails", "int", 3, (1, 10)),
    ("clip", "asr_min_match", "float", 0.5, (0.1, 0.95)),
    ("agent", "history_turns", "int", 4, (0, 20)),
    ("agent", "handover_lines", "int", 20, (0, 100)),
    ("agent", "lm_temperature", "float", 0.9, (0, 2)),
    ("agent", "lm_max_tokens", "int", 2000, (100, 32000)),
    ("agent", "codex_idle_limit", "int", 120, (10, 900)),
    ("agent", "claude_start_timeout", "int", 180, (10, 900)),
]
SPEC_BY_KEY = {row[1]: row for row in SPEC}
# 設定画面に出すのはこれだけ。ほかは config.json の tuning で変える(画面に並べると多すぎて分からない)
SHOWN = ("max_chars", "lines_per_turn", "reverb_check")


def defaults():
    return {row[1]: row[3] for row in SPEC}


def spec():
    """画面向けの定義。"""
    out = []
    for group, key, kind, default, rng in SPEC:
        item = {"group": group, "key": key, "kind": kind, "default": default, "shown": key in SHOWN}
        if kind in ("int", "float"):
            item["min"], item["max"] = rng
        elif kind in ("text", "regex"):
            item["max"] = rng
        elif kind == "choice":
            item["choices"] = list(rng)
        out.append(item)
    return out


@functools.lru_cache(maxsize=64)
def _compile(pattern):
    return re.compile(pattern, re.I)


def rx(pattern):
    """設定の正規表現。空欄(その処理をしない)か、読めない時は None。"""
    if not isinstance(pattern, str) or not pattern:
        return None
    try:
        return _compile(pattern)
    except re.error:
        return None


def _check(row, value):
    """1 項目を検証して整えた値を返す。正しくなければ ValueError(理由)。"""
    _, key, kind, default, rng = row
    label = t(f"tuning.{key}.label")
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(t("tuning.err_bool", label=label))
        return value
    if kind in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(t("tuning.err_number", label=label))
        if kind == "int":
            if value != int(value):
                raise ValueError(t("tuning.err_int", label=label))
            value = int(value)
        else:
            value = float(value)
        lo, hi = rng
        if not lo <= value <= hi:
            raise ValueError(t("tuning.err_range", label=label, lo=lo, hi=hi))
        return value
    if kind == "choice":
        if value not in rng:
            raise ValueError(t("tuning.err_choice", label=label, list=" / ".join(t(f"tuning.choice.{c}") for c in rng)))
        return value
    if not isinstance(value, str):
        raise ValueError(t("tuning.err_text", label=label))
    if len(value) > rng:
        raise ValueError(t("tuning.err_length", label=label, n=rng))
    if kind == "regex" and value:
        try:
            compiled = re.compile(value, re.I)
        except re.error as e:
            raise ValueError(t("tuning.err_regex", label=label, e=e))
        if key == "change_pattern" and compiled.match(""):
            raise ValueError(t("tuning.err_regex_empty", label=label))
    return value


def clean(patch):
    """画面から来た変更を検証する。知らないキーは捨て、正しくない値は ValueError(理由)。"""
    if not isinstance(patch, dict):
        raise ValueError(t("tuning.bad_format"))
    return {key: _check(SPEC_BY_KEY[key], value) for key, value in patch.items() if key in SPEC_BY_KEY}


# 以前の版の既定値。保存済みの値がこれと同じ(変えていない)なら、英語の言い方も入った今の既定へ移す
OLD_DEFAULTS = {
    "voice_words": "声|男|女|少年|少女|子ども|老",
    "change_pattern": r"^\s*(?:/change|チェンジ)(?:で|して|お願い)?(?:[、。,.!！？?\s]+|$)",
}


def repair(data):
    """保存済みの値を読んだ後、正しくない値と足りない値を既定に直す(壊れた config.json でも起動できるように)。直したら True。"""
    changed = False
    for row in SPEC:
        key = row[1]
        if key in OLD_DEFAULTS and data.get(key) == OLD_DEFAULTS[key]:
            data[key] = row[3]
            changed = True
        try:
            fixed = _check(row, data.get(key, row[3]))
        except ValueError:
            fixed = row[3]
        if key not in data or data[key] != fixed or type(data[key]) is not type(fixed):
            data[key] = fixed
            changed = True
    return changed
