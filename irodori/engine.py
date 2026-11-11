import atexit
import base64
import collections
import json
import os
import queue
import re
import io
import random
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

from .config import IS_MAC, IS_WINDOWS, ROOT, find_audio, find_speech, official_speech
from .i18n import t

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


class EngineError(Exception):
    pass


class Cancelled(EngineError):
    pass


def log(message):
    """サーバーの標準エラーに 1 行残す。文章の全文は書かない。"""
    if sys.stderr:
        print(f"[engine] {message}", file=sys.stderr, flush=True)


def _make_kill_job():
    """Windows: 本アプリが落ちても子プロセスが残らないよう Job Object に入れる。"""
    if not IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class BasicLimit(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in
                        ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [("Basic", BasicLimit), ("Io", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        job = k32.CreateJobObjectW(None, None)
        info = ExtendedLimit()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        return k32, job
    except Exception:
        return None


class Request:
    def __init__(self, rid, tag):
        self.id, self.tag = rid, tag
        self.q = queue.Queue()
        self.cancelled = False


# Kataribe が話せる speech worker の protocol(speech.cpp の docs/worker.md)。3 は v0.9 系、4 は v0.10 系
# (4 で増えたのは音声認識の keywords だけで、音声合成の受け渡しは同じ)
PROTOCOLS = (3, 4)


class Engine:
    """`speech worker` を子プロセスで常駐させ、stdin/stdout の JSON Lines で合成・声の登録・中断をする。"""

    def __init__(self, config, bus):
        self.config, self.bus = config, bus
        self.state, self.error, self.missing = "stopped", "", ""
        self.since = time.time()
        self.voices = []
        self.sample_rate = 48000
        self.model = {}   # 読み込んだモデルの中身(ready の model): 話せる言語、内蔵の声、受け付ける設定
        self.proc = None
        self.kind = "speech"   # いま動かしているエンジン: "speech"(speech.cpp の worker)か "audio"(audio.cpp のサーバー)
        self._port = 0
        self._boot_id = 0
        self._ids = 0
        self._added = {}
        self._pending = {}
        self._current = None
        self._bad_exe = ""   # protocol が合わなかった speech の実行ファイル
        self._no_files = ""  # 登録した声(録音から作る声)を受け付けなかったモデル。次からは声を渡さずに起動する
        self._tail = collections.deque(maxlen=12)
        self._lock = threading.RLock()
        self._wlock = threading.Lock()
        self._synth_lock = threading.Lock()
        self._job = _make_kill_job()
        atexit.register(self.stop)

    def snapshot(self):
        return {"state": self.state, "error": self.error, "missing": self.missing, "since": self.since,
                "voices": list(self.voices), "model": self.model_summary()}

    # --- 読み込んだモデルでできること(モデルごとに違う) ---------------------------------------------
    def options(self):
        return {o["name"]: o for o in self.model.get("options") or []}

    def presets(self):
        """モデルに入っている声(Irodori の "none" = 参照なしは除く)。[{name, language, gender, description}]"""
        mine = set(self.voices)   # voices/ から登録した声は、モデルの声ではなく「登録した声」として別に出す
        return [v for v in self.model.get("voices") or [] if v.get("name") != "none" and v.get("name") not in mine]

    def can_none(self):
        """参照の声なしで話せるか(Irodori-TTS)。話せなければ、内蔵の声か登録した声が要る。"""
        choices = (self.options().get("voice") or {}).get("choices") or []
        return not self.model or "none" in choices

    def model_summary(self):
        m = self.model
        return {"name": m.get("name", ""), "languages": m.get("languages") or [], "presets": self.presets(),
                "voice_files": bool(m.get("voice_files", True)) if m else True,
                "instructions": "instructions" in self.options() if m else True}

    def _set(self, state, error="", missing=""):
        """missing: "speech"(実行ファイルが無い)、"model"(モデルが無い)、audio.cpp を選んでいる時は "audio" / "audio_model"。
        画面に用意のしかたを出す。"""
        self.state, self.error, self.missing, self.since = state, error, missing, time.time()
        self.bus.emit("engine", self.snapshot())

    def voice_files(self):
        """voices/ の声。同名なら声ファイル(*.voice.gguf)を優先する。{名前: パス}"""
        folder = self.config.path("voices_dir")
        found = {}
        if folder.is_dir():
            for f in sorted(folder.glob("*.wav")):
                found[f.stem] = f
            # 声ファイル(*.voice.gguf)は speech.cpp だけが読める。audio.cpp では WAV を参照の声として渡す
            if self.kind != "audio":
                for f in sorted(folder.glob("*.voice.gguf")):
                    found[f.name[: -len(".voice.gguf")]] = f
        return found

    def start(self):
        with self._lock:
            if self.proc and self.proc.poll() is None:
                return
            cfg = self.config.get()
            self.kind = "audio" if cfg["tts_backend"] == "audio" else "speech"
            if self.kind == "audio":
                return self._start_audio(cfg)
            exe = Path(cfg["speech_exe"]).expanduser() if cfg["speech_exe"] else None
            if not cfg["speech_exe"]:
                # 未設定なら探し直す(起動後に speech.cpp を入れて「再確認」を押した時)。入れたパスは消さない
                found = find_speech()
                if found:
                    self.config.update({"speech_exe": found})
                    exe = Path(found)
            if self._bad_exe and str(cfg["speech_exe"]) == self._bad_exe:
                alt = official_speech() or find_speech()
                if alt and alt != self._bad_exe:
                    self.config.update({"speech_exe": alt})
                    exe = Path(alt)
            if not exe or not exe.is_file():
                self._set("error", t("eng.missing", path=cfg["speech_exe"] or t("eng.unset")), missing="speech")
                return
            # モデルが無いまま worker を起動すると、黙って約 1.9GB を取りに行く。先に画面へ「セットアップ」を出す
            from .installer import model_present
            if model_present(cfg) is False:
                self._set("error", t("eng.no_model", model=cfg["model"]), missing="model")
                return
            try:
                self.config.path("voices_dir").mkdir(parents=True, exist_ok=True)
            except OSError as e:
                self._set("error", t("eng.start_failed", e=e))
                return
            files = self.voice_files()
            files.update({n: Path(p) for n, p in self._added.items() if Path(p).is_file()})
            if cfg["model"] == self._no_files:
                files = {}
            self.voices = list(files)
            cmd = [str(exe), "worker", cfg["model"], "--no-warmup", "--device", cfg["device"] or "auto"]
            for name, f in files.items():
                cmd += ["--add-voice", f"{name}={f}"]
            env = dict(os.environ)
            if cfg["model_dir"]:
                env["SPEECH_MODEL_DIR"] = str(Path(cfg["model_dir"]).expanduser())
            flags = (CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP) if IS_WINDOWS else 0
            self._tail.clear()
            try:
                self.proc = subprocess.Popen(cmd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                             stderr=subprocess.PIPE, creationflags=flags,
                                             start_new_session=not IS_WINDOWS)
            except OSError as e:
                self._set("error", t("eng.start_failed", e=e))
                return
            if self._job:
                k32, job = self._job
                k32.AssignProcessToJobObject(job, int(self.proc._handle))
            self._boot_id += 1
            boot_id, proc = self._boot_id, self.proc
            self._set("starting")
            log(f"worker start voices={len(files)}")
            threading.Thread(target=self._read_stderr, args=(proc,), daemon=True).start()
            threading.Thread(target=self._read_stdout, args=(boot_id, proc), daemon=True).start()
            threading.Thread(target=self._watchdog, args=(boot_id,), daemon=True).start()

    # --- audio.cpp(audiocpp_server を子プロセスで常駐させ、HTTP で 1 本ずつ作らせる) -------------------------------
    def _start_audio(self, cfg):
        exe = cfg["audio_exe"] or find_audio()
        if exe and not cfg["audio_exe"]:
            self.config.update({"audio_exe": exe})
        exe = Path(exe).expanduser() if exe else None
        if not exe or not exe.is_file():
            self._set("error", t("eng.audio_missing", path=cfg["audio_exe"] or t("eng.unset")), missing="audio")
            return
        model = Path(cfg["audio_model"]).expanduser() if cfg["audio_model"] else None
        if model and not model.is_absolute():
            model = ROOT / model
        if not model or not model.is_file():
            self._set("error", t("eng.audio_no_model", path=cfg["audio_model"] or t("eng.unset")), missing="audio_model")
            return
        model = model.resolve()
        try:
            self.config.path("voices_dir").mkdir(parents=True, exist_ok=True)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                self._port = sock.getsockname()[1]
            conf = Path(tempfile.gettempdir()) / f"kataribe-audio-{os.getpid()}.json"
            conf.write_text(json.dumps({"host": "127.0.0.1", "port": self._port, "lazy_load": False, "max_loaded_models": 1, "models": [
                {"id": "tts", "family": "irodori_tts", "task": "tts", "mode": "offline", "path": model.as_posix()}]}), encoding="utf-8")
        except OSError as e:
            self._set("error", t("eng.start_failed", e=e))
            return
        files = self.voice_files()
        files.update({n: Path(p) for n, p in self._added.items() if Path(p).is_file() and str(p).lower().endswith(".wav")})
        self._added = {n: str(p) for n, p in files.items()}
        self.voices = list(files)
        backend = audio_backend(str(exe))
        cmd = [str(exe), "--config", str(conf), "--no-ui", "--backend", backend]
        if str(cfg["device"]).isdigit() and backend != "cpu":
            cmd += ["--device", str(cfg["device"])]
        flags = (CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP) if IS_WINDOWS else 0
        self._tail.clear()
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                         creationflags=flags, start_new_session=not IS_WINDOWS)
        except OSError as e:
            self._set("error", t("eng.start_failed", e=e))
            return
        self.adopt(self.proc)
        self._boot_id += 1
        boot_id, proc = self._boot_id, self.proc
        self._set("starting")
        log(f"audio.cpp start backend={backend} voices={len(files)}")
        threading.Thread(target=self._read_stderr, args=(proc,), daemon=True).start()
        threading.Thread(target=self._boot_audio, args=(boot_id, proc, model), daemon=True).start()
        threading.Thread(target=self._watchdog, args=(boot_id,), daemon=True).start()

    def _boot_audio(self, boot_id, proc, model):
        """サーバーが応えるまで待ち、モデルでできることを決めて、1 本読ませて暖める。"""
        while proc.poll() is None and boot_id == self._boot_id:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self._port}/health", timeout=2).read()
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.3)
        if boot_id != self._boot_id:
            return
        if proc.poll() is not None:
            self._set("error", t("eng.exited", code=proc.poll(), tail=self._tail_text()))
            return
        # Irodori-TTS(日本語)。参照なし("none")でも、WAV を参照の声にしても話せる。話し方(instruct)も受け付ける
        self.model = {"name": model.stem, "architecture": "irodori-tts", "languages": ["ja"], "sample_rate": 48000, "voices": [],
                      "voice_files": True, "meanflow": "-mf" in model.stem.lower(),
                      "options": [{"name": "voice", "choices": ["none"]}] + [{"name": n} for n in ("steps", "seed", "instructions", "speed", "language", "keep_tail")]}
        self.sample_rate = 48000
        threading.Thread(target=self._warm, args=(boot_id,), daemon=True).start()
        proc.wait()
        if boot_id == self._boot_id and self.state != "error":
            self._set("error", t("eng.exited", code=proc.poll(), tail=self._tail_text()))

    def _audio_request(self, fields, out):
        """1 本を作らせる(別スレッドで動く)。結果は out に入れる: {"pcm", "end"} か {"error"}。"""
        voice = fields.get("voice") or "none"
        options = {"seed": int(fields["seed"]) if fields.get("seed") is not None else random.randrange(2**31)}
        body = {"model": "tts", "input": fields["text"], "options": options}
        try:
            if voice == "none":
                options["no_ref"] = True
            else:
                body["voice_ref"] = Path(self._added[voice]).as_posix()
            if fields.get("instructions"):
                options["instruct"] = fields["instructions"]
            if fields.get("steps") and self.model.get("meanflow"):
                options["num_inference_steps"] = int(fields["steps"])   # 段数の設定は MeanFlow 版のもの。ほかはモデルの既定に任せる
            if fields.get("speed") and float(fields["speed"]) != 1.0:
                options["duration_scale"] = round(1.0 / float(fields["speed"]), 3)
            req = urllib.request.Request(f"http://127.0.0.1:{self._port}/v1/audio/speech", json.dumps(body).encode("utf-8"),
                                         {"Content-Type": "application/json"})
            with wave.open(io.BytesIO(urllib.request.urlopen(req, timeout=300).read())) as w:
                pcm = w.readframes(w.getnframes())
            out.update(pcm=pcm, end={"seed": options["seed"], "stop": "complete", "samples": len(pcm) // 2})
        except urllib.error.HTTPError as e:
            try:
                message = json.loads(e.read())["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = str(e)
            out["error"] = message
        except (OSError, wave.Error, EOFError, KeyError) as e:
            out["error"] = str(e) or t("eng.error")

    def _call_audio(self, kind, fields, tag):
        if kind == "add_voice":
            if not str(fields["path"]).lower().endswith(".wav") or not Path(fields["path"]).is_file():
                raise EngineError(t("eng.audio_wav_only"))
            return b"", {}
        with self._synth_lock:
            req = Request("", tag)
            self._current = req
            out = {}
            worker = threading.Thread(target=self._audio_request, args=(fields, out), daemon=True)
            boot_id = self._boot_id
            worker.start()
            try:
                while worker.is_alive():
                    worker.join(0.05)
                    # サーバーは作り始めた 1 本を止められない。待つのをやめて捨てる(次の 1 本はサーバー側で順番を待つ)
                    if req.cancelled:
                        raise Cancelled(t("eng.cancelled"))
                    if boot_id != self._boot_id:
                        raise EngineError(self.error or t("eng.ended"))
                if "error" in out:
                    raise EngineError(out["error"])
                return out["pcm"], out["end"]
            finally:
                self._current = None

    def _read_stderr(self, proc):
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if line:
                self._tail.append(line[:240])

    def _tail_text(self):
        return " / ".join(list(self._tail)[-3:])

    def _watchdog(self, boot_id):
        time.sleep(600)
        if boot_id == self._boot_id and self.state in ("starting", "warming"):
            self._set("error", t("eng.boot_timeout"))

    def _read_stdout(self, boot_id, proc):
        for raw in iter(proc.stdout.readline, b""):
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if boot_id != self._boot_id:
                continue
            kind = msg.get("type")
            if kind == "ready":
                protocol = msg.get("protocol")
                if protocol not in PROTOCOLS:
                    # 話し方の違う speech.cpp。古ければ「セットアップ」で新しい版を入れられる。新しすぎれば Kataribe の更新が要る
                    older = not isinstance(protocol, int) or protocol < min(PROTOCOLS)
                    need = " / ".join(map(str, PROTOCOLS))
                    self._set("error", t("eng.protocol_old" if older else "eng.protocol_new", have=protocol, need=need,
                                         version=msg.get("version") or "?"), missing="speech" if older else "")
                    if older:
                        self._bad_exe = str(self.config.get()["speech_exe"])
                    self.stop_proc(proc)
                    continue
                self.model = msg.get("model") or {}
                self.sample_rate = int(self.model.get("sample_rate") or 48000)
                threading.Thread(target=self._warm, args=(boot_id,), daemon=True).start()
            elif kind == "fatal":
                err = msg.get("error") or {}
                # モデルのファイルを読めない(壊れている、途中まで): 「セットアップ」で取り直せるようにする
                is_model = err.get("code") == "model_file" and not err.get("option")
                if err.get("code") == "unsupported" and self.voices and self.config.get()["model"] != self._no_files:
                    # 内蔵の声だけで話すモデル(Qwen3-TTS など)は、登録した声を受け付けない。声を渡さずに起動し直す
                    self._no_files = self.config.get()["model"]
                    log(f"model takes no voice files, restarting without them: {err.get('message')}")
                    threading.Thread(target=self.restart, daemon=True).start()
                    continue
                self._set("error", err.get("message") or t("eng.fatal"), missing="model" if is_model else "")
            else:
                req = self._pending.get(msg.get("id"))
                if req:
                    req.q.put(msg)
        if boot_id != self._boot_id:
            return
        for req in list(self._pending.values()):
            req.q.put({"type": "eof"})
        if self.state != "error":
            tail = self._tail_text()
            old = self.state == "starting" and re.search(r"no subcommand|unknown (?:sub)?command|unrecognized|unknown option", tail, re.I)
            if old:
                self._bad_exe = str(self.config.get()["speech_exe"])
            self._set("error", t("eng.exited", code=proc.poll(), tail=tail), missing="speech" if old else "")

    def stop_proc(self, proc):
        """この worker だけを止める(状態の表示は変えない)。"""
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def _warm(self, boot_id):
        self._set("warming")
        try:
            # 声はモデルに入っているもの(参照なしで話せるなら none)。文はそのモデルが話せる言語で
            voice = "none" if self.can_none() else (self.presets() or [{"name": "none"}])[0]["name"]
            langs = self.model.get("languages") or ["ja"]
            req = {"text": "あ" if "ja" in langs else "Hi.", "voice": voice, "steps": 4}
            known = self.options()
            self._call("synthesize", {k: v for k, v in req.items() if k == "text" or not known or k in known})
        except EngineError as e:
            if boot_id == self._boot_id and self.state != "error":
                self._set("error", t("eng.warm_failed", e=e))
            return
        if boot_id == self._boot_id and self.state == "warming":
            self._set("ready")
            log("worker ready")

    def stop(self):
        with self._lock:
            self._boot_id += 1
            proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except OSError:
                pass
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            (Path(tempfile.gettempdir()) / f"kataribe-audio-{os.getpid()}.json").unlink()   # audio.cpp に渡した一時の設定
        except OSError:
            pass
        for req in list(self._pending.values()):
            req.q.put({"type": "eof"})
        if self.state != "stopped":
            self._set("stopped")

    def restart(self):
        self.stop()
        self.start()

    def wait_ready(self, timeout=300, abort=lambda: False):
        end = time.time() + timeout
        while time.time() < end and not abort():
            if self.state == "ready":
                return
            if self.state in ("stopped", "error"):
                raise EngineError(self.error or t("eng.stopped"))
            time.sleep(0.2)
        if not abort():
            raise EngineError(t("eng.not_in_time"))

    # --- worker とのやり取り ---------------------------------------------------------------------------------------
    def _write(self, obj):
        with self._wlock:
            proc = self.proc
            if not proc or proc.poll() is not None or proc.stdin.closed:
                raise EngineError(t("eng.not_running"))
            try:
                proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                proc.stdin.flush()
            except OSError as e:
                raise EngineError(t("eng.write_failed", e=e))

    def _call(self, kind, fields, tag=None, timeout=120):
        """1 つの要求を送り、終端メッセージまで待つ。(PCM, end メッセージ) を返す。要求は 1 本ずつ直列に流す。"""
        if self.kind == "audio":
            return self._call_audio(kind, fields, tag)
        with self._synth_lock:
            self._ids += 1
            req = Request(f"r{self._ids}", tag)
            self._pending[req.id] = req
            self._current = req
            pcm = bytearray()
            try:
                self._write({"type": kind, "id": req.id, **fields})
                while True:
                    try:
                        msg = req.q.get(timeout=timeout)
                    except queue.Empty:
                        raise EngineError(t("eng.no_reply"))
                    kind_ = msg.get("type")
                    if kind_ == "chunk":
                        pcm += base64.b64decode(msg["pcm"])
                    elif kind_ == "end":
                        return bytes(pcm), msg
                    elif kind_ == "cancelled":
                        raise Cancelled(t("eng.cancelled"))
                    elif kind_ == "error":
                        raise EngineError((msg.get("error") or {}).get("message") or t("eng.error"))
                    elif kind_ == "eof":
                        raise EngineError(self.error or t("eng.ended"))
            finally:
                self._pending.pop(req.id, None)
                self._current = None

    def cancel(self, tag):
        """tag の要求が実行中なら中断を頼む(別の要求には触れない)。"""
        req = self._current
        if req and req.tag == tag:
            req.cancelled = True
            if self.kind == "audio":
                return
            try:
                self._write({"type": "cancel", "id": req.id})
            except EngineError:
                pass

    def synthesize(self, payload, tag=None):
        """PCM(16bit モノラル、self.sample_rate)と end メッセージ(seed・stop など)を返す。"""
        self.wait_ready()
        # モデルが受け付けない設定は送らない(送ると、その要求ごと断られる)
        known = self.options()
        if known:
            payload = {k: v for k, v in payload.items() if k == "text" or k in known}
        pcm, end = self._call("synthesize", payload, tag)
        log(f"synthesize voice={payload.get('voice')} chars={len(payload.get('text', ''))} samples={end.get('samples')}")
        return pcm, end

    def adopt(self, proc):
        """別の子プロセスも、本アプリが落ちた時に一緒に終わるようにする。"""
        if self._job:
            k32, job = self._job
            k32.AssignProcessToJobObject(job, int(proc._handle))

    def add_voice(self, name, path):
        """実行中の worker に声(WAV か声ファイル)を登録する。再起動はしない。"""
        self.wait_ready()
        self._call("add_voice", {"name": name, "path": str(path)})
        self._added[name] = str(path)
        if name not in self.voices:
            self.voices.append(name)
        log(f"add_voice {name}")
        self.bus.emit("engine", self.snapshot())


def _audio_devices(audio_exe):
    """audio.cpp が使えるデバイス(--list-devices --json)。読めなければ空。"""
    try:
        out = subprocess.run([audio_exe, "--list-devices", "--json"], capture_output=True, timeout=30,
                             creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0).stdout.decode("utf-8", "replace")
        return json.loads(out[out.index("{"):])["devices"]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return []


def audio_backend(audio_exe):
    """その audio.cpp で使うバックエンド。GPU があればそのバックエンド(CUDA 版なら cuda、Vulkan 版なら vulkan)、無ければ cpu。
    一覧を読めない古い版は、これまでどおり Mac は metal、ほかは vulkan。"""
    found = _audio_devices(audio_exe)
    if not found:
        return "metal" if IS_MAC else "vulkan"
    gpu = next((d for d in found if d.get("type") == "gpu" and d.get("backend")), None)
    return gpu["backend"] if gpu else "cpu"


def list_devices(speech_exe, audio_exe=""):
    if audio_exe:
        # audio.cpp: 番号で選ぶ(使うバックエンドの GPU だけ出す)
        backend = audio_backend(audio_exe)
        return [{"name": str(d["index"]), "kind": "gpu", "info": d.get("description", "")}
                for d in _audio_devices(audio_exe) if d.get("type") == "gpu" and d.get("backend") == backend]
    if not speech_exe or not Path(speech_exe).is_file():
        return []
    try:
        out = subprocess.run([speech_exe, "devices"], capture_output=True, timeout=30,
                             creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return []
    devices = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[1] in ("gpu", "cpu"):
            devices.append({"name": parts[0], "kind": parts[1], "info": parts[2]})
    return devices
