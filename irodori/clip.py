"""生成した PCM の検査と整え。PCM の切り出しと倍率変更だけで、音は作らない。しきい値などは詳しい設定(tuning.py)の値。"""
import array
import io
import math
import sys
import wave

from . import tuning

FRAME = 0.010            # 短時間エネルギーを測る 1 区間(秒)
CLIP_LEVEL = 32767 * 0.99
CLIP_COUNT = 24


def wav_bytes(pcm, rate):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _samples(pcm):
    data = array.array("h")
    data.frombytes(pcm[: len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        data.byteswap()
    return data


def _bytes(data):
    if sys.byteorder == "big":
        data = array.array("h", data)
        data.byteswap()
    return data.tobytes()


def inspect(pcm, rate, text, stop, opt=None, lang="ja"):
    """検査して、前後の息・ノイズを切った PCM と報告を返す。報告の reasons が空なら合格。
    報告: raw/kept(秒), head/tail(切った ms), speech(発話の秒), chars, gain(倍率), reasons
    opt は詳しい設定(tuning の clip_* と check_*)。無ければ既定。"""
    opt = opt or tuning.defaults()
    data = _samples(pcm)
    total = len(data)
    info = {"raw": total / rate, "kept": total / rate, "head": 0.0, "tail": 0.0, "speech": 0.0, "chars": 0,
            "gain": 1.0, "reasons": []}
    reasons = info["reasons"]
    if stop != "complete":
        reasons.append(f"stop={stop}")
    if not total:
        reasons.append("empty")
        return pcm, info
    if max(data) >= CLIP_LEVEL or min(data) <= -CLIP_LEVEL:
        if sum(1 for v in data if abs(v) >= CLIP_LEVEL) >= CLIP_COUNT:
            reasons.append("clip")

    n = max(1, round(rate * FRAME))
    energy = [math.sqrt(sum(v * v for v in data[i: i + n]) / len(data[i: i + n])) for i in range(0, total, n)]
    top = max(energy)
    if top < 30:
        reasons.append("silent")
        return pcm, info
    active = [i for i, e in enumerate(energy) if e >= top * opt["clip_threshold"]]
    keep = round(rate * opt["clip_keep_ms"] / 1000)
    chars = sum(1 for ch in text if ch.isalnum())
    start = max(0, active[0] * n - keep)
    end = min(total, (active[-1] + 1) * n + keep)
    out = data[start:end]
    speech = (active[-1] + 1 - active[0]) * n / rate
    info.update(kept=len(out) / rate, head=start / rate * 1000, tail=(total - end) / rate * 1000, speech=speech, chars=chars)

    if speech * rate / total < opt["check_min_speech"]:
        reasons.append("speech<40%")
    # 1 字あたりの秒数の目安は、日本語と中国語(1 字 = 1 音節前後)で決めたもの。ほかの言語では測らない
    if lang in ("ja", "zh") and chars >= opt["check_min_chars"]:
        per = info["kept"] / chars
        if per < opt["check_sec_min"]:
            reasons.append(f"short({per:.2f}s/char)")
        elif per > opt["check_sec_max"]:
            reasons.append(f"long({per:.2f}s/char)")

    fade = min(round(rate * opt["clip_fade_ms"] / 1000), len(out) // 2)
    for i in range(fade):
        k = i / fade
        out[i] = int(out[i] * k)
        out[len(out) - 1 - i] = int(out[len(out) - 1 - i] * k)
    peak = max(max(out), -min(out))
    peak_max = 32767 * 10 ** (opt["clip_peak_db"] / 20)
    if peak > peak_max:
        info["gain"] = peak_max / peak
        out = array.array("h", (int(v * info["gain"]) for v in out))
    return _bytes(out), info


REVERB_FROM, REVERB_TO, REVERB_BEFORE = 3, 15, 10   # 切れ目の 30〜150ms 後と、直前 100ms(10ms の区間で数える)
REVERB_MIN_TAIL = 5                                  # 切れ目の後に、30〜150ms のうち最低これだけ(50ms)の無音が要る
REVERB_FLOOR = -90.0


def reverb_db(pcm, rate, opt=None):
    """発話が切れた直後の残響の強さ(dB)。0 に近いほど響きが長く、-30 より小さければ乾いた声。測れなければ None。
    10ms ごとのエネルギーで、発話の切れ目の直後 30〜150ms の平均が、直前 100ms の平均から何 dB 下がるかを、切れ目ごとに出して中央値にする。
    発話とみなす音量は clip_threshold(inspect と同じ)。切れ目の後すぐに次の発話が来る短い間は、残響を測れないので数えない。"""
    opt = opt or tuning.defaults()
    data = _samples(pcm)
    n = max(1, round(rate * FRAME))
    power = [sum(v * v for v in data[i: i + n]) / len(data[i: i + n]) for i in range(0, len(data), n)]
    top = max(power, default=0.0)
    if top < 30 * 30:
        return None
    floor = top * opt["clip_threshold"] ** 2
    voiced = [p >= floor for p in power]
    levels = []
    for b in range(REVERB_BEFORE // 2, len(power)):
        if voiced[b] or not voiced[b - 1]:
            continue
        tail = power[b + REVERB_FROM: b + REVERB_TO]
        if len(tail) < REVERB_MIN_TAIL or any(voiced[b + REVERB_FROM: b + REVERB_TO]):
            continue
        before = power[max(0, b - REVERB_BEFORE): b]
        loud, quiet = sum(before) / len(before), sum(tail) / len(tail)
        levels.append(max(REVERB_FLOOR, 10 * math.log10(max(quiet, 1e-9) / loud)))
    if not levels:
        return None
    levels.sort()
    mid = len(levels) // 2
    return levels[mid] if len(levels) % 2 else (levels[mid - 1] + levels[mid]) / 2
