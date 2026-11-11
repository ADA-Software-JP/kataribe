"""ライブセッション: 依頼 → エージェントが書く読み上げ文(JSON Lines)→ 1 行ごとに音声化 → 止めるまで続ける。
声はセッションの最初の 1 本で決め、以後すべての行をその声で読む(worker の add_voice で登録する)。"""
import atexit
import random
import re
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import asr, clip, history, library, prompts, tuning, voices
from .engine import Cancelled, EngineError, log
from .i18n import lang as ui_lang, t
from .llm import LineSplitter, LLMError, parse_line, split_say

LIVE = ("queued", "making", "ready")
KEEP_PLAYED = 200  # 鳴り終えた音声の一時 WAV を残す本数
HEARD_LINES = 4  # 途中の一言に添える、聞き終えた文の本数
BRIEFS_KEPT = 6  # 新しいセッションに引き継ぐ依頼と途中の一言の数(最初の依頼 + 直近)
QUOTED = re.compile(r"(?:\s*「[^」]*」)+\s*")
# 「」のほかに、全体を ""(英語のキーボードで打てる引用符)で囲んだ入力もそのまま読む
WRAPPERS = (('"', '"'), ("“", "”"))
# 声を変える合図・声の指定とみなす言葉・再生の段取り・検査などの数値は、詳しい設定(tuning.py)から読む


# 響きを表す語。Irodori は「エコーなしで」の否定を読めず、語に引っぱられてかえって響かせるので、その句ごと渡さない
ECHO_WORDS = re.compile(r"エコー|反響|残響|リバーブ|響|こだま|echo|reverb", re.I)


def _instructions(line, opt):
    """Irodori に渡す話し方: 常に足す話し方(設定、既定は空)、全体の how、その行の feel(感情・演技)を区切りでつなぐ。
    響きを表す句(「エコーなしで」など)は除く。"""
    joined = opt["instructions_sep"].join(x for x in (opt["instructions_always"], line["how"], line["feel"]) if x)
    parts = [p for p in re.split(r"[、,。.]\s*", joined) if p.strip() and not ECHO_WORDS.search(p)]
    return "、".join(parts)


# 読み上げる言語の英語名(LLM への指示に入れる)。表に無い言語は、コードのまま伝える
LANG_NAMES = {"ja": "Japanese", "en": "English", "zh": "Chinese", "ko": "Korean", "de": "German", "es": "Spanish", "fr": "French",
              "it": "Italian", "pt": "Portuguese", "ru": "Russian"}


