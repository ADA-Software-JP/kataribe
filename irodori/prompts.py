"""エージェント(LLM)に渡す文面。どれも設定(config.json の prompts)で書き換えられ、空なら既定を使う。
行数・字数・絵文字の一覧は詳しい設定(tuning.py)の値で、文面の {lines} {max_chars} {emoji} に入る。
字数の指示はすべて {max_chars} から作る({half_chars} は半分、{first_chars} は 3 分の 1 の目安)。文面に別の数字の字数指定は書かない。"""
import json
import re

from irodori import tuning
from irodori.config import BUNDLE

# 演技の絵文字は変な音の元になる(ユーザー確認)ので、既定では読み上げる文章に一つも入れさせない。
# 詳しい設定で「絵文字を残す」にした時だけ、この表にあるものを残す(llm.py)。
_EMOJI = json.loads((BUNDLE / "static" / "emoji.json").read_text(encoding="utf-8"))
EMOJI_LIST = " ".join(f"{x['e']}{x['label']}" for x in _EMOJI)

# 既定の文面。{text} などの差し込みは VARS にあるものだけ置き換え、知らない名前はそのまま残す(落ちない)。
# 言語設定(日本語/English)に関係なく英語で持つ。書き換えるのは自由。
DEFAULTS = {
    # 書き方。システムプロンプトの前半。講義・朗読・解説・ロールプレイなど、読み上げる文章なら何でも書く。
    # 指示文は英語。読み上げる本文は、音声合成が話す言語({speak})で書かせる(指示文の中で明記する)
    "style": (
        "You write text that a speech synthesizer reads aloud, following the user's request, and keep going until you are told to stop.\n"
        "The voice speaks {speak}, so always write natural spoken {speak}, whatever language the request is in.\n"
        "Match the kind of text the request asks for, and do not add settings it does not ask for.\n"
        "Do not repeat yourself, and do not wrap up with a farewell or a summary until you are asked to end.\n"
        "If the user asks you something directly, answer it first, in your own words.\n"
        "Keep each line within {max_chars} characters."
    ),
    # 出力の形式。システムプロンプトの後半。アプリは say を持つ 1 行 1 JSON を読む
    "format": (
        "Output one JSON object per line and nothing else:\n"
        '{"say":"the {speak} text to read aloud","feel":"emotion","how":"way of speaking","voice":"the voice","pause":<seconds>}\n'
        "- say: what is read aloud.\n"
        "- feel, how, voice: only when the request asks for them, as short phrases. voice only on the first line.\n"
        "- pause: seconds of silence after the line.\n"
        "Write {lines} lines per turn."
    ),
    # Claude Code は起動直後にこれを送り、返事が来たら準備完了とみなす(返事の中身は見ない)
    "ready": "When you are ready, reply with just READY.",
    "request": "Request: {text}\nStart writing. Only the very first line must be short enough to be read right away, within {first_chars} characters. Write {lines} lines in total, JSON only.",
    # {now_playing} を含む行は、鳴っている声が無い時は行ごと省く({text} もある行は残す)
    "steer": (
        "The user's remark: {text}\n"
        "Heard so far: \"{heard}\"\n"
        "Now playing (it is cut off the moment your first line is ready): \"{now_playing}\"\n"
        "Thrown away, not heard: \"{dropped}\"\n"
        "The remark comes before everything earlier. If it asks to stop something, stop it from the next line. If it asks you something, answer it first.\n"
        "Begin your first line with a brief, natural acknowledgement of the remark (an aizuchi such as a listener gives), then go straight on. Keep that first line short.\n"
        "Write {lines} lines, JSON only."
    ),
    # 切り替えた直後の新しいセッションの、最初の依頼の前に付ける。中身が空の行は省く
    "handover": (
        "Session handover. A live reading is already in progress, and you are taking over from the writer before you (an earlier session or another model). You have no memory of it, so this is everything you need.\n"
        "The user's requests so far, oldest first. The first is the original request and the others are remarks made while it played. The newest one wins over the earlier ones:{requests}\n"
        "The last lines the listener has heard, oldest first:{heard}\n"
        "The line now playing (it will play to the end): \"{now_playing}\"\n"
        "Lines already written that are waiting to play (they will be heard next, in this order; do not repeat them, and continue after the last of them):{upcoming}\n"
        "Continue from exactly this point, in the same subject, form, way of address, tone and rhythm as the lines above, as if you had written them yourself. "
        "Do not start over, do not introduce the topic again, and do not summarize what has been said. "
        "If a newer request changed the subject or the form, the lines before it are history only.\n"
        "The next instruction follows."
    ),
    # 検索の可否。システムプロンプトの最後に付ける(Claude Code と Codex は設定の「Web 検索を使う」がオンの時だけ search、それ以外は no_search)
    "search": (
        "You can search the web. Search only when you must check something that may have changed or that you cannot know: latest events, news, today's information, current prices and the like. "
        "Do not search for anything else, and when the request does not need it, start writing at once without searching. "
        "When the request asks for news or current information, read out what you found (the facts of today's items), not general background. "
        "Whenever you use what you found, say the name of the outlet or publisher briefly in the spoken text (for example \"according to NHK\"), and never read out a URL."
    ),
    "no_search": (
        "You cannot look anything up. Do not invent anything that cannot be checked, such as today's events; "
        "explain the background and the mechanisms within what you reliably know."
    ),
    "more": "Please continue. From where you left off, write {lines} more lines of what has not been said yet, JSON only. Do not end the talk, and do not repeat the same content.",
    "retry": "The format was wrong. Output only one JSON object per line ({\"say\": ...}). Write the next {lines} lines of the continuation again.",
    "ping": "Reply with just \"OK\".",
    # 接続テスト(Codex)の指示。Claude Code と LM Studio の接続テストでは使わない
    "ping_system": "Reply briefly to the user's words.",
}
# 文面ごとの差し込み名
CHARS = ["max_chars", "half_chars", "first_chars"]
VARS = {"style": ["speak", "lines", *CHARS, "emoji"], "format": ["speak", "lines", *CHARS, "emoji", "feel_max", "how_max", "voice_max"],
        "request": ["text", "lines", *CHARS], "steer": ["text", "heard", "now_playing", "dropped", "lines"], "more": ["lines"], "retry": ["lines"],
        "handover": ["requests", "heard", "now_playing", "upcoming"]}
