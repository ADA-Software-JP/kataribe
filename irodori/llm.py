"""文章を書く係(エージェント)。Claude Code は常駐セッション、Codex は常駐の app-server、LM Studio はストリーム。"""
import collections
import copy
import http.client
import json
import math
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

from . import VERSION, auth as authmod, prompts, tuning
from .config import DEFAULT_EFFORT, DEFAULT_MODELS, EFFORTS, IS_WINDOWS
from .i18n import t

PROVIDERS = ("claude", "codex", "lmstudio")
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


class LLMError(Exception):
    """code は接続テストの失敗の言い分け用(文言は言語で変わるので、文面では見分けない): no_cli / lm_connect / lm_no_model / timeout"""

    def __init__(self, message="", code=""):
        super().__init__(message)
        self.code = code


class AuthError(LLMError):
    """ログインが切れている・していない。画面にログインの案内を出す。"""


def _kill_tree(proc):
    if proc.poll() is not None:
        return
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, creationflags=NO_WINDOW)
    else:
        proc.kill()


def _need(cli, hint):
    path = shutil.which(cli)
    if not path:
        raise LLMError(t("llm.no_cli", cli=cli, hint=hint), "no_cli")
    return path


def _claude_exe():
    return _need("claude", t("llm.hint_claude"))


def _auth_error(provider):
    return AuthError(t("llm.not_signed_in", name=authmod.NAMES[provider]))