class Live:
    def __init__(self, config, engine, llm, bus):
        self.config, self.engine, self.llm, self.bus = config, engine, llm, bus
        self.cv = threading.Condition()
        self.closed = False
        self.root = Path(tempfile.gettempdir()) / "irodori-live" / uuid.uuid4().hex[:8]
        self._ids = 0
        self._voice_n = 0
        self._cmd = None
        self._making = None
        self._last_gen = 0.0
        self.checker = asr.Checker(config, engine)
        self._speak_seen = ""   # 直前のターンで書かせた言語
        prompts.speak_name = lambda: LANG_NAMES.get(self.speak_language(), self.speak_language())
        self.checker.on_broken = lambda: self.bus.emit("live_notice", {"text": t("live.asr_stopped"), "kind": "err"})
        self.sess = 0
        self.running = True
        self._reset_state()
        atexit.register(self.close)
        threading.Thread(target=self._tts_loop, daemon=True).start()
        threading.Thread(target=self._agent_loop, daemon=True).start()

    def _opt(self):
        return self.config.get()["tuning"]

    def _new_seed(self):
        """声の seed。設定が「固定」ならその値、それ以外は毎回ランダム。"""
        opt = self._opt()
        return opt["seed_value"] if opt["seed_mode"] == "fixed" else random.randrange(2**31)

    def _reset_state(self):
        self.log, self.lines = [], {}
        # 会話の id(履歴のファイル名)。新しく始めるたびに変わる
        self.hid = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
        self.created = time.time()
        self.epoch = getattr(self, "epoch", 0) + 1
        self.how, self.turns, self.speed = self._default_how(), 0, 1.0
        self.seed = self._new_seed()
        self.rewriting = False
        self.agent = {"state": "idle", "n": 0, "error": ""}
        self.tts_error = ""
        self.auto_ok = True
        self.fails = 0
        self.sess += 1
        self.steer, self._steer_mark, self._steer_line = "", "", None
        self._retry = None
        self.briefs = []        # 今の会話の依頼と途中の一言(古い順)。LLM を切り替えた時に新しいセッションへ渡す
        self._llm_lines = 0     # LLM が書いた行の数
        self._writer = None     # 直前に書いたエージェント(別のものに替わったら、これまでの流れを渡す)
        self.voice, self.voice_state, self.voice_origin = self._start_voice()
        self.voice_ref, self.voice_saved = None, ""
        self.voice_desc = ""
        self.voice_try = None   # 残響で新しい声を作り直している間の (回数, 最大回数)

    def _start_voice(self):
        """そのセッションの声。既定の声が選ばれていればそれ、なければ最初の 1 本で決める。"""
        name = self.config.get()["default_voice"]
        if name and name in self.engine.voice_files():
            return name, "fixed", "saved"
        return "none", "pending", "first"

    def speak_language(self):
        """読み上げる言語のコード。設定で選んだ言語、無ければ(auto)モデルが話せる言語のうち画面の言語、それも無ければ日本語か最初の言語。"""
        langs = self.engine.model.get("languages") or ["ja"]
        want = self.config.get().get("speak_language") or "auto"
        if want in langs:
            return want
        if len(langs) == 1:
            return langs[0]
        ui = ui_lang().split("-")[0]
        return ui if ui in langs else ("ja" if "ja" in langs else langs[0])

    def _preset_voice(self):
        """内蔵の声しか使えないモデル(Qwen3-TTS など)の声: 設定で選んだ声、無ければ読み上げる言語の最初の声。"""
        presets = self.engine.presets()
        if not presets:
            return "none"
        names = [v["name"] for v in presets]
        want = self.config.get().get("tts_voice") or ""
        if want in names:
            return want
        lang = self.speak_language()
        return next((v["name"] for v in presets if v.get("language") == lang), names[0])

    def _default_how(self):
        return str(self.config.get().get("default_how") or "").strip()

    def _next_id(self, prefix):
        self._ids += 1
        return f"{prefix}{self._ids}"

    def close(self):
        self.closed = True
        with self.cv:
            self.cv.notify_all()
        shutil.rmtree(self.root, ignore_errors=True)
        try:
            self.root.parent.rmdir()
        except OSError:
            pass

    # --- 状態の配信 ---------------------------------------------------------------------------------------------
    def status(self):
        making = any(ln["state"] == "making" for ln in self.lines.values())
        return {"sid": self.root.name, "hid": self.hid, "running": self.running, "voice": self.voice,
                "voice_state": self.voice_state, "voice_origin": self.voice_origin, "voice_saved": self.voice_saved, "how": self.how,
                "rewriting": self.rewriting, "agent": dict(self.agent), "making": making,
                "tts_error": self.tts_error, "steer": self.steer, "voice_try": self.voice_try}

    def snapshot(self):
        with self.cv:
            return dict(self.status(), log=[dict(e) for e in self.log])

    def _emit_status(self):
        self.bus.emit("live_status", self.status())

    def _emit_line(self, line, *keys):
        self.bus.emit("live_line", {k: line[k] for k in ("id",) + keys})

    # --- 操作 ---------------------------------------------------------------------------------------------------
    def _verbatim(self, text):
        """LLM を通さず読む場合の行。「」で囲まれた入力、または LLM が使えない時の全入力。通す場合は None。"""
        if QUOTED.fullmatch(text):
            parts = re.findall(r"「([^」]*)」", text)
        elif text.startswith("「") and text.endswith("」"):
            parts = [text[1:-1]]
        elif len(text) > 1 and any(text[0] == a and text[-1] == b for a, b in WRAPPERS):
            parts = [text[1:-1]]
        elif not self.llm.snapshot()["ok"]:
            parts = [text]
        else:
            return None
        # 長い行は、LLM が書いた行と同じく、文の切れ目で max_chars 以内に分ける
        cap = self._opt()["max_chars"]
        found = [piece for part in parts for ln in part.splitlines() if ln.strip() for piece in split_say(ln.strip(), cap)]
        if not found:
            raise ValueError(t("live.empty_read"))
        return found

    def send(self, text):
        text = text.strip()
        if not text:
            raise ValueError(t("srv.empty"))
        pattern = tuning.rx(self._opt()["change_pattern"])
        change = pattern.match(text) if pattern else None
        if change:
            text = text[change.end():].strip()
            saved = next((n for n in self.engine.voice_files() if n.lower() == text.lower()), None)
            if saved:
                self.use_voice(saved)
                self.bus.emit("live_notice", {"text": t("live.voice_set", name=saved), "kind": "ok"})
                return
            self.new_voice()
            self.bus.emit("live_notice", {"text": t("live.voice_change"), "kind": "ok"})
            if not text:
                return
        verbatim = self._verbatim(text)
        with self.cv:
            self.running, self.auto_ok = True, True
            if verbatim is not None:
                for ln in verbatim:
                    self._add_line(ln, self.how, None, None)
                self._save_history()
                self._emit_status()
                self.cv.notify_all()
                return
            entry = {"id": self._next_id("u"), "kind": "user", "text": text, "at": time.time()}
            self.log.append(entry)
            self.bus.emit("live_entry", entry)
            self.briefs.append(text)
            self._save_history()
            self._drop_upcoming()
            self.epoch += 1
            self.rewriting = self.turns > 0
            self.steer = self._steer_mark = text if self.turns else ""
            self._steer_line = None
            self._cmd = ("steer" if self.turns else "request", text, self._heard() if self.turns else ("", "", ""))
            self.turns += 1
            self.agent.update(error="", n=0)
            self._emit_status()
            self.llm_agent_interrupt()
            self.cv.notify_all()

    def llm_agent_interrupt(self):
        try:
            self.llm.agent().interrupt()
        except LLMError:
            pass

    def _heard(self):
        """途中の一言に添える、聞き手が実際に聞いた所。(今鳴っている文, 聞き終えた直近の文, 聞かれずに捨てた文)。"""
        lines = list(self.lines.values())
        playing = next((ln["text"] for ln in lines if ln["state"] == "playing"), "")
        done = [ln["text"] + ("…(cut off here)" if ln.get("cut") else "") for ln in lines if ln["state"] == "played"][-HEARD_LINES:]
        last = max((i for i, ln in enumerate(lines) if ln["state"] in ("played", "playing")), default=-1)
        dropped = [ln["text"] for ln in lines[last + 1:] if ln["state"] == "dropped"]
        return playing, "".join(done), "".join(dropped)

    def _handover(self, agent):
        """新しいセッション(LLM の種類・モデル・文面を変えた直後、または前回と別のエージェント)の最初のターンに添える、これまでの流れ。
        依頼と途中の一言すべて、聞き終えた直近の文(本数は詳しい設定の handover_lines)、今鳴っている文、まだ鳴っていない予定の文。
        続きを書いている同じセッションや、まだ何も書いていない最初の依頼では空。"""
        with self.cv:
            fresh = not agent.turns_sent or agent is not self._writer
            self._writer = agent
            if not fresh or not self._llm_lines:
                return ""
            lines = list(self.lines.values())
            count = self._opt()["handover_lines"]
            heard = [ln["text"] for ln in lines if ln["state"] == "played"][-count:] if count else []
            playing = next((ln["text"] for ln in lines if ln["state"] == "playing"), "")
            upcoming = [ln["text"] for ln in lines if ln["state"] in LIVE]
            briefs = self.briefs if len(self.briefs) <= BRIEFS_KEPT else self.briefs[:1] + self.briefs[1 - BRIEFS_KEPT:]
            return prompts.handover(self.config.get()["prompts"], briefs, heard, playing, upcoming, self._opt())

    def _drop_upcoming(self):
        ids = []
        for ln in self.lines.values():
            if ln["state"] in LIVE:
                if ln["state"] == "making":
                    self.engine.cancel(ln["id"])
                ln["state"] = "dropped"
                self._forget_file(ln["id"])
                ids.append(ln["id"])
        if ids:
            self.bus.emit("live_drop", {"ids": ids})
        return ids

    def _forget_file(self, line_id):
        try:
            (self.root / f"{line_id}.wav").unlink()
        except OSError:
            pass

    def stop(self):
        with self.cv:
            self.running = False
            self._emit_status()
            self.cv.notify_all()

    def resume(self):
        with self.cv:
            self.running, self.auto_ok = True, True
            self._emit_status()
            self.cv.notify_all()

    def reset(self):
        with self.cv:
            self.running = True
            self._cmd = None
            if self._making:
                self.engine.cancel(self._making)
            self._reset_state()
            shutil.rmtree(self.root, ignore_errors=True)
            self.bus.emit("live_reset", {})
            self._emit_status()
            self.cv.notify_all()
        self.llm_agent_interrupt()
        self.llm.reset()

    def retry(self):
        """失敗したエージェントの依頼と、作れなかった声をやり直す。"""
        with self.cv:
            self.running, self.auto_ok = True, True
            self._cmd = self._retry or self._cmd
            self._retry = None
            self.agent["error"] = ""
            if self.tts_error:
                self.fails, self.tts_error = 0, ""
                for ln in self.lines.values():
                    if ln["state"] == "failed":
                        ln["state"] = "queued"
                        self._emit_line(ln, "state")
            self._emit_status()
            self.cv.notify_all()

    def after_login(self):
        """ログインが通った後、ログイン切れで止まっていた依頼があればやり直す。"""
        if self._retry and self.agent["error"]:
            self.retry()

    def mark_played(self, line_id, done, failed=False, cut=False):
        with self.cv:
            ln = self.lines.get(line_id)
            if not ln or ln["state"] == "dropped":
                return False
            # 開始と終了の知らせは別々に届くので、鳴り終えた後に遅れて届いた「開始」では戻さない
            if not done and not failed and ln["state"] == "played":
                return True
            if failed and not ln.get("replayed"):
                # 画面が音声を読み込めなかった: 1 度だけ作り直して、同じ位置で鳴らし直す
                ln.update(state="queued", replayed=True)
                self._forget_file(line_id)
                self._emit_line(ln, "state")
                self._emit_status()
                self.cv.notify_all()
                return True
            if failed:
                # 画面が音声を読み込めなかった。鳴ったことにはせず、失敗として次へ進む。飛ばしたことは画面に知らせる
                ln["state"] = "failed"
                self._emit_line(ln, "state")
                self.bus.emit("live_notice", {"text": t("live.play_failed", text=ln["text"][:30]), "kind": "err"})
                self._emit_status()
                self.cv.notify_all()
                return True
            if not done and line_id == self._steer_line:
                self.steer, self._steer_line = "", None
            ln["state"] = "played" if done else "playing"
            ln["ended_at" if done else "started_at"] = round(time.time(), 3)
            if done and cut:
                # 返事に替えるために途中で切った: 聞いたのは途中まで。全文の音声は残さない
                ln["cut"] = True
                self._trim_played()
                self._save_history()
            elif done:
                # 「鳴った音声を全部残す」は、鳴り終えた時に残す(先読みしただけで捨てた音声は残さない)
                path = self.wav_path(line_id)
                if self.config.get()["keep_all"] and path and not ln["stem"]:
                    try:
                        ln["out"] = str(self.config.path("output_dir"))
                        ln["stem"] = library.save(ln["out"], path, self._meta(ln))
                        self._emit_line(ln, "stem")
                    except OSError as e:
                        log(f"could not keep the WAV in the output folder: {e}")
                        self.bus.emit("live_notice", {"text": t("live.keep_failed", e=e), "kind": "err"})
                self._trim_played()
                self._save_history()
            self._emit_line(ln, "state")
            self._emit_status()
            self.cv.notify_all()
            return True

    def _trim_played(self):
        """止めずに流し続けても一時フォルダーが膨らまないよう、鳴り終えた音声は新しい KEEP_PLAYED 本だけ残す
        (それより古い行は「これまでの文章」の★で残せなくなる。★を付けた行は outputs/ に写してあるので消してよい)。"""
        played = [ln for ln in self.log if ln["kind"] == "line" and ln["state"] == "played"]
        for ln in played[:-KEEP_PLAYED]:
            if not ln.get("gone"):
                self._forget_file(ln["id"])
                ln["gone"] = True

    # --- 履歴 ---------------------------------------------------------------------------------------------------
    def _save_history(self):
        """今の会話を履歴に書く(cv を持って呼ぶ)。鳴り終えた行と、依頼・途中の一言だけを残す。"""
        kept = [{k: e.get(k) for k in ("kind", "text", "how", "feel", "speed", "pause", "starred", "stem", "out", "at", "cut")}
                for e in self.log if e["kind"] == "user" or (e["kind"] == "line" and e["state"] == "played")]
        if not kept:
            return
        title = next((e["text"] for e in kept if e["kind"] == "user"), kept[0]["text"])
        saved = self.voice if self.voice_origin == "saved" else self.voice_saved
        data = {"id": self.hid, "title": title[:80], "created": self.created, "updated": time.time(), "log": kept,
                "briefs": self.briefs, "how": self.how, "voice_desc": self.voice_desc, "seed": self.seed, "saved_voice": saved or ""}
        try:
            history.save(data)
        except OSError as e:
            log(f"could not write the history: {e}")
            return
        self.bus.emit("history", {})

    def open_history(self, hid):
        """履歴の会話を開き直す。止めた状態で、続きは ▶ か一言で書かせる。声はその会話の声(無ければ新しい声)。"""
        data = history.load(hid)
        plan = None
        with self.cv:
            self._cmd = None
            if self._making:
                self.engine.cancel(self._making)
            self._reset_state()
            shutil.rmtree(self.root, ignore_errors=True)
            self.hid, self.created = data["id"], data.get("created", time.time())
            for e in data.get("log", []):
                entry = dict(e, id=self._next_id("u" if e.get("kind") == "user" else "l"))
                if entry["kind"] == "line":
                    entry.update(state="played", gone=True, voice="", seed=0, redo=0, gen_seconds=0.0, audio_seconds=0.0)
                    entry.setdefault("from", "")
                    self.lines[entry["id"]] = entry
                self.log.append(entry)
            self.briefs = list(data.get("briefs") or [])
            self.how, self.voice_desc = data.get("how") or self.how, data.get("voice_desc") or ""
            self.seed = data.get("seed") or self.seed
            self._llm_lines = len(self.lines)
            self.turns = 1 if self.lines else 0
            self.running = False
            saved = data.get("saved_voice") or ""
            ref = history.voice_file(self.hid)
            if saved and saved in self.engine.voice_files():
                self.voice, self.voice_state, self.voice_origin = saved, "fixed", "saved"
            elif ref:
                self.voice, self.voice_state, self.voice_origin = "none", "fixing", "first"
                plan = (f"hist-{self.hid}", ref)
            self.bus.emit("live_load", self.snapshot())
            self._emit_status()
            self.cv.notify_all()
        self.llm_agent_interrupt()
        self.llm.reset()
        if plan:
            self._fix_voice(plan, self.sess)

    def delete_history(self, hid):
        history.remove(hid)
        if hid == self.hid:
            self.reset()
        self.bus.emit("history", {})

    def wav_path(self, line_id):
        path = self.root / f"{line_id}.wav"
        return path if line_id in self.lines and path.is_file() else None

    def star(self, line_id, value):
        with self.cv:
            ln = self.lines.get(line_id)
            path = self.wav_path(line_id)
            # 一時 WAV が要るのは、新しく残す時だけ(残した後の★は、残した先のファイルを書き換える)
            if not ln or not (path or ln["stem"]):
                return None
            if value and not ln["stem"]:
                ln["out"] = str(self.config.path("output_dir"))
                ln["stem"] = library.save(ln["out"], path, self._meta(ln))
            out = ln.get("out") or self.config.path("output_dir")   # 残した時のフォルダー(後で保存先を変えても、そこを触る)
            if ln["stem"]:
                if self.config.get()["keep_all"]:
                    library.set_favorite(out, ln["stem"], value)
                elif not value:
                    library.remove(out, ln["stem"])
                    ln["stem"] = None
                else:
                    library.set_favorite(out, ln["stem"], True)
            ln["starred"] = bool(value)
            self._emit_line(ln, "starred", "stem")
            return {"starred": ln["starred"], "stem": ln["stem"], "out": str(out)}

    def _meta(self, ln):
        return {"text": ln["text"], "instructions": _instructions(ln, self._opt()), "voice": ln["voice"], "seed": ln["seed"],
                "speed": ln["speed"], "gen_seconds": ln["gen_seconds"], "audio_seconds": ln["audio_seconds"],
                "created_at": datetime.now().isoformat(timespec="seconds"), "favorite": bool(ln["starred"])}

    # --- 声 -----------------------------------------------------------------------------------------------------
    def new_voice(self):
        """次の 1 本を声なしで作り、それを新しい固定声にする。"""
        with self.cv:
            self._renew_voice()
            self._emit_status()
            self.cv.notify_all()

    def use_voice(self, name):
        """保存した声に切り替える。次の 1 本からその声で読む。"""
        with self.cv:
            self._renew_voice()
            self.voice, self.voice_state, self.voice_origin = name, "fixed", "saved"
            for ln in self.lines.values():
                if ln["state"] == "queued":
                    ln["voice"] = name
            self._emit_status()
            self.cv.notify_all()

    def _renew_voice(self):
        """声を決め直す。シードも変えないと同じ声に戻る。前の声で作り終えた次の 1 本は鳴らさず、
        同じ文を新しい行として同じ位置に入れ直す(文は抜かさない)。"""
        self.voice, self.voice_state, self.voice_origin = "none", "pending", "new"
        self.voice_ref, self.voice_saved = None, ""
        self.seed = self._new_seed()
        ids, again = [], []
        for ln in list(self.lines.values()):
            if ln["state"] == "queued":
                ln["seed"] = self.seed
            elif ln["state"] in ("making", "ready"):
                if ln["state"] == "making":
                    self.engine.cancel(ln["id"])
                ln["state"] = "dropped"
                self._forget_file(ln["id"])
                ids.append(ln["id"])
                copy = dict(ln, id=self._next_id("l"), state="queued", voice=self.voice, seed=self.seed, redo=0,
                            gen_seconds=0.0, audio_seconds=0.0, starred=False, stem=None)
                self.log.insert(self.log.index(ln) + 1, copy)
                again.append((copy, ln["id"]))
        if again:
            self.lines = {e["id"]: e for e in self.log if e["kind"] == "line"}
        if ids:
            self.bus.emit("live_drop", {"ids": ids})
        for copy, after in again:
            self.bus.emit("live_entry", dict(copy, after=after))

    def apply_default_voice(self):
        """まだ何も作っていなければ、設定した既定の声を今のセッションにも反映する。"""
        with self.cv:
            if self.lines or self.voice_state == "fixing":
                return
            self.voice, self.voice_state, self.voice_origin = self._start_voice()
            self.voice_ref = None
            self.how = self._default_how()
            self._emit_status()

    def forget_voice(self, name):
        with self.cv:
            if self.voice == name:
                self.voice, self.voice_state, self.voice_origin = "none", "pending", "first"
                self._emit_status()

    def save_voice(self, name):
        """今のセッションの声を voices/<名前>.wav と .json に残し、実行中のエンジンにも登録する。"""
        with self.cv:
            if self.voice_state != "fixed" or not self.voice_ref:
                raise ValueError(t("live.nothing_to_save"))
            if self.voice_origin == "saved" or self.voice_saved:
                raise ValueError(t("live.already_saved"))
            ref, meta = self.voice_ref, {"instructions": self.how, "seed": self.seed}
        folder = self.config.path("voices_dir")
        try:
            final = voices.save_wav(folder, ref, name, meta)
        except voices.VoiceError as e:
            raise ValueError(str(e))
        try:
            self.engine.add_voice(final, folder / f"{final}.wav")
        except EngineError as e:
            # ファイルは残せた。起動し直せば voices/ から読み込むので、そうして使えるようにする
            log(f"could not register the saved voice: {e}")
            self.bus.emit("live_notice", {"text": t("live.voice_reload", name=final), "kind": "ok"})
            threading.Thread(target=self.engine.restart, daemon=True).start()
        with self.cv:
            self.voice_saved = final
            self._emit_status()
        return final

    def _synth_voice(self):
        """いま読む声の名前。エンジンが動いているのに登録がない声は、最初の 1 本で決め直す。
        参照なしで話せないモデルでは、内蔵の声を使う(設定で声を変えたら、次の 1 本から変わる)。"""
        ready = self.engine.state == "ready"
        if ready and not self.engine.can_none() and (self.voice == "none" or self.voice_origin == "preset"):
            preset = self._preset_voice()
            if self.voice != preset:
                self.voice, self.voice_state, self.voice_origin = preset, "fixed", "preset"
                self.voice_ref = None
                self._emit_status()
            return self.voice
        if (self.voice != "none" and ready and self.voice not in self.engine.voices
                and self.voice not in [v["name"] for v in self.engine.presets()]):
            self.voice, self.voice_state, self.voice_origin = "none", "pending", "first"
            self.voice_ref = None
        return self.voice

    def _fix_voice(self, plan, sess):
        """最初の 1 本の WAV をエンジンに登録して、以後その声で読む。失敗したら次の 1 本でやり直す。"""
        name, ref = plan
        try:
            self.engine.add_voice(name, ref)
            ok = True
        except EngineError as e:
            log(f"could not register the voice: {e}")
            self.bus.emit("live_notice", {"text": t("live.voice_fix_failed", e=e), "kind": "err"})
            ok = False
        with self.cv:
            if sess == self.sess and self.voice_state == "fixing":
                if ok:
                    self.voice, self.voice_state, self.voice_ref = name, "fixed", ref
                    if Path(ref) != history.voice_file(self.hid):
                        try:
                            history.keep_voice(self.hid, ref)   # 履歴から開き直した時も、同じ声で続ける
                        except OSError as e:
                            log(f"could not keep the voice for the history: {e}")
                else:
                    self.voice_state = "pending"
                self._emit_status()
                self.cv.notify_all()

    # --- 行の追加 -----------------------------------------------------------------------------------------------
    def _add_line(self, text, how, speed, pause, steer="", feel=""):
        line = {"id": self._next_id("l"), "kind": "line", "text": text, "how": how, "feel": feel, "speed": speed or 1.0,
                "pause": self._opt()["pause_default"] if pause is None else pause, "state": "queued", "voice": self.voice, "seed": self.seed,
                "redo": 0, "gen_seconds": 0.0, "audio_seconds": 0.0, "starred": False, "stem": None, "from": steer}
        self.lines[line["id"]] = line
        self.log.append(line)
        self.bus.emit("live_entry", dict(line))
        return line

    def _ingest(self, raw, epoch, kind, text):
        opt = self._opt()
        parsed = parse_line(raw, opt)
        if not parsed:
            return False
        with self.cv:
            if epoch != self.epoch or self.closed:
                return False
            how = self.how
            # LLM は頼まれなくても声の説明を書くので、一言(依頼・注文)が声に触れた時だけ、その最初の行で声を決め直す
            opening = kind != "more" and self.agent["n"] == 0
            words = tuning.rx(opt["voice_words"])
            if opening and parsed["voice"] and words and words.search(text) and parsed["voice"] != self.voice_desc:
                self.voice_desc = parsed["voice"]
                # 依頼が声そのものを指定した: 保存した声を使っていても、その説明で声を決め直す
                self.how = opt["instructions_sep"].join(x for x in (parsed["voice"], parsed["how"]) if x)
                self._renew_voice()
                self._emit_status()
            elif parsed["how"]:
                # 声を指定されていなければ、既定の声の文に、LLM の話し方を足す
                base = "" if self.voice_desc else self._default_how()
                self.how = opt["instructions_sep"].join(x for x in (base, parsed["how"]) if x)
            if self.how != how:
                entry = {"id": self._next_id("h"), "kind": "how", "text": self.how}
                self.log.append(entry)
                self.bus.emit("live_entry", entry)
            self.speed = parsed["speed"] or self.speed
            # 上限(max_chars)を超えた行は、文の切れ目で分けて別の音声にする。話し方は引き継ぎ、間は最後の行だけに付ける
            pieces = split_say(parsed["say"], opt["max_chars"])
            for i, piece in enumerate(pieces):
                last = i == len(pieces) - 1
                line = self._add_line(piece, self.how, self.speed, parsed["pause"] if last else 0.0, self._steer_mark, parsed["feel"])
                if self._steer_mark:
                    self._steer_line, self._steer_mark = line["id"], ""
            self._llm_lines += len(pieces)
            self.agent["n"] += 1
            self.agent["state"] = "writing"
            self.rewriting = False
            self._emit_status()
            self.cv.notify_all()
        return True

    # --- エージェント ---------------------------------------------------------------------------------------------
    def _stock(self):
        return sum(1 for ln in self.lines.values() if ln["state"] in LIVE)

    def _want_more(self):
        return (self.running and self.turns > 0 and self.auto_ok and self._stock() <= self._opt()["stock_low"])

    def _agent_loop(self):
        while not self.closed:
            with self.cv:
                while not self.closed and (self._making or not (self._cmd or self._want_more())):
                    self.cv.wait(0.5)
                if self.closed:
                    return
                cmd = self._cmd or ("more", "", ("", "", ""))
                self._cmd = None
                epoch = self.epoch
                self.agent.update(state="thinking", n=0, error="")
                self._emit_status()
            self._run_turn(cmd, epoch)

    def _run_turn(self, cmd, epoch):
        kind, text, heard = cmd
        cancelled = lambda: self.epoch != epoch or self.closed
        agent, count = None, 0
        try:
            agent = self.llm.agent()
            if agent.name in ("claude", "codex") and agent.state != "ready":
                with self.cv:
                    self.agent["state"] = "waking"
                    self._emit_status()
            cfg = self.config.get()
            custom, tune = cfg["prompts"], cfg["tuning"]
            # Claude Code / Codex は 1 ターンごとにそれまでの会話を全部読み直すので、長く話すほど消費が増える。
            # 決めた往復数に達したら新しいセッションにして、これまでの流れ(_handover)だけを渡す
            limit = tune["history_turns"]
            # 読み上げる言語が変わったら(モデルや設定を変えた)、指示文ごと新しいセッションにする
            speak = self.speak_language()
            changed = self._speak_seen not in ("", speak)
            self._speak_seen = speak
            if agent.name in ("claude", "codex") and (changed or (limit and agent.turns_sent >= limit)):
                agent.reset()
            message = {"request": lambda: prompts.request(custom, text, tune),
                       "steer": lambda: prompts.steer(custom, text, heard[0], tune, heard[1], heard[2]),
                       "more": lambda: prompts.more(custom, tune)}[kind]()

            def on_search():
                # 検索を始めた(状態表示に「調べています」を出す)。書き始めたら on_text / _ingest が状態を戻す
                with self.cv:
                    if epoch == self.epoch and self.agent["state"] in ("waking", "thinking", "writing"):
                        self.agent["state"] = "searching"
                        self._emit_status()

            for attempt in (1, 2):
                splitter = LineSplitter()

                def on_text(chunk, splitter=splitter):
                    nonlocal count
                    with self.cv:
                        before = self.agent["state"]
                        if before in ("waking", "searching"):
                            self.agent["state"] = "thinking"
                            if before == "searching":
                                self._emit_status()
                    for ln in splitter.feed(chunk):
                        count += self._ingest(ln, epoch, kind, text)

                agent.turn(message, on_text, cancelled, self._handover, on_search)
                for ln in splitter.flush():
                    count += self._ingest(ln, epoch, kind, text)
                if count or cancelled():
                    break
                message = prompts.retry(custom, tune)
            with self.cv:
                if not count and not cancelled():
                    self.agent["error"] = t("live.llm_unreadable")
                    self._fail_turn(cmd)
        except LLMError as e:
            with self.cv:
                if agent is not None and agent.retired:
                    # LLM の種類・モデル・文面の変更で、書いている途中のセッションが閉じられた。失敗にせず、新しいセッションで続ける
                    # (書けた行はそのまま鳴る。何も書けていない依頼は、これまでの流れを添えてやり直す)
                    if not count and kind != "more" and epoch == self.epoch and self._cmd is None:
                        self._cmd = cmd
                elif epoch == self.epoch:
                    self.agent["error"] = str(e)
                    self._fail_turn(cmd)
        finally:
            with self.cv:
                self.agent["state"] = "idle"
                if epoch == self.epoch:
                    self.rewriting = False
                self._emit_status()
                self.cv.notify_all()

    def _fail_turn(self, cmd):
        self.auto_ok, self._retry = False, cmd
        self.steer, self._steer_mark, self._steer_line = "", "", None

    # --- 音声化 -------------------------------------------------------------------------------------------------
    def _make_wait(self):
        """次の 1 本を作り始めるまでの秒数(0 なら今すぐ)。作らない時は None。
        先読みは 1 本だけ。今鳴っている声の残りが短くなってから作り始める。
        止めている間も、鳴らす 1 本は用意しておく(▶ ですぐ鳴らすため)。声が作れていない時と、音声モデルが動いていない時は作らない。"""
        if self.voice_state == "fixing" or (not self.running and (self.tts_error or self.engine.state != "ready")):
            return None
        if any(ln["state"] in ("making", "ready") for ln in self.lines.values()):
            return None
        if not any(ln["state"] == "queued" for ln in self.lines.values()):
            return None
        playing = next((ln for ln in self.lines.values() if ln["state"] == "playing"), None)
        if not playing:
            return 0.0
        # 途中の一言への返事(その最初の行)は、今の声の終わりを待たずにすぐ作る。できたら画面が今の声を切って差し替える
        if next(ln for ln in self.log if ln["kind"] == "line" and ln["state"] == "queued")["from"]:
            return 0.0
        left = playing.get("started_at", 0) + playing["audio_seconds"] - time.time()
        opt = self._opt()
        return max(0.0, left - max(opt["lead_min"], opt["lead_factor"] * self._last_gen))

    def _render(self, line, payload):
        """1 本を作り、検査して前後を切る。崩れていたらシードを変えて最大 max_redo 回(設定)作り直す。
        新しい声の最初の 1 本(voice が none)は、発話が切れた直後の響きも測り、長ければ(立体音響のように聞こえる)
        シードを変えて最大 reverb_max_redo 回作り直す。使い切ったら、いちばん響きの少ないものを使う。
        (WAV, end, エラー文, 中断, 作り直した回数, 報告)"""
        redo = 0
        opt = self._opt()
        # 響きの検査は毎行。新しい声の最初の 1 本は reverb_max_redo 回まで、決まった声の行は 2 回まで作り直す(遅れすぎないように)
        # 響き(エコー)が乗るのは Irodori-TTS の癖。ほかのモデルの声は、響きでは作り直さない
        check = opt["reverb_check"] and self.engine.model.get("architecture", "irodori-tts") == "irodori-tts"
        limit = opt["reverb_max_redo"] if payload["voice"] == "none" else min(2, opt["reverb_max_redo"])
        if line["from"] and payload["voice"] != "none":
            limit = 0   # 途中の一言への返事は、作り直さずにすぐ鳴らす(待たせない)
        tries, best = 0, None   # 残響で作り直した回数と、ここまででいちばん響きの少なかったもの
        while True:
            try:
                pcm, end = self.engine.synthesize(payload, tag=line["id"])
            except Cancelled:
                return None, {}, "", True, redo, {}
            except EngineError as e:
                return None, {}, str(e), False, redo, {}
            rate = self.engine.sample_rate
            out, info = clip.inspect(pcm, rate, line["text"], end.get("stop", "complete"), opt, self.speak_language())
            # 読み間違いの検査は、日本語の聞き取りモデル(ReazonSpeech)で聞くので、日本語の時だけ
            if not info["reasons"] and self.config.get()["asr_check"] and self.speak_language() == "ja":
                match = self.checker.match(out, rate, line["text"])
                if match is not None and match < opt["asr_min_match"]:
                    info["reasons"].append(f"asr={match:.2f}")
            log(f"clip {line['id']} try={redo + 1} raw={info['raw']:.2f}s kept={info['kept']:.2f}s "
                f"cut={info['head']:.0f}+{info['tail']:.0f}ms gain={info['gain']:.2f} "
                f"{'NG ' + ','.join(info['reasons']) if info['reasons'] else 'ok'}")
            if not info["reasons"] and check:
                level = clip.reverb_db(pcm, rate, opt)
                log(f"reverb {line['id']} {'n/a' if level is None else f'{level:.1f}dB'} limit={opt['reverb_db']:.0f}dB try={tries + 1}")
                if level is not None and level > opt["reverb_db"]:
                    if best is None or level < best[0]:
                        best = (level, clip.wav_bytes(out, rate), end, info)
                    if tries < limit:
                        tries += 1
                        if not self._reverb_retry(line, tries, limit):
                            return None, {}, "", True, redo + tries, info
                        payload["seed"] = random.randrange(2**31)
                        continue
                    wav, end, info = best[1], best[2], best[3]   # 使い切った: いちばん響きの少ないもの
                    return wav, end, "", False, redo + tries, info
            if not info["reasons"] or redo >= opt["max_redo"]:
                broken = [r for r in info["reasons"] if r in ("empty", "silent") or r.startswith("stop=")]
                if broken:
                    return None, {}, t("live.bad_clip", reason=", ".join(broken)), False, redo + tries, info
                return clip.wav_bytes(out, rate), end, "", False, redo + tries, info
            redo += 1
            with self.cv:
                if line["state"] != "making":
                    return None, {}, "", True, redo + tries, info
            payload["seed"] = random.randrange(2**31)

    def _reverb_retry(self, line, n, limit):
        """残響が長くて新しい声を作り直す間、状態表示に出す。まだ作っている最中なら True。"""
        with self.cv:
            if line["state"] != "making":
                return False
            self.voice_try = (n, limit)
            self._emit_status()
        return True

    def _tts_loop(self):
        while not self.closed:
            with self.cv:
                while not self.closed and self._make_wait() != 0.0:
                    delay = self._make_wait()
                    self.cv.wait(0.4 if delay is None else min(max(delay, 0.05), 0.4))
                if self.closed:
                    return
                line = next(ln for ln in self.log if ln["kind"] == "line" and ln["state"] == "queued")
                sess, voice = self.sess, self._synth_voice()
                line.update(state="making", voice=voice)
                self._making = line["id"]
                opt = self._opt()
                payload = {"text": line["text"], "voice": voice, "steps": opt["steps"], "seed": line["seed"], "keep_tail": False,
                           "language": self.speak_language()}
                instructions = _instructions(line, opt)
                if instructions:
                    payload["instructions"] = instructions
                    # 文章の全文は書かない。話し方(Irodori への指示)は読み上げる文ではないので、そのまま残す
                    log(f"instructions {line['id']} {instructions}")
                speed = round(line["speed"] * float(self.config.get().get("speech_speed", 1.0)), 3)
                if speed != 1.0:
                    payload["speed"] = speed
                self._emit_line(line, "state", "voice")
                self._emit_status()
            started = time.time()
            wav, end, error, cancelled, redo, info = self._render(line, payload)
            spent = time.time() - started
            plan = None
            with self.cv:
                self._making = None
                self.voice_try = None
                if line["state"] != "making" or self.lines.get(line["id"]) is not line:
                    self._emit_status()
                    continue
                if cancelled:
                    line["state"] = "queued"
                elif error:
                    line["state"] = "failed"
                    self.fails += 1
                    self.tts_error = error
                    if self.fails >= opt["max_fails"]:
                        self.running = False
                else:
                    self._last_gen = spent / (redo + 1)
                    path = self.root / f"{line['id']}.wav"
                    try:
                        self.root.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(wav)
                    except OSError as e:
                        # 一時フォルダーに書けない(容量・権限)。この行は失敗にして、ループは止めない
                        log(f"could not write the WAV: {e}")
                        line["state"] = "failed"
                        self.tts_error = str(e)
                        self.running = False
                        self._emit_line(line, "state")
                        self._emit_status()
                        self.cv.notify_all()
                        continue
                    self.fails, self.tts_error = 0, ""
                    line.update(state="ready", gen_seconds=round(spent, 3), audio_seconds=round(info["kept"], 3), redo=redo,
                                seed=int(end.get("seed", line["seed"])))
                    if voice == "none" and self.voice == "none" and self.voice_state == "pending" and sess == self.sess:
                        self._voice_n += 1
                        name = f"live-{self.root.name}-{self._voice_n}"
                        ref = self.root / f"{name}.wav"
                        try:
                            shutil.copyfile(path, ref)
                            self.voice_state = "fixing"
                            plan = (name, ref)
                        except OSError as e:
                            log(f"could not keep the voice reference WAV: {e}")
                self._emit_line(line, "state", "gen_seconds", "audio_seconds", "stem", "redo")
                self._emit_status()
                self.cv.notify_all()
            if plan:
                self._fix_voice(plan, sess)
