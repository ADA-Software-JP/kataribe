"""CLI のログイン状態の確認と、アプリ内からのログイン。claude と codex は、ブラウザで認証する login コマンドを裏で起動する。
トークンや認証の中身は読まない・ログにも画面にも出さない(状態は CLI の status コマンドの成否だけで見る)。"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from .config import IS_WINDOWS
from .i18n import t

NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
POLL_SECONDS = 3          # ログイン中に状態を調べる間隔
LOGIN_TIMEOUT = 300       # ログインを待つ最長(秒)
URL_HINT_SECONDS = 20     # この間に認証ページの URL が出なければ、手動のコマンドを案内する
GRACE_SECONDS = 8         # 状態が通ってから login コマンドが終わるのを待つ最長(秒)
NAMES = {"claude": "Claude Code", "codex": "Codex"}
# provider: (状態を調べるコマンド, ログインのコマンド)。どちらも実在の CLI の引数(--help で確認済み)
COMMANDS = {
    "claude": (["auth", "status", "--json"], ["auth", "login"]),
    "codex": (["login", "status"], ["login"]),
}
# CLI や API が返す「認証が切れている」ことを示す文
AUTH_ERROR = re.compile(
    r"oauth|authenticat|not logged in|please (?:run )?/?login|run /login|log in|sign in|login required|"
    r"unauthori[sz]ed|invalid api key|invalid_api_key|credentials|\b401\b|token (?:has )?expired|session expired|"
    r"refresh token", re.I)
URL = re.compile(r"https://[^\s\"'<>]+")


def _log(text):
    if sys.stdout:
        print(f"Kataribe: {text}", flush=True)


def is_auth_error(text):
    return bool(text) and bool(AUTH_ERROR.search(str(text)))


def supports(provider):
    return provider in COMMANDS


def manual_command(provider):
    """ブラウザが開かない時などに、ユーザーが自分で打つコマンド。"""
    return "claude auth login" if provider == "claude" else "codex login"


def _exe(provider):
    return shutil.which(provider)


def _run(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                          cwd=tempfile.gettempdir(), creationflags=NO_WINDOW)


def probe(provider):
    """ログイン状態を CLI に聞く。"in" / "out" / "unknown"(コマンドが無い・時間切れ・読めない)。"""
    exe = _exe(provider)
    if not exe:
        return "unknown"
    try:
        done = _run([exe] + COMMANDS[provider][0], 25)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    text = (done.stdout + done.stderr).decode("utf-8", "replace")
    if provider == "claude":
        try:
            return "in" if json.loads(done.stdout.decode("utf-8", "replace")).get("loggedIn") else "out"
        except (ValueError, AttributeError):
            return "unknown"
    if done.returncode == 0:
        return "in"
    return "out" if re.search(r"not logged in", text, re.I) else "unknown"


class Auth:
    """provider ごとのログイン状態。state: unknown(未確認)/ ok / out(未ログイン)/ login(ログイン中)。"""

    def __init__(self, on_change, on_success):
        self._on_change, self._on_success = on_change, on_success
        self._lock = threading.RLock()
        self._s = {p: self._blank() for p in COMMANDS}
        self._procs = {}
        self._cancel = {}

    @staticmethod
    def _blank():
        return {"state": "unknown", "detail": "", "url": "", "error": "", "hint": False, "since": 0.0, "forced": False}

    def snapshot(self, provider):
        if provider not in COMMANDS:
            return {"state": "none"}
        with self._lock:
            s = self._s[provider]
            return {"state": s["state"], "detail": s["detail"], "url": s["url"], "error": s["error"], "hint": s["hint"],
                    "since": s["since"], "command": manual_command(provider), "name": NAMES[provider],
                    "timeout": LOGIN_TIMEOUT}

    def blocked(self, provider):
        """ログインしていないと分かっている(この間は LLM を使わない)。"""
        return self._s.get(provider, {}).get("state") in ("out", "login")

    def _set(self, provider, **fields):
        with self._lock:
            before = dict(self._s[provider])
            self._s[provider].update(fields)
            changed = self._s[provider] != before
        if changed:
            self._on_change()

    # --- 状態の確認 ---------------------------------------------------------------------------------------------
    def check(self, provider):
        """CLI に聞いて状態を更新する(数秒かかるので呼び出し側はスレッドで)。ログイン中は触らない。"""
        if provider not in COMMANDS:
            return "unknown"
        found = probe(provider)
        with self._lock:
            if self._s[provider]["state"] == "login":
                return found
            if found == "in":
                self._set(provider, state="ok", detail="", error="", forced=False)
            elif found == "out":
                self._set(provider, state="out", detail=t("auth.out"), forced=False)
        return found

    def mark_out(self, provider):
        """実行中のエラーで、認証が切れていると分かった時(status は「ログイン済み」のままでも、トークンが期限切れのことがある)。"""
        if provider not in COMMANDS:
            return
        with self._lock:
            if self._s[provider]["state"] == "login":
                return
            self._set(provider, state="out", detail=t("auth.expired"), forced=True)

    # --- ログイン -----------------------------------------------------------------------------------------------
    def login(self, provider):
        """login コマンドを起動して、状態を数秒おきに調べるスレッドを始める。すでに始まっていれば何もしない。"""
        if provider not in COMMANDS:
            raise ValueError(t("auth.no_login"))
        exe = _exe(provider)
        if not exe:
            raise ValueError(t("auth.no_cli", cli=provider))
        with self._lock:
            if self._s[provider]["state"] == "login":
                return
            self._cancel[provider] = threading.Event()
            self._set(provider, state="login", detail=t("auth.in_browser"), url="", error="", hint=False,
                      since=time.time())
        threading.Thread(target=self._login_run, args=(provider, exe), daemon=True).start()

    def cancel(self, provider):
        ev = self._cancel.get(provider)
        if ev:
            ev.set()

    def _login_run(self, provider, exe):
        cmd = [exe] + COMMANDS[provider][1]
        _log(t("auth.log_start", cmd=" ".join([provider] + COMMANDS[provider][1])))
        cancel = self._cancel[provider]
        proc = None
        try:
            # 端末入力は渡さない(待ち続けて止まらないように)。出力は URL を拾うためだけに読み、ログには出さない
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    cwd=tempfile.gettempdir(), creationflags=NO_WINDOW)
            self._procs[provider] = proc
            threading.Thread(target=self._read_url, args=(provider, proc), daemon=True).start()
            started = last = time.time()
            while True:
                if cancel.wait(0.5):
                    return self._finish(provider, error=t("auth.cancelled"))
                now = time.time()
                if now - started > LOGIN_TIMEOUT:
                    return self._finish(provider, error=t("auth.timeout", min=LOGIN_TIMEOUT // 60))
                code = proc.poll()
                if not self._s[provider]["url"] and not self._s[provider]["hint"] and now - started > URL_HINT_SECONDS:
                    self._set(provider, hint=True)
                if code is None and now - last < POLL_SECONDS:
                    continue
                last = now
                found = probe(provider)
                forced = self._s[provider]["forced"]
                if code == 0 and found != "out":
                    return self._succeed(provider)
                if code is None and found == "in" and not forced:
                    # 認証は通っている。login コマンドが終わるのを少し待つ(終わらなければ止める)
                    try:
                        proc.wait(GRACE_SECONDS)
                    except subprocess.TimeoutExpired:
                        pass
                    return self._succeed(provider)
                if code is not None and code != 0:
                    return self._finish(provider, error=t("auth.cmd_ended"))
                if code == 0:
                    return self._finish(provider, error=t("auth.unconfirmed"))
        except OSError as e:
            self._finish(provider, error=t("auth.cmd_failed", e=e))
        finally:
            self._reap(provider, proc)

    def _read_url(self, provider, proc):
        """login の出力から認証ページの URL だけを拾う(ブラウザが開かない時の案内用)。出力そのものは捨てる。"""
        for raw in iter(proc.stdout.readline, b""):
            m = URL.search(raw.decode("utf-8", "replace"))
            if m and not self._s[provider]["url"]:
                self._set(provider, url=re.sub(r"[\x00-\x1f]", "", m.group(0))[:2000])

    def _reap(self, provider, proc):
        self._procs.pop(provider, None)
        if proc and proc.poll() is None:
            if IS_WINDOWS:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=NO_WINDOW)
            else:
                proc.kill()

    def _finish(self, provider, error):
        self._set(provider, state="out", detail=t("auth.out"), error=error, url="", hint=False)

    def _succeed(self, provider):
        self._set(provider, state="ok", detail="", error="", url="", hint=False, forced=False)
        _log(t("auth.log_done", name=NAMES[provider]))
        self._on_success(provider)