def _system_file(text):
    """複数行の指示を渡すためのファイル。サーバーごと・起動ごとに別のファイルにする(同時に動く別のサーバーと混ざらないように)。"""
    fd, path = tempfile.mkstemp(prefix="irodori_system_", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _codex_exe():
    return _need("codex", t("llm.hint_codex"))


def _jsonl(obj):
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


# 標準エラーに混ざりうる秘密(トークン、キー、認証ヘッダー、長い乱数列)は、画面に出す前に伏せる
_SECRET = re.compile(r"(?i)(bearer\s+\S+|\b(?:api[_-]?key|token|secret|password|authorization)\b\s*[:=]\s*\S+|\bsk-[\w-]{8,}"
                     r"|\beyJ[\w-]{8,}\.[\w.-]+|\b[A-Za-z0-9_-]{32,}={0,2})")
_DENIED = ("permission denied", "access is denied", "access denied", "read-only file system", "os error 5", "sqlite", "eperm")


def _scrub(text):
    return _SECRET.sub("***", text.replace(os.path.expanduser("~"), "~"))


class _Stderr:
    """子プロセスの標準エラーを読み捨てずに、末尾の数行だけ持つ(終了した理由を画面に出すため)。"""

    def __init__(self, proc):
        self._lines = collections.deque(maxlen=8)
        self._thread = threading.Thread(target=self._pump, args=(proc,), daemon=True)
        self._thread.start()

    def _pump(self, proc):
        for raw in iter(proc.stderr.readline, b""):
            line = _scrub(raw.decode("utf-8", "replace").strip())
            if line:
                self._lines.append(line[:300])

    def tail(self):
        """終了直後でも最後の出力を拾えるよう、読み取りの終わりを少しだけ待つ。"""
        self._thread.join(1.0)
        return " / ".join(list(self._lines)[-2:])


def _exited(proc):
    """書き込みに失敗した時、相手が終わっていたか(終わる途中なら少しだけ待つ)。"""
    try:
        proc.wait(1)
    except subprocess.TimeoutExpired:
        pass
    return proc.poll() is not None


def _exit_error(provider, err):
    """プロセスが終わった時の LLMError。標準エラーの末尾から、原因と次にやることを短く添える。"""
    tail = err.tail() if err else ""
    name = authmod.NAMES[provider]
    if authmod.is_auth_error(tail):
        return _auth_error(provider)
    if any(w in tail.lower() for w in _DENIED):
        todo = t("llm.denied_todo", provider=provider)
    else:
        todo = t("llm.start_todo", provider=provider)
    cause = t("llm.cause", tail=tail[-160:]) if tail else t("llm.cause_none")
    return LLMError(t("llm.exited", name=name, cause=cause, todo=todo))


# Claude Code に許す道具(Web 検索の設定がオンの時だけ。ほかの道具は渡さない)
CLAUDE_SEARCH_TOOLS = "WebSearch,WebFetch"
CLAUDE_SEARCH_NAMES = tuple(CLAUDE_SEARCH_TOOLS.split(","))


def _with_context(message, context, agent):
    """新しいセッションの最初の依頼には、これまでの流れ(context が返す文)を前に付ける。"""
    prefix = context(agent) if context else ""
    return f"{prefix}\n\n{message}" if prefix else message


class ClaudeAgent:
    """claude -p を 1 本起動したままにして、user メッセージを書き込んで会話する。"""

    name = "claude"

    def __init__(self, cfg, on_state, system, ready="", on_auth=None, tune=None, search=False):
        self.model = cfg.get("model") or DEFAULT_MODELS["claude"]
        self._search = search
        self.turns_sent = 0       # このセッションに送ったターンの数(0 なら、これまでの流れを知らない新しいセッション)
        self.retired = False      # 設定が変わって新しいセッションに置き換えられた
        self._tune = tune or tuning.defaults
        self._on_state = on_state
        self._on_auth = on_auth or (lambda: None)
        self._auth_failed = False
        self._system = system
        self._ready = ready
        self._system_path = ""
        self.state = "stopped"
        self._proc = None
        self._err = None
        self._events = queue.Queue()
        self._cond = threading.Condition()
        self._wlock = threading.Lock()
        self._outstanding = 0
        self._startlock = threading.Lock()

    def _set(self, state):
        if self.state != state:
            self.state = state
            self._on_state()

    def start(self):
        with self._startlock:
            if self._proc and self._proc.poll() is None:
                return
            self._drop_system_file()
            self._auth_failed = False
            self._system_path = _system_file(self._system)
            cmd = [_claude_exe(), "-p", "--model", self.model, "--input-format", "stream-json",
                   "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                   "--strict-mcp-config", "--setting-sources", "", "--tools", CLAUDE_SEARCH_TOOLS if self._search else "",
                   "--no-session-persistence",
                   # Claude Code 自身の指示(コーディング用)を使わず、読み上げ文の指示だけを渡す。複数行なのでファイルで渡す
                   "--system-prompt-file", self._system_path]
            if self._search:
                cmd += ["--allowedTools", CLAUDE_SEARCH_TOOLS]   # 承認を求めずに使わせる(それ以外の道具は渡さない)
            try:
                proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        cwd=tempfile.gettempdir(), creationflags=NO_WINDOW)
            except OSError as e:
                raise LLMError(t("llm.claude_start_failed", e=e))
            self._proc = proc
            self._err = _Stderr(proc)
            self._events = queue.Queue()
            self.turns_sent = 0
            with self._cond:
                self._outstanding = 0
            self._set("waking")
            threading.Thread(target=self._read, args=(proc, self._events), daemon=True).start()
            self._send({"type": "user", "message": {"role": "user", "content": self._ready}})

    def _send(self, message):
        with self._wlock:
            if not self._proc or self._proc.poll() is not None:
                raise _exit_error("claude", self._err)
            try:
                if message.get("type") == "user":
                    with self._cond:
                        self._outstanding += 1
                self._proc.stdin.write(_jsonl(message))
                self._proc.stdin.flush()
            except OSError as e:
                if _exited(self._proc):
                    raise _exit_error("claude", self._err)
                raise LLMError(t("llm.claude_write_failed", e=e))

    def _read(self, proc, events):
        for raw in iter(proc.stdout.readline, b""):
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            # 認証が切れていると、assistant に error=authentication_failed、result に is_error で返ってくる
            if (ev.get("type") == "assistant" and ev.get("error") == "authentication_failed") or (
                    ev.get("type") == "result" and ev.get("is_error") and authmod.is_auth_error(ev.get("result"))):
                if proc is self._proc and not self._auth_failed:
                    self._auth_failed = True
                    self._on_auth()
            if ev.get("type") == "result":
                with self._cond:
                    self._outstanding = max(0, self._outstanding - 1)
                    if self._outstanding == 0 and proc is self._proc:
                        self._set("ready")
                    self._cond.notify_all()
            events.put(ev)
        events.put({"type": "eof"})
        with self._cond:
            if proc is self._proc:
                self._outstanding = 0
                self._set("stopped")
            self._cond.notify_all()

    def _wait_idle(self, cancelled):
        end = time.time() + self._tune()["claude_start_timeout"]
        with self._cond:
            while self._outstanding > 0:
                if cancelled():
                    return False
                if time.time() > end:
                    break
                self._cond.wait(0.25)
            else:
                return True
        # 起動の確認に返事が無い: 止めて、次の依頼では新しく起動し直す
        self.close()
        raise LLMError(t("llm.claude_timeout"), "timeout")

    def turn(self, message, on_text, cancelled, context=None, on_search=None):
        """1 ターン書かせる。context(agent) は、新しいセッションの時に添える「これまでの流れ」を返す。on_search() は検索を始めた時に呼ぶ。"""
        for attempt in (1, 2):
            if cancelled():
                return
            self.start()
            if not self._wait_idle(cancelled):
                return
            if self._auth_failed:
                raise _auth_error("claude")
            if self._proc is None or self._proc.poll() is not None:
                continue
            events = self._events
            while not events.empty():
                events.get_nowait()
            self._send({"type": "user", "message": {"role": "user", "content": _with_context(message, context, self)}})
            self.turns_sent += 1
            got, interrupted_at, last = False, None, time.time()
            while True:
                try:
                    ev = events.get(timeout=0.2)
                except queue.Empty:
                    ev = None
                if cancelled() and interrupted_at is None:
                    interrupted_at = time.time()
                    self.interrupt()
                if interrupted_at and time.time() - interrupted_at > 6:
                    self.close()
                    return
                if ev is None:
                    # 生きたまま何も返さなくなった claude は閉じて、失敗表示と再試行に回す(Codex と同じ上限)
                    if not interrupted_at and time.time() - last > self._tune()["codex_idle_limit"]:
                        self.close()
                        raise LLMError(t("llm.claude_timeout"), "timeout")
                    continue
                last = time.time()
                kind = ev.get("type")
                if kind == "eof":
                    if got or attempt == 2:
                        raise _exit_error("claude", self._err)
                    break
                if kind == "stream_event" and interrupted_at is None:
                    d = ev.get("event", {})
                    if on_search and d.get("type") == "content_block_start" and (d.get("content_block") or {}).get("name") in CLAUDE_SEARCH_NAMES:
                        on_search()
                    delta = (d.get("delta") or {}) if d.get("type") == "content_block_delta" else {}
                    if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str) and delta["text"]:
                        got = True
                        on_text(delta["text"])
                elif kind == "result":
                    if ev.get("is_error") and ev.get("subtype") != "error_during_execution":
                        if self._auth_failed or authmod.is_auth_error(ev.get("result")):
                            raise _auth_error("claude")
                        detail = str(ev.get("result") or "").strip()[-300:]
                        raise LLMError(detail or t("llm.claude_error"))
                    return
        raise _exit_error("claude", self._err)

    def interrupt(self):
        try:
            self._send({"type": "control_request", "request_id": f"i{time.time_ns()}",
                        "request": {"subtype": "interrupt"}})
        except LLMError:
            pass

    def reset(self):
        self.close()

    def _drop_system_file(self):
        try:
            os.unlink(self._system_path)
        except OSError:
            pass
        self._system_path = ""

    def close(self):
        with self._startlock:
            proc, self._proc = self._proc, None
        if proc:
            _kill_tree(proc)
        self._drop_system_file()
        with self._cond:
            self._outstanding = 0
            self._cond.notify_all()
        self._set("stopped")


