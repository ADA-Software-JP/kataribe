"""読み間違いの検査(設定でオンにした時だけ)。reazonspeech-v2 を別の worker で動かし、聞き取った文と読ませた文を文字単位で比べる。"""
import atexit
import base64
import difflib
import json
import os
import subprocess
import threading
import time
from pathlib import Path

from .config import IS_WINDOWS
from .engine import CREATE_NO_WINDOW, log

MODEL = "reazonspeech-v2"
CHUNK = 48000
WAIT_LIMIT = 120  # 起動と 1 本の聞き取りを待つ上限(秒)
RETRY_AFTER = 60  # 聞き取りが止まった後、もう一度試すまでの秒数


def _norm(text):
    return "".join(ch for ch in text if ch.isalnum())


class Checker:
    def __init__(self, config, engine):
        self.config, self.engine = config, engine
        self.proc, self.broken = None, False
        self.broken_at = 0.0
        self.on_broken = lambda: None   # 止まった時に画面へ知らせる(live が差し込む)
        self.lock = threading.Lock()
        self._n = 0
        atexit.register(self.close)

    def _kill_stuck(self):
        proc = self.proc
        if proc and proc.poll() is None:
            proc.kill()

    def close(self):
        proc, self.proc = self.proc, None
        if proc and proc.poll() is None:
            proc.terminate()

    def _start(self):
        cfg = self.config.get()
        exe = Path(cfg["speech_exe"]).expanduser() if cfg["speech_exe"] else None
        if not exe or not exe.is_file():
            raise OSError("no speech executable")
        env = dict(os.environ)
        if cfg["model_dir"]:
            env["SPEECH_MODEL_DIR"] = str(Path(cfg["model_dir"]).expanduser())
        flags = CREATE_NO_WINDOW if IS_WINDOWS else 0
        self.proc = subprocess.Popen([str(exe), "worker", MODEL, "--no-warmup", "--device", cfg["device"] or "auto"], env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags)
        self.engine.adopt(self.proc)
        first = json.loads(self.proc.stdout.readline() or b"{}")
        if first.get("type") != "ready":
            raise OSError("the transcription worker did not start")

    def _send(self, obj):
        self.proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))

    def match(self, pcm, rate, text):
        """聞き取りと読ませた文の一致度(0〜1)。使えない時は None。"""
        if self.broken:
            if time.time() - self.broken_at < RETRY_AFTER:
                return None
            self.broken = False   # しばらく経ったら、もう一度試す
        with self.lock:
            # 応答しなくなった worker は止める(読み取りが空になり、下の except で検査をやめて音声化は続く)
            watchdog = threading.Timer(WAIT_LIMIT, self._kill_stuck)
            watchdog.daemon = True
            watchdog.start()
            try:
                if not self.proc or self.proc.poll() is not None:
                    self._start()
                self._n += 1
                rid = f"a{self._n}"
                for seq, i in enumerate(range(0, len(pcm), CHUNK)):
                    self._send({"type": "chunk", "id": rid, "seq": seq, "pcm": base64.b64encode(pcm[i: i + CHUNK]).decode()})
                self._send({"type": "transcribe", "id": rid, "sample_rate": rate})
                self.proc.stdin.flush()
                while True:
                    raw = self.proc.stdout.readline()
                    if not raw:
                        raise OSError("the transcription worker exited")
                    msg = json.loads(raw)
                    if msg.get("id") == rid and msg.get("type") in ("end", "error"):
                        if msg["type"] == "error":
                            raise OSError("transcription failed")
                        return difflib.SequenceMatcher(None, _norm(text), _norm(msg.get("text", ""))).ratio()
            except (OSError, ValueError) as e:
                log(f"stopping the misreading check: {e}")
                self.broken, self.broken_at = True, time.time()
                self.close()
                self.on_broken()
                return None
            finally:
                watchdog.cancel()