# 文面ごとの上限(字)。書き方と形式は長くなりうるので広めにとる
LIMITS = {"style": 8000, "format": 8000}
LIMIT = 2000


def limit(key):
    return LIMITS.get(key, LIMIT)


def text(custom, key):
    """設定の文面。空なら既定。"""
    value = (custom or {}).get(key)
    return value.strip() if isinstance(value, str) and value.strip() else DEFAULTS[key]


def fill(template, **values):
    """{名前} を置き換える。VARS にない名前や、波かっこ入りの JSON はそのまま残す。"""
    return re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


# 読み上げる言語の名前(英語)。live が、読み込んだモデルと設定から決めて差し替える
speak_name = lambda: "Japanese"


def _vars(tune):
    """設定の値から、文面に差し込む名前の値を作る。"""
    tune = tune or tuning.defaults()
    cap = tune["max_chars"]
    return {"speak": speak_name(), "lines": tune["lines_per_turn"], "max_chars": cap, "half_chars": cap // 2, "first_chars": max(10, cap // 3),
            "emoji": EMOJI_LIST if tune["emoji_keep"] else "",
            "feel_max": tune["feel_max"], "how_max": tune["how_max"], "voice_max": tune["voice_max"]}


def system(custom, tune=None, search=False):
    """LLM に渡す指示の全文: 書き方 + 出力の形式 + 検索できるかどうか。"""
    tail = text(custom, "search" if search else "no_search")
    return fill(f"{text(custom, 'style')}\n\n{text(custom, 'format')}\n\n{tail}", **_vars(tune))


def ready(custom):
    return text(custom, "ready")


def request(custom, said, tune=None):
    return fill(text(custom, "request"), text=said, **_vars(tune))


def _without_empty(template, values, always=("text",)):
    """空の差し込み({heard} など)を含む行は、行ごと省く(always の名前が同じ行にあれば残す)。全部省くことになるなら、そのまま返す。"""
    kept = []
    for ln in template.split("\n"):
        empty = [k for k, v in values.items() if "{%s}" % k in ln and not v]
        if not empty or any("{%s}" % k in ln for k in always):
            kept.append(ln)
    return "\n".join(kept) if "".join(kept).strip() else template


def steer(custom, said, now_playing, tune=None, heard="", dropped=""):
    """途中の一言。聞き終えた文章(heard)、今鳴っている文章、捨てた予定(dropped)を区別して渡す。"""
    values = {"heard": heard, "now_playing": now_playing, "dropped": dropped}
    return fill(_without_empty(text(custom, "steer"), values), text=said, **values, **_vars(tune))


def handover(custom, requests, heard, now_playing, upcoming, tune=None):
    """LLM の種類・モデル・文面を変えた直後の新しいセッションに、これまでの流れを渡す。requests / heard / upcoming は文字列のリスト。"""
    def bullets(items, number=False):
        return "".join(f"\n{i}. {x}" if number else f"\n- {x}" for i, x in enumerate(items, 1))
    values = {"requests": bullets(requests, True), "heard": bullets(heard), "now_playing": now_playing, "upcoming": bullets(upcoming)}
    return fill(_without_empty(text(custom, "handover"), values, always=()), **values, **_vars(tune))


def more(custom, tune=None):
    return fill(text(custom, "more"), **_vars(tune))


def retry(custom, tune=None):
    return fill(text(custom, "retry"), **_vars(tune))


def ping(custom):
    return text(custom, "ping")


def ping_system(custom):
    return text(custom, "ping_system")