def _lm_url(cfg, path):
    return (cfg.get("base_url") or "http://localhost:1234/v1").rstrip("/") + path


def _lm_request(cfg, path, payload=None, timeout=30):
    req = urllib.request.Request(_lm_url(cfg, path), method="POST" if payload is not None else "GET",
                                 data=json.dumps(payload).encode("utf-8") if payload is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise LLMError(t("llm.lm_http", code=e.code, body=e.read()[:200].decode("utf-8", "replace")))
    except (OSError, ValueError) as e:
        raise LLMError(t("llm.lm_connect", url=_lm_url(cfg, "")), "lm_connect")


def _with_draft(saved, draft):
    """保存済みの LLM 設定に、画面の未保存の値を重ねた写しを返す。文字列の項目だけ受け取り、空のモデルは既定にする。"""
    cfg = copy.deepcopy(saved)
    for provider, values in (draft.items() if isinstance(draft, dict) else ()):
        if provider in cfg and isinstance(values, dict):
            for key, value in values.items():
                if key in cfg[provider] and isinstance(value, str):
                    cfg[provider][key] = value.strip()
    for provider in ("claude", "codex"):
        cfg[provider]["model"] = cfg[provider].get("model") or DEFAULT_MODELS[provider]
    if cfg["codex"].get("effort") not in EFFORTS:
        cfg["codex"]["effort"] = DEFAULT_EFFORT
    return cfg


_NAMES = {"claude": "Claude Code", "codex": "Codex", "lmstudio": "LM Studio"}


def explain_failure(provider, raw, cfg, code=""):
    """接続テストの失敗を、利用者向けの短い原因(detail)と次にやること(next)にする。元の文は raw に残す。
    code は LLMError.code(文言は言語で変わるので、日本語か英語かで見分けない)。"""
    name = _NAMES[provider]
    model = cfg[provider].get("model") or ""
    low = raw.lower()
    if re.search(r"model_not_found|model.{0,40}(does not exist|not found|not supported|unknown)|invalid model|unknown model", low):
        detail, action = t("llm.fail_model", name=name, model=model), t("llm.fail_model_next")
    elif provider == "lmstudio" and code == "lm_connect":
        detail, action = t("llm.fail_lm"), t("llm.fail_lm_next")
    elif provider == "lmstudio" and code == "lm_no_model":
        detail, action = t("llm.fail_lm_nomodel"), t("llm.fail_lm_nomodel_next")
    elif code == "no_cli":
        detail, action = t("llm.fail_cli", name=name), t("llm.fail_cli_next")
    elif code == "timeout" or re.search(r"timed? ?out", low):
        detail, action = t("llm.fail_timeout", name=name), t("llm.fail_timeout_next")
    elif re.search(r"rate.?limit|quota|429|insufficient", low):
        detail, action = t("llm.fail_limit", name=name), t("llm.fail_limit_next")
    else:
        detail, action = t("llm.fail_other", name=name), t("llm.fail_other_next")
    return {"detail": detail, "next": action, "raw": raw[-600:]}


def lmstudio_models(cfg):
    try:
        with _lm_request(cfg, "/models") as r:
            ids = [m["id"] for m in json.loads(r.read()).get("data", [])]
        if not all(isinstance(i, str) and i for i in ids):
            raise ValueError("bad model id")
        return ids
    except (OSError, http.client.HTTPException, ValueError, TypeError, KeyError, AttributeError):
        # LM Studio 以外が答えた(HTML など)、または読み取り中に切れた
        raise LLMError(t("llm.lm_connect", url=_lm_url(cfg, "")), "lm_connect")


class LMStudioAgent:
    name = "lmstudio"
    state = "ready"

    def __init__(self, cfg, on_state, system, ready="", on_auth=None, tune=None, search=False):
        self.cfg = cfg
        self._tune = tune or tuning.defaults
        self._on_state = on_state
        self._system = system
        self._resp = None
        self.history = []
        self.resolved = ""
        self.retired = False

    @property
    def turns_sent(self):
        return len(self.history) // 2

    def turn(self, message, on_text, cancelled, context=None, on_search=None):
        model = self.cfg.get("model") or (lmstudio_models(self.cfg) or [""])[0]
        if not model:
            raise LLMError(t("llm.lm_no_model"), "lm_no_model")
        if model != self.resolved:
            self.resolved = model
            self._on_state()
        opt = self._tune()
        messages = [{"role": "system", "content": self._system}]
        keep = opt["history_turns"] * 2
        messages += (self.history[-keep:] if keep else []) + [{"role": "user", "content": _with_context(message, context, self)}]
        reply = []
        try:
            with _lm_request(self.cfg, "/chat/completions", {"model": model, "temperature": opt["lm_temperature"], "max_tokens": opt["lm_max_tokens"],
                                                              "stream": True, "messages": messages}, 120) as r:
                self._resp = r
                finished = False
                for raw in r:
                    if cancelled():
                        return
                    line = raw.decode("utf-8", "replace").strip()
                    if line.endswith("[DONE]"):
                        finished = True
                    if not line.startswith("data:") or line.endswith("[DONE]"):
                        continue
                    try:
                        choice = json.loads(line[5:])["choices"][0]
                        finished = finished or bool(choice.get("finish_reason"))
                        delta = choice.get("delta", {}).get("content")
                    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                        continue
                    if isinstance(delta, str) and delta:
                        reply.append(delta)
                        on_text(delta)
        except (OSError, http.client.HTTPException, ValueError):
            if cancelled():
                return   # interrupt() が通信を閉じた
            # 書いている途中の切断・タイムアウト。LLMError にして、呼び出し側の失敗表示と再試行に回す
            raise LLMError(t("llm.lm_connect", url=_lm_url(self.cfg, "")), "lm_connect")
        finally:
            self._resp = None
        if not finished:
            # 終わりの印が来ないまま切れた。途中までを成功として履歴に残さない
            raise LLMError(t("llm.lm_connect", url=_lm_url(self.cfg, "")), "lm_connect")
        self.history += [{"role": "user", "content": message}, {"role": "assistant", "content": "".join(reply)}]

    def interrupt(self):
        """応答を待っている読み取りを、通信を閉じて止める(LM Studio が黙ったままでも、次の依頼へすぐ移る)。"""
        resp = self._resp
        if resp is not None:
            try:
                resp.close()
            except (OSError, ValueError):
                pass

    def reset(self):
        self.history = []

    def close(self):
        pass


# Codex の app-server に渡す設定。読み上げ文を書くだけなので、フック・MCP 周りの拡張・ツールは使わない
# (ユーザーの環境にあるフックなどで 1 ターンが数秒遅れるため)。Web 検索は設定の「Web 検索を使う」で決める(start())。
CODEX_QUIET = [arg for name in ("hooks", "apps", "plugins", "memories", "multi_agent", "multi_agent_v2", "browser_use",
                                "computer_use", "image_generation", "skill_search", "shell_tool", "unified_exec")
               for arg in ("--disable", name)]
# app-server から受け取る通知のうち使うもの(ほかはフックや MCP の起動通知で、数が多いので読み捨てる)
CODEX_EVENTS = {"item/started", "item/agentMessage/delta", "turn/completed", "error"}


class CodexAgent:
    """codex app-server(JSON-RPC、1 行 1 JSON)を 1 本起動したままにして、スレッドを作り、ターンを続けて送る。
    返事は agentMessage の差分で届くので、書き終えた行から読み上げに回せる。"""

    name = "codex"

    def __init__(self, cfg, on_state, system, ready="", on_auth=None, tune=None, search=False):
        self.model = cfg.get("model") or DEFAULT_MODELS["codex"]
        self._search = search
        self.turns_sent = 0       # このスレッドに送ったターンの数(0 なら、これまでの流れを知らない新しいスレッド)
        self.retired = False      # 設定が変わって新しいセッションに置き換えられた
        self._tune = tune or tuning.defaults
        self.effort = cfg.get("effort") if cfg.get("effort") in EFFORTS else DEFAULT_EFFORT
        self._on_state = on_state
        self._on_auth = on_auth or (lambda: None)
        self._system = system
        self.state = "stopped"
        self._proc = None
        self._err = None
        self._thread = ""
        self._turn_id = ""
        self._events = queue.Queue()
        self._pending = {}
        self._n = 0
        self._plock = threading.Lock()
        self._wlock = threading.Lock()
        self._startlock = threading.RLock()

    def _set(self, state):
        if self.state != state:
            self.state = state
            self._on_state()

    def _write(self, message):
        with self._wlock:
            proc = self._proc
            if not proc or proc.poll() is not None:
                raise _exit_error("codex", self._err)
            try:
                proc.stdin.write(_jsonl(message))
                proc.stdin.flush()
            except OSError as e:
                if _exited(proc):
                    raise _exit_error("codex", self._err)
                raise LLMError(t("llm.codex_write_failed", e=e))

    def _call(self, method, params, timeout=30):
        with self._plock:
            self._n += 1
            rid, waiter = self._n, (threading.Event(), {})
            self._pending[rid] = waiter
        try:
            self._write({"id": rid, "method": method, "params": params})
            if not waiter[0].wait(timeout):
                raise LLMError(t("llm.codex_no_reply", method=method), "timeout")
        finally:
            with self._plock:
                self._pending.pop(rid, None)
        error = waiter[1].get("error")
        if error:
            text = str(error.get("message") or "") if isinstance(error, dict) else str(error)
            if authmod.is_auth_error(text):
                self._on_auth()
                raise _auth_error("codex")
            if isinstance(error, dict) and error.get("exited"):
                raise LLMError(text)
            raise LLMError(t("llm.codex_error", text=text[-300:]))
        return waiter[1].get("result") or {}

    def _read(self, proc, events):
        for raw in iter(proc.stdout.readline, b""):
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if "method" in msg and "id" in msg:
                # サーバーからの要求(承認など)。読み取り専用で頼まないので、断る
                try:
                    self._write({"id": msg["id"], "error": {"code": -32601, "message": "unsupported"}})
                except LLMError:
                    pass
            elif msg.get("method") in CODEX_EVENTS:
                events.put(msg)
            elif "id" in msg and "method" not in msg:
                with self._plock:
                    waiter = self._pending.get(msg["id"])
                if waiter:
                    waiter[1].update(msg)
                    waiter[0].set()
        events.put({"method": "eof"})
        with self._plock:
            waiting = list(self._pending.values())
        if waiting:
            reason = str(_exit_error("codex", self._err))
            for event, box in waiting:
                box.setdefault("error", {"message": reason, "exited": True})
                event.set()
        if proc is self._proc:
            self._thread = ""
            self._set("stopped")

    def start(self):
        """プロセスを起動し、会話のスレッドを作る(どちらも済んでいれば何もしない)。"""
        with self._startlock:
            if not (self._proc and self._proc.poll() is None):
                self._kill()
                cmd = [_codex_exe(), "app-server"] + CODEX_QUIET + ["-c", f'web_search="{"live" if self._search else "disabled"}"']
                try:
                    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            cwd=tempfile.gettempdir(), creationflags=NO_WINDOW)
                except OSError as e:
                    raise LLMError(t("llm.codex_start_failed", e=e))
                self._proc = proc
                self._err = _Stderr(proc)
                self._events = queue.Queue()
                self._thread = ""
                self._set("waking")
                threading.Thread(target=self._read, args=(proc, self._events), daemon=True).start()
                try:
                    self._call("initialize", {"clientInfo": {"name": "kataribe", "title": "Kataribe", "version": VERSION}})
                    self._write({"method": "initialized"})
                except LLMError:
                    self.close()
                    raise
            if not self._thread:
                self._set("waking")
                try:
                    # 組み込みの指示は baseInstructions で置き換える。書き込みは禁止、承認は求めない、履歴は残さない
                    result = self._call("thread/start", {
                        "model": self.model, "sandbox": "read-only", "approvalPolicy": "never", "ephemeral": True,
                        "cwd": tempfile.gettempdir(), "baseInstructions": self._system})
                    self._thread = result["thread"]["id"]
                    self.turns_sent = 0
                except LLMError:
                    self.close()
                    raise
                except (KeyError, TypeError):
                    self.close()
                    raise LLMError(t("llm.codex_no_thread"))
            self._set("ready")

    def turn(self, message, on_text, cancelled, context=None, on_search=None):
        """1 ターン書かせる。context(agent) は、新しいスレッドの時に添える「これまでの流れ」を返す。on_search() は検索を始めた時に呼ぶ。"""
        for attempt in (1, 2):
            if cancelled():
                return
            self.start()
            if cancelled():
                return
            events = self._events
            while not events.empty():
                events.get_nowait()
            sent = _with_context(message, context, self)
            self.turns_sent += 1
            result = self._call("turn/start", {"threadId": self._thread, "effort": self.effort, "summary": "none",
                                               "input": [{"type": "text", "text": sent}]})
            turn_id = self._turn_id = (result.get("turn") or {}).get("id", "")
            got, interrupted_at, last, items, failure = False, None, time.time(), set(), {}
            while True:
                try:
                    ev = events.get(timeout=0.2)
                except queue.Empty:
                    ev = None
                now = time.time()
                if cancelled() and interrupted_at is None:
                    interrupted_at = now
                    self.interrupt()
                if interrupted_at and now - interrupted_at > 6:
                    self.close()
                    return
                if ev is None:
                    idle = self._tune()["codex_idle_limit"]
                    if not interrupted_at and now - last > idle:
                        self.close()
                        raise LLMError(t("llm.codex_idle", sec=idle), "timeout")
                    continue
                last = now
                method, params = ev.get("method"), ev.get("params") or {}
                if method == "eof":
                    if got or attempt == 2:
                        raise _exit_error("codex", self._err)
                    break
                if params.get("turnId", turn_id) != turn_id:
                    continue
                if method == "item/started":
                    item = params.get("item") or {}
                    # 途中経過のコメント(commentary)は読ませない。最終の返事だけを使う
                    if item.get("type") == "agentMessage" and item.get("phase") in (None, "final_answer"):
                        items.add(item.get("id"))
                    elif item.get("type") == "webSearch" and on_search and interrupted_at is None:
                        on_search()
                elif method == "item/agentMessage/delta":
                    delta = params.get("delta")
                    # 差分は文字列だけ使う(null や文字列でない値を連結しない)
                    if interrupted_at is None and params.get("itemId") in items and isinstance(delta, str) and delta:
                        got = True
                        on_text(delta)
                elif method == "error":
                    if not params.get("willRetry"):
                        failure = params.get("error") or {}
                elif method == "turn/completed":
                    done = params.get("turn") or {}
                    if done.get("id") != turn_id:
                        continue
                    if done.get("status") == "failed":
                        raise self._failure(done.get("error") or failure)
                    return
        raise _exit_error("codex", self._err)

    def _failure(self, error):
        error = error if isinstance(error, dict) else {}
        text = str(error.get("message") or "")
        if error.get("codexErrorInfo") == "unauthorized" or authmod.is_auth_error(text):
            self._on_auth()
            return _auth_error("codex")
        return LLMError(text[-300:] or t("llm.codex_error_default"))

    def interrupt(self):
        if self._thread and self._turn_id:
            try:
                self._write({"id": f"i{time.time_ns()}", "method": "turn/interrupt",
                             "params": {"threadId": self._thread, "turnId": self._turn_id}})
            except LLMError:
                pass

    def reset(self):
        """会話を新しくする。プロセスはそのままで、次にスレッドを作り直す。"""
        self._thread = ""
        self._turn_id = ""

    def _kill(self):
        proc, self._proc = self._proc, None
        self._thread = ""
        if proc:
            _kill_tree(proc)

    def close(self):
        with self._startlock:
            self._kill()
        self._set("stopped")


