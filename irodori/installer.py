"""足りない部品を、画面のボタン 1 つで入れる: speech.cpp(公式のインストーラー)と、声のモデル(speech pull)。
入っているものはそのまま使い、足りないものだけ入れる。入れ先はどちらも speech.cpp の既定の場所
(Windows は %LOCALAPPDATA%、macOS・Linux は ~/.local と ~/.cache など)で、Kataribe のフォルダーには入れない。"""
import os
import re
import subprocess
import threading
from pathlib import Path

from .config import IS_WINDOWS, find_speech, official_speech
from .engine import CREATE_NO_WINDOW, log
from .i18n import t

# 公式のインストーラー(最新の版を入れる)。Kataribe が話せるかは、worker の protocol で確かめる(engine.PROTOCOLS)
INSTALLER = "https://raw.githubusercontent.com/nyosegawa/speech.cpp/main/install"
PERCENT = re.compile(r"(\d{1,3})(?:\.\d+)?\s*%")


def _install_cmd():
    if IS_WINDOWS:
        script = f"& ([scriptblock]::Create((irm {INSTALLER}.ps1))) -NoModifyPath"
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
    script = (f'tmp=$(mktemp) && curl -fsSL "{INSTALLER}.sh" -o "$tmp" && sh "$tmp" --no-modify-path; '
              'code=$?; rm -f "$tmp"; exit $code')
    return ["sh", "-c", script]


def _env(cfg):
    env = dict(os.environ)
    if cfg["model_dir"]:
        env["SPEECH_MODEL_DIR"] = str(Path(cfg["model_dir"]).expanduser())
    return env


# 既定のモデルが入るフォルダー(Hugging Face のリポジトリ名)。ここに .gguf があれば取得済みとみなす(起動を待たせない速い確認)
MODEL_ROW = re.compile(r"^(\S+)\s+(\S+)\s+([\d.]+ [KMGT]?B)\s+(yes|no)\s+(\S+)\s*(.*)$")


def list_models(cfg):
    """speech.cpp が扱える音声合成のモデル。[{name, type, size, fetched, languages}](`speech models` の表を読む)。"""
    exe = cfg["speech_exe"]
    if not exe or not Path(exe).is_file():
        return []
    try:
        out = subprocess.run([exe, "models"], capture_output=True, timeout=60, env=_env(cfg),
                             creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        m = MODEL_ROW.match(line.strip())
        if m and m.group(5) == "synthesis":
            found.append({"name": m.group(1), "type": m.group(2), "size": m.group(3), "fetched": m.group(4) == "yes",
                          "languages": m.group(6).split()})
    return found


def model_present(cfg):
    """選んだモデルを取ってあるか。表に無い名前(ファイルのパスなど)は None(確かめずに起動する)。"""
    row = next((m for m in list_models(cfg) if m["name"] == cfg["model"]), None)
    return row["fetched"] if row else None


class Installer:
    """state: idle / running / done / error。step: speech / model。進み具合は bus の "setup" で画面へ送る。"""

    def __init__(self, config, engine, bus):
        self.config, self.engine, self.bus = config, engine, bus
        self.state, self.step, self.percent, self.line, self.error = "idle", "", None, "", ""
        self._lock = threading.Lock()

    def snapshot(self):
        return {"state": self.state, "step": self.step, "percent": self.percent, "line": self.line, "error": self.error}

    def _emit(self, **changes):
        for key, value in changes.items():
            setattr(self, key, value)
        self.bus.emit("setup", self.snapshot())

    def start(self):
        with self._lock:
            if self.state == "running":
                return
            self._emit(state="running", step="", percent=None, line="", error="")
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            cfg = self.config.get()
            missing = self.engine.missing
            have = cfg["speech_exe"] and Path(cfg["speech_exe"]).is_file()
            if not have or missing == "speech":
                found = find_speech() if not have else ""
                if not found:
                    # 無い、または入っている版と話せない: 公式のインストーラーで最新の版を入れ、その場所のものを使う
                    self._emit(step="speech", percent=None, line="")
                    self._stream(_install_cmd(), dict(os.environ))
                    found = official_speech() or find_speech()
                    if not found:
                        raise RuntimeError(t("setup.auto_no_speech"))
                self.config.update({"speech_exe": found})
                cfg = self.config.get()
            # M1: 置いてあっても読み込めなかったモデル(missing="model")は、pull で確かめ直す(サイズと SHA-256 を照合して取り直す)
            if model_present(cfg) is False or missing == "model":
                self._emit(step="model", percent=0, line="")
                if missing == "model" and model_present(cfg):
                    self._stream([cfg["speech_exe"], "rm", cfg["model"]], _env(cfg))
                self._stream([cfg["speech_exe"], "pull", cfg["model"]], _env(cfg))
            self._emit(state="done", step="", percent=None)
            self.engine.restart()
        except (OSError, RuntimeError) as e:
            log(f"setup failed: {e}")
            self._emit(state="error", error=str(e))

    def _stream(self, cmd, env):
        """コマンドを動かし、出力の最後の行と % を画面へ送る。失敗したら最後の数行を添えて RuntimeError。"""
        log(f"setup run {cmd[0]} {' '.join(cmd[1:3])}")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                                creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0)
        self.engine.adopt(proc)
        tail, buf = [], b""
        while True:
            chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            # 進み具合は \r で上書きされることが多いので、\r と \n の両方で区切る
            *done, buf = re.split(rb"[\r\n]", buf)
            for raw in done:
                text = raw.decode("utf-8", "replace").strip()
                if not text:
                    continue
                tail = (tail + [text])[-6:]
                m = PERCENT.search(text)
                self._emit(line=text[-160:], percent=min(100, int(m.group(1))) if m else self.percent)
        code = proc.wait()
        if code != 0:
            raise RuntimeError(t("setup.auto_failed", code=code, tail="\n".join(tail)))
