"""足りない部品を、画面のボタン 1 つで入れる: speech.cpp(公式のインストーラー)と、声のモデル(speech pull)。
入っているものはそのまま使い、足りないものだけ入れる。入れ先はどちらも speech.cpp の既定の場所
(Windows は %LOCALAPPDATA%、macOS・Linux は ~/.local と ~/.cache など)で、Kataribe のフォルダーには入れない。"""
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from . import audio_models
from .config import IS_MAC, IS_WINDOWS, ROOT, find_audio, find_speech, official_speech
from .engine import CREATE_NO_WINDOW, log
from .i18n import t

# 公式のインストーラー(最新の版を入れる)。Kataribe が話せるかは、worker の protocol で確かめる(engine.PROTOCOLS)
INSTALLER = "https://raw.githubusercontent.com/nyosegawa/speech.cpp/main/install"
PERCENT = re.compile(r"(\d{1,3})(?:\.\d+)?\s*%")

# audio.cpp: 実行ファイルは GitHub の最新リリースから、声のモデルは Hugging Face から取り、Kataribe のフォルダーの tools/ と models/ に置く。
# どちらも SHA-256 を照合してから使う(リリースの digest、Hugging Face の LFS の oid)
AUDIO_RELEASE = "https://api.github.com/repos/0xShug0/audio.cpp/releases/latest"
UA = {"User-Agent": "Kataribe"}


def _audio_asset():
    """この OS で使う audio.cpp の配布物の名前の終わり。"""
    if IS_WINDOWS:
        return "bin-windows-x64-vulkan.zip"
    if IS_MAC:
        return f"bin-macos-{'arm64' if platform.machine().lower() in ('arm64', 'aarch64') else 'x64'}-metal.tar.gz"
    return "bin-ubuntu-x64-vulkan.tar.gz"


def _get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as res:
        return json.loads(res.read())


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

    def _download(self, url, dest, sha256=""):
        """url を dest に落とす。途中まで落とした分(.part)があれば続きから。進み具合を画面へ送り、SHA-256 が合わなければ消して失敗にする。"""
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        have = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(url, headers=dict(UA, Range=f"bytes={have}-") if have else UA)
        try:
            res = urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as e:
            if e.code != 416:   # 416: もう全部落としてある
                raise
            res = None
        if res is not None:
            if have and res.status != 206:   # 続きからを受け付けなかった: 最初から
                have = 0
            total = have + int(res.headers.get("Content-Length") or 0)
            with res, open(part, "ab" if have else "wb") as out:
                done, shown = have, -1
                while True:
                    chunk = res.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    percent = int(done * 100 / total) if total else None
                    if percent != shown:
                        shown = percent
                        self._emit(percent=percent, line=f"{done / 1e6:.0f} / {total / 1e6:.0f} MB" if total else f"{done / 1e6:.0f} MB")
        if sha256:
            digest = hashlib.sha256()
            with open(part, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    digest.update(chunk)
            if digest.hexdigest() != sha256.lower():
                part.unlink(missing_ok=True)
                raise RuntimeError(t("setup.audio_bad_file", name=dest.name))
        os.replace(part, dest)

    def _run_audio(self, cfg):
        """audio.cpp を選んでいる時: 足りない実行ファイルと声のモデルを入れる。"""
        exe = cfg["audio_exe"] or find_audio()
        if not exe or not Path(exe).expanduser().is_file():
            self._emit(step="audio", percent=0, line="")
            release = _get_json(AUDIO_RELEASE)
            asset = next((a for a in release.get("assets", []) if a["name"].endswith(_audio_asset())), None)
            if not asset:
                raise RuntimeError(t("setup.audio_no_asset", name=_audio_asset()))
            folder = ROOT / "tools" / "audio.cpp" / release["tag_name"]
            archive = folder.with_name(asset["name"])
            self._download(asset["browser_download_url"], archive, (asset.get("digest") or "").removeprefix("sha256:"))
            self._emit(percent=None, line="")
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True)
            if archive.name.endswith(".zip"):
                with zipfile.ZipFile(archive) as z:
                    z.extractall(folder)
            else:
                with tarfile.open(archive) as z:
                    z.extractall(folder, filter="data")
            archive.unlink(missing_ok=True)
            found = next(iter(folder.rglob("audiocpp_server.exe" if IS_WINDOWS else "audiocpp_server")), None)
            if not found:
                raise RuntimeError(t("setup.audio_no_exe"))
            if not IS_WINDOWS:
                found.chmod(0o755)
            self.config.update({"audio_exe": str(found)})
            cfg = self.config.get()
        entry, model = audio_models.current(cfg)
        if not model.is_file():
            if cfg["audio_model"]:
                raise RuntimeError(t("eng.audio_no_model", path=cfg["audio_model"]))   # 自分で指定した GGUF は、こちらでは取れない
            self._emit(step="audio_model", percent=0, line="")
            rows = _get_json(audio_models.info_url(entry))
            row = next((r for r in rows if r.get("path", "").endswith("/" + entry["file"])), {})
            self._download(audio_models.url(entry), model, (row.get("lfs") or {}).get("oid", ""))

    def _run(self):
        try:
            cfg = self.config.get()
            if cfg["tts_backend"] == "audio":
                self._run_audio(cfg)
                self._emit(state="done", step="", percent=None)
                self.engine.restart()
                return
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
        except (OSError, RuntimeError, ValueError, KeyError, zipfile.BadZipFile, tarfile.TarError) as e:
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