_ALLOWED = sorted((x["e"] for x in prompts._EMOJI), key=len, reverse=True)
_OTHER_EMOJI = re.compile("[🀀-🫿←-⇿⌀-⏿①-➿⤀-⥿⬀-⯿️‍]")


def only_allowed_emoji(text, keep=False):
    """絵文字を落とす。keep(詳しい設定の「絵文字を残す」)の時だけ、絵文字表にあるものを残す。
    表にないものは、モデルが読めずに崩れるので落とす。"""
    held = {}
    for i, e in enumerate(_ALLOWED if keep else ()):
        mark = f"{chr(0xE100 + i)}"
        held[mark] = e
        text = text.replace(e, mark)
    text = _OTHER_EMOJI.sub("", text)
    for mark, e in held.items():
        text = text.replace(mark, e)
    return text


def tidy_say(text, opt=None):
    """読めない記号と顔文字だけを落とす(どこを落とすかは詳しい設定の正規表現)。三点リーダー、伸ばし、！などの書き方は変えない。"""
    opt = opt or tuning.defaults()
    for key in ("kaomoji_pattern", "symbol_pattern"):
        found = tuning.rx(opt[key])
        if found:
            text = found.sub("", text)
    return re.sub(r"^[、。]+", "", text).strip()


# LLM が決める行間(pause)の上限。範囲は LLM に任せるが、壊れた値(桁違いの数)で再生が止まらないための安全弁にだけ使う
PAUSE_MAX = 30.0
_NUMBER = re.compile(r"-?(?:\d+(?:\.\d+)?|\.\d+)")


def parse_pause(value):
    """LLM が書いた pause を秒の数にする。0.8 のほか、"0.8" や "0.8秒"、"500ms" のような文字列も読む。
    数にできない値と有限でない値(nan, inf)は None(= 詳しい設定の既定の間)。負の値は 0。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        found = _NUMBER.search(value)
        if not found:
            return None
        value, millis = found.group(), bool(re.search(r"ms|ミリ", value[found.end():], re.I))
    else:
        millis = False
    try:
        seconds = float(value) / (1000 if millis else 1)
    except (TypeError, ValueError, OverflowError):
        return None
    return min(max(seconds, 0.0), PAUSE_MAX) if math.isfinite(seconds) else None


# 話し方の欄(how / feel / voice)に説明や返事が入ったと見なす文の形: 文の途中の句点・感嘆符、または「です」「ます」などで終わる
_PROSE = re.compile(r"[。！？!?]\s*\S|(?:です|ます|ました|でした|ません|でしょう|ください|ではありません)\s*[。.!！]*\s*$", re.I)


def _manner(value, cap, name):
    """how / feel / voice は話し方を表す短い語句だけ。上限(詳しい設定)を超える、説明文の形をしている値は捨てて、ログに残す。"""
    value = " ".join(str(value or "").split())
    if not value:
        return ""
    if len(value) > cap or _PROSE.search(value.rstrip("。.")):
        from .engine import log
        log(f"dropped {name}: {len(value)} chars (limit {cap}): {value[:24]}...")
        return ""
    return value


def parse_line(text, opt=None):
    """エージェントが書いた 1 行(JSON)を読む。読めなければ None。opt は詳しい設定。"""
    opt = opt or tuning.defaults()
    text = text.strip().strip("`").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start: end + 1])
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    keep = opt["emoji_keep"]
    raw = " ".join(only_allowed_emoji(str(obj.get("say", "")), keep).split())
    # 括弧書きのト書き(「（小声で）」など)は読ませない
    stage = tuning.rx(opt["stage_pattern"])
    say = tidy_say(stage.sub("", raw) if stage else raw, opt)
    if not say:
        return None
    feel = _manner(obj.get("feel"), opt["feel_max"], "feel")
    how = _manner(obj.get("how"), opt["how_max"], "how")
    voice = _manner(obj.get("voice"), opt["voice_max"], "voice")
    return {"say": say, "feel": only_allowed_emoji(feel, keep), "how": how, "voice": voice, "speed": None, "pause": parse_pause(obj.get("pause"))}


_SENTENCE = re.compile(r"[^。！？!?]+(?:[。！？!?]+[」』）)]*)?|[。！？!?]+[」』）)]*", re.S)
_CLAUSE = re.compile(r"[^、]+(?:、+)?|、+", re.S)


def split_say(text, cap):
    """1 行が上限(cap 字)を超えたら、文の切れ目(。！？!?)、なければ読点(、)、それもなければ上限の位置で分ける。
    上限に収まる範囲でなるべく長くつなぐ。"""
    if len(text) <= cap:
        return [text]
    for pattern in (_SENTENCE, _CLAUSE):
        parts = pattern.findall(text)
        if len(parts) < 2:
            continue
        chunks, cur = [], ""
        for part in parts:
            if len(cur) + len(part) <= cap:
                cur += part
                continue
            if cur:
                chunks.append(cur)
            cur = part
        if cur:
            chunks.append(cur)
        return [piece for chunk in chunks for piece in split_say(chunk, cap)]
    return [text[i: i + cap] for i in range(0, len(text), cap)]


class LineSplitter:
    def __init__(self):
        self.buf = ""

    def feed(self, chunk):
        self.buf += chunk
        *done, self.buf = self.buf.split("\n")
        return [ln for ln in done if ln.strip()]

    def flush(self):
        rest, self.buf = self.buf.strip(), ""
        return [rest] if rest else []


class LLM:
    def __init__(self, config, bus):
        self.config, self.bus = config, bus
        self._agents = {}
        self._lock = threading.Lock()
        self._warming = set()
        self.tune = lambda: self.config.get()["tuning"]
        self.auth = authmod.Auth(self.emit, self._logged_in)
        self.on_login = None   # ログインが通った後に呼ぶ(server.py が設定する)

    @staticmethod
    def search_supported(provider):
        """Web 検索を使える接続か(LM Studio は使えない)。"""
        return provider in ("claude", "codex")

    def search_on(self, provider, cfg=None):
        """設定の「Web 検索を使う」がオンで、その接続が検索を使える。"""
        return bool((cfg or self.config.get()).get("web_search", True)) and self.search_supported(provider)

    def _signature(self, provider, cfg):
        # モデル・書き方・形式・READY・検索の可否が変わった時だけ作り直す(依頼の包み方などは毎回読むので作り直さない)。
        # Codex の思考の深さは毎ターン渡すので、変えてもスレッドはそのまま(agent() が値だけ入れ替える)
        llm_cfg = {k: v for k, v in cfg["llm"].get(provider, {}).items() if k != "effort"}
        search = self.search_on(provider, cfg)
        return json.dumps([llm_cfg, prompts.system(cfg["prompts"], cfg["tuning"], search), prompts.ready(cfg["prompts"]), search],
                          sort_keys=True)

    def agent(self):
        cfg = self.config.get()
        provider = cfg["llm_provider"]
        if provider not in PROVIDERS:
            raise LLMError(t("llm.bad_type"))
        sig = self._signature(provider, cfg)
        with self._lock:
            held = self._agents.get(provider)
            if held and held[0] != sig:
                held[1].retired = True   # 書いている途中なら、Live が止められたと分かって新しいセッションで続ける
                held[1].close()
                held = None
            if not held:
                cls = {"claude": ClaudeAgent, "codex": CodexAgent, "lmstudio": LMStudioAgent}[provider]
                search = self.search_on(provider, cfg)
                held = (sig, cls(cfg["llm"].get(provider, {}), self.emit, prompts.system(cfg["prompts"], cfg["tuning"], search),
                               prompts.ready(cfg["prompts"]), on_auth=lambda: self.auth.mark_out(provider), tune=self.tune, search=search))
                self._agents[provider] = held
            elif provider == "codex":
                effort = cfg["llm"]["codex"].get("effort")
                held[1].effort = effort if effort in EFFORTS else DEFAULT_EFFORT
            return held[1]

    @staticmethod
    def availability(provider):
        if provider == "claude" and not shutil.which("claude"):
            return False, t("llm.no_claude")
        if provider == "codex" and not shutil.which("codex"):
            return False, t("llm.no_codex")
        return True, ""

    @staticmethod
    def model_label(provider, model):
        """モデル ID を画面向けの名前にする(claude-haiku-5-5 は Claude Haiku 5.5)。"""
        model = (model or "").strip() or DEFAULT_MODELS.get(provider, "")
        if provider == "claude":
            m = re.match(r"claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$", model)
            if m:
                return f"Claude {m.group(1).title()} {m.group(2)}" + (f".{m.group(3)}" if m.group(3) else "")
            return model if model.lower().startswith("claude") else f"Claude {model.title()}"
        return f"Codex {model}"

    def label(self, provider, cfg, agent):
        """画面に出す「いま書いている LLM」の名前。"""
        model = (cfg["llm"].get(provider, {}).get("model") or "").strip()
        if provider == "lmstudio":
            return model or getattr(agent, "resolved", "") or "LM Studio"
        return self.model_label(provider, model)

    def snapshot(self):
        cfg = self.config.get()
        provider = cfg["llm_provider"]
        held = self._agents.get(provider)
        ok, reason = self.availability(provider)
        if ok and self.auth.blocked(provider):
            ok, reason = False, t("llm.not_signed_in_short", name=authmod.NAMES[provider])
        return {"provider": provider, "state": held[1].state if held else "stopped", "ok": ok, "reason": reason,
                "label": self.label(provider, cfg, held[1] if held else None), "auth": self.auth.snapshot(provider),
                "search": self.search_on(provider, cfg), "search_supported": self.search_supported(provider)}

    def emit(self):
        self.bus.emit("llm", self.snapshot())

    def warm(self):
        """Claude / Codex が選ばれていれば、ログイン状態を確かめてから裏で起動して温める(最初の 1 行を早く鳴らすため)。"""
        provider = self.config.get()["llm_provider"]
        if provider not in ("claude", "codex") or provider in self._warming or not self.availability(provider)[0]:
            return

        def run():
            try:
                self.auth.check(provider)
                self.emit()
                if not self.auth.blocked(provider):
                    self.agent().start()
            except LLMError:
                pass
            finally:
                self._warming.discard(provider)
        self._warming.add(provider)
        threading.Thread(target=run, daemon=True).start()

    def refresh(self):
        self.emit()
        self.warm()

    def reset(self):
        agent = self._agents.get(self.config.get()["llm_provider"])
        if agent:
            agent[1].reset()
        self.warm()

    def close(self):
        for _, agent in list(self._agents.values()):
            agent.close()

    # --- ログイン -----------------------------------------------------------------------------------------------
    def login(self, provider):
        try:
            self.auth.login(provider)
        except ValueError as e:
            raise LLMError(str(e))
        self.emit()

    def cancel_login(self, provider):
        self.auth.cancel(provider)

    def recheck(self, provider):
        """ログイン状態を調べ直す(「状態を確認」ボタン)。"""
        self.auth.check(provider)
        self.emit()
        self.warm()

    def _logged_in(self, provider):
        """ログインが通った: 古いセッションを捨てて張り直し、待っていた依頼を再開させる。"""
        with self._lock:
            held = self._agents.pop(provider, None)
        if held:
            held[1].close()
        self.emit()
        self.warm()
        if self.on_login:
            self.on_login(provider)

    def test(self, provider, draft=None):
        """接続テスト。draft は画面に入っている未保存の LLM 設定で、あればそれでテストする(設定は書き換えない)。"""
        started = time.time()
        cfg = _with_draft(self.config.get()["llm"], draft)
        custom = self.config.get()["prompts"]
        ping = prompts.ping(custom)
        try:
            if provider == "lmstudio":
                models = lmstudio_models(cfg["lmstudio"])
                result = {"ok": bool(models), "models": models,
                          "detail": (t("llm.test_lm_ok", models=", ".join(models[:5])) if models
                                     else t("llm.test_lm_none"))}
            elif provider == "claude":
                reply = self._oneshot("claude", [_claude_exe(), "-p", "--model", cfg["claude"].get("model") or DEFAULT_MODELS["claude"],
                                                 "--tools", "", "--strict-mcp-config", "--setting-sources", "",
                                                 "--no-session-persistence", ping])
                result = {"ok": True, "detail": t("llm.test_reply", reply=reply[:40])}
            else:
                # 本番と同じ常駐の経路で確かめる(app-server を起動して 1 ターン)
                agent = CodexAgent(cfg["codex"], lambda: None, prompts.ping_system(custom),
                                   on_auth=lambda: self.auth.mark_out("codex"), tune=self.tune)
                reply = []
                try:
                    agent.turn(ping, reply.append, lambda: False)
                finally:
                    agent.close()
                result = {"ok": True, "detail": t("llm.test_reply", reply="".join(reply).strip()[:40])}
        except LLMError as e:
            result = {"ok": False, **explain_failure(provider, str(e), cfg, e.code)}
        result["seconds"] = round(time.time() - started, 1)
        return result

    def _oneshot(self, provider, cmd):
        try:
            done = subprocess.run(cmd, capture_output=True, timeout=120, stdin=subprocess.DEVNULL,
                                  cwd=tempfile.gettempdir(), creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise LLMError(t("llm.unreachable_oneshot"), "timeout")
        except OSError as e:
            raise LLMError(t("llm.oneshot_start_failed", e=e))
        out = done.stdout.decode("utf-8", "replace").strip()
        err = done.stderr.decode("utf-8", "replace").strip()
        if done.returncode != 0 or not out:
            text = (err or out)[-300:]
            if authmod.is_auth_error(text):
                self.auth.mark_out(provider)
                raise _auth_error(provider)
            raise LLMError(text or t("llm.failed_code", code=done.returncode))
        return out
