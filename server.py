import argparse
import json
import mimetypes
import queue
import re
import select
import signal
import socket
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from irodori import VERSION
from irodori import cli, history, i18n, library, prompts, tuning, voices, window
from irodori.i18n import t
from irodori.config import BUNDLE, DEFAULT_MODELS, EFFORTS, IS_MAC, IS_WINDOWS, MODEL_CHOICES, Config
from irodori.engine import Engine, EngineError, list_devices
from irodori.events import Bus
from irodori.installer import Installer, list_models
from irodori.live import Live
from irodori.llm import LLM, PROVIDERS, LLMError, lmstudio_models

# `server.py send "…"` のように操作名が付いていれば、起動中の Kataribe を外から動かす CLI(irodori/cli.py)として働く
if __name__ == "__main__":
    if any(arg in cli.COMMANDS for arg in sys.argv[1:]):
        sys.exit(cli.main(sys.argv[1:]))

# Mac の .app では static/ が Contents/Resources へのシンボリックリンクなので、実体の場所で比べる(比べ方がずれると全部「見つかりません」になる)
STATIC = (BUNDLE / "static").resolve()
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")

bus = Bus()
config = Config()
engine = Engine(config, bus)
llm = LLM(config, bus)
live = Live(config, engine, llm, bus)
installer = Installer(config, engine, bus)


class ApiError(Exception):
    def __init__(self, message, status=HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


def voice_list():
    return voices.list_voices(config.path("voices_dir"), engine.voices, config.get()["default_voice"])


def full_state():
    return {"version": VERSION, "platform": "windows" if IS_WINDOWS else "mac" if IS_MAC else "linux",
            "config": config.get(), "lang": i18n.lang(), "os_lang": i18n.detect(), "langs": i18n.LANGS, "engine": engine.snapshot(), "setup": installer.snapshot(), "llm": llm.snapshot(), "voices": voice_list(),
            "live": live.snapshot(),
            "model_choices": MODEL_CHOICES, "default_models": DEFAULT_MODELS, "efforts": EFFORTS,
            "prompt_defaults": prompts.DEFAULTS, "prompt_vars": prompts.VARS,
            "prompt_limits": {key: prompts.limit(key) for key in prompts.DEFAULTS},
            "tuning_spec": tuning.spec()}


def api_config_put(body):
    if not isinstance(body, dict):
        raise ApiError(t("srv.bad_config"))
    if "prompts" in body:
        if not isinstance(body["prompts"], dict):
            raise ApiError(t("srv.bad_prompts"))
        cleaned = {}
        for key, value in body["prompts"].items():
            if key not in prompts.DEFAULTS:
                continue
            if not isinstance(value, str):
                raise ApiError(t("srv.prompt_not_text", key=key))
            if len(value.strip()) > prompts.limit(key):
                raise ApiError(t("srv.prompt_too_long", key=key, n=prompts.limit(key)))
            # 既定と同じなら空で持つ(既定の文が更新された時にそちらに追従する)
            cleaned[key] = "" if value.strip() == prompts.DEFAULTS[key] else value.strip()
        body = dict(body, prompts=cleaned)
    if "llm" in body:
        codex = (body["llm"] or {}).get("codex") if isinstance(body["llm"], dict) else None
        if isinstance(codex, dict) and "effort" in codex and codex["effort"] not in EFFORTS:
            raise ApiError(t("srv.bad_effort", list=" / ".join(EFFORTS)))
    if "default_how" in body:
        if not isinstance(body["default_how"], str):
            raise ApiError(t("srv.bad_config"))
        body = dict(body, default_how=body["default_how"].strip()[:200])
    for key in ("speak_language", "tts_voice"):
        if key in body and not isinstance(body[key], str):
            raise ApiError(t("srv.bad_config"))
    if "web_search" in body and not isinstance(body["web_search"], bool):
        raise ApiError(t("r20.bad_web_search"))
    if "shortcuts" in body and not (isinstance(body["shortcuts"], dict) and all(isinstance(v, str) for v in body["shortcuts"].values())):
        raise ApiError(t("r22.bad_shortcuts"))
    if "language" in body and body["language"] not in (i18n.AUTO, *i18n.LANGS):
        raise ApiError(t("srv.bad_language"))
    if "speech_speed" in body:
        try:
            speed = float(body["speech_speed"])
        except (TypeError, ValueError):
            raise ApiError(t("srv.speed_number"))
        if not 0.7 <= speed <= 1.6:
            raise ApiError(t("srv.speed_range"))
        body = dict(body, speech_speed=speed)
    if "tuning" in body:
        try:
            body = dict(body, tuning=tuning.clean(body["tuning"]))
        except ValueError as e:
            raise ApiError(str(e))
    new = config.update(body)
    llm.refresh()
    if "default_voice" in body or "default_how" in body:
        live.apply_default_voice()
    return new


def api_prompts_reset(_):
    """LLM に渡す文面すべての既定を返す。保存はしない(画面は保存前の状態として扱い、「保存」で初めて config.json に入る。
    保存時に既定と同じ文は空で持つので、そのまま保存すれば既定に戻る)。"""
    return {"prompts": dict(prompts.DEFAULTS)}


def api_voices_import(body):
    try:
        result = voices.import_path(str(body.get("path", "")).strip(), config.path("voices_dir"))
    except ValueError as e:
        raise ApiError(str(e))
    files = engine.voice_files()
    failed = False
    for name in result["copied"]:
        if name in files and engine.state == "ready":
            try:
                engine.add_voice(name, files[name])
            except EngineError:
                failed = True
    if failed:
        # 登録できなかった声は、起動し直して voices/ から読み込ませる
        bus.emit("live_notice", {"text": t("srv.voices_reload"), "kind": "ok"})
        threading.Thread(target=engine.restart, daemon=True).start()
    result["voices"] = voice_list()
    return result


def api_voices_delete(body):
    name = str(body.get("name", ""))
    voices.delete(config.path("voices_dir"), name)   # 画面の一覧が古くて、もう無い声を消そうとしても、エラーにせず一覧を返す
    live.forget_voice(name)
    if config.get()["default_voice"] == name:
        config.update({"default_voice": ""})
    return {"voices": voice_list()}


def need(body, key):
    value = str(body.get(key, "")).strip()
    if not value:
        raise ApiError(t("srv.empty"))
    return value


def live_call(fn, *args):
    try:
        return fn(*args)
    except ValueError as e:
        raise ApiError(str(e))


def api_live_send(body):
    live_call(live.send, need(body, "text"))
    return {"ok": True}


def api_live_played(body):
    return {"ok": live.mark_played(need(body, "id"), bool(body.get("done")), bool(body.get("failed")))}


def api_player_claim(body):
    """声を鳴らす画面は 1 つだけ。別のブラウザやデスクトップ版の窓にも、担当が替わったことを伝える。"""
    bus.emit("player", {"id": str(need(body, "id"))})
    return {"ok": True}


def api_live_star(body):
    done = live_call(live.star, need(body, "id"), bool(body.get("value", True)))
    if done is None:
        raise ApiError(t("srv.audio_gone"))
    return dict(done, dir=done.pop("out", None) or str(config.path("output_dir")))


def api_live_use_voice(body):
    name = need(body, "name")
    saved = next((n for n in engine.voice_files() if n.lower() == name.lower()), None)
    if not saved:
        raise ApiError(t("srv.not_found"), HTTPStatus.NOT_FOUND)
    live.use_voice(saved)
    return {"voice": saved}


def api_live_save_voice(body):
    return {"name": live_call(live.save_voice, str(body.get("name", "")).strip() or "voice"), "voices": voice_list()}


def api_history_open(body):
    try:
        live.open_history(need(body, "id"))
    except (OSError, ValueError, KeyError):
        raise ApiError(t("srv.history_gone"))
    return {"ok": True}


def api_history_delete(body):
    try:
        live.delete_history(need(body, "id"))
    except ValueError:
        raise ApiError(t("srv.history_gone"))
    return {"items": history.items()}


def api_live_action(name):
    def run(_):
        getattr(live, name)()
        return {"ok": True}
    return run


def api_llm_test(body):
    provider = body.get("provider") or config.get()["llm_provider"]
    if provider not in PROVIDERS:
        raise ApiError(t("srv.bad_provider"))
    return llm.test(provider, body.get("llm"))


def llm_provider(body):
    provider = body.get("provider") or config.get()["llm_provider"]
    if provider not in PROVIDERS:
        raise ApiError(t("srv.bad_provider"))
    return provider


def api_llm_login(body):
    try:
        llm.login(llm_provider(body))
    except LLMError as e:
        raise ApiError(str(e))
    return llm.snapshot()


def api_llm_login_cancel(body):
    llm.cancel_login(llm_provider(body))
    return llm.snapshot()


def api_llm_auth(body):
    llm.recheck(llm_provider(body))
    return llm.snapshot()


def api_llm_models(_):
    try:
        return {"models": lmstudio_models(config.get()["llm"]["lmstudio"])}
    except LLMError as e:
        raise ApiError(str(e), HTTPStatus.BAD_GATEWAY)


def api_open_folder(body):
    key = {"output": "output_dir", "voices": "voices_dir"}.get(body.get("which"))
    if not key:
        raise ApiError(t("srv.bad_folder"))
    library.open_folder(config.path(key))
    return {"ok": True}


def api_setup_install(_):
    """足りない speech.cpp とモデルを入れる(入っているものは使う)。進み具合は SSE の "setup" で送る。"""
    installer.start()
    return installer.snapshot()


def api_tts_models(_):
    """選べる音声合成のモデル(speech.cpp の一覧)と、いま読み込んでいるモデルでできること。"""
    return {"models": list_models(config.get()), "loaded": engine.model_summary(), "speak": live.speak_language()}


def api_engine_action(action):
    def run(_):
        getattr(engine, action)()
        return engine.snapshot()
    return run


# (method, pattern, handler(body, *groups))。SSE・音声・静的ファイルは Handler で個別に処理する。
ROUTES = [
    ("GET", r"/api/state", lambda b: full_state()),
    ("GET", r"/api/config", lambda b: config.get()),
    ("PUT", r"/api/config", api_config_put),
    ("GET", r"/api/devices", lambda b: {"devices": list_devices(config.get()["speech_exe"])}),
    ("POST", r"/api/prompts/reset", api_prompts_reset),
    ("POST", r"/api/open", api_open_folder),
    ("GET", r"/api/tts/models", api_tts_models),
    ("POST", r"/api/engine/start", api_engine_action("start")),
    ("POST", r"/api/engine/stop", api_engine_action("stop")),
    ("POST", r"/api/engine/restart", api_engine_action("restart")),
    ("POST", r"/api/setup/install", api_setup_install),
    ("GET", r"/api/voices", lambda b: {"voices": voice_list()}),
    ("POST", r"/api/voices/import", api_voices_import),
    ("POST", r"/api/voices/delete", api_voices_delete),
    ("GET", r"/api/live", lambda b: live.snapshot()),
    ("POST", r"/api/live/send", api_live_send),
    ("POST", r"/api/live/played", api_live_played),
    ("POST", r"/api/player/claim", api_player_claim),
    ("POST", r"/api/live/stop", api_live_action("stop")),
    ("POST", r"/api/live/resume", api_live_action("resume")),
    ("POST", r"/api/live/reset", api_live_action("reset")),
    ("POST", r"/api/live/retry", api_live_action("retry")),
    ("POST", r"/api/live/new_voice", api_live_action("new_voice")),
    ("POST", r"/api/live/star", api_live_star),
    ("POST", r"/api/live/save_voice", api_live_save_voice),
    ("POST", r"/api/live/use_voice", api_live_use_voice),
    ("GET", r"/api/history", lambda b: {"items": history.items()}),
    ("POST", r"/api/history/open", api_history_open),
    ("POST", r"/api/history/delete", api_history_delete),
    ("POST", r"/api/llm/test", api_llm_test),
    ("POST", r"/api/llm/login", api_llm_login),
    ("POST", r"/api/llm/login/cancel", api_llm_login_cancel),
    ("POST", r"/api/llm/auth", api_llm_auth),
    ("GET", r"/api/llm/models", api_llm_models),
]
ROUTES = [(m, re.compile(p + "$"), h) for m, p, h in ROUTES]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "IrodoriWebUI"

    def log_message(self, *args):
        pass

    def _allowed_host(self):
        """Host は自分(ループバック)だけ。Origin が付いた要求は、この画面と同じスキーム・ホスト・ポートだけ通す
        (同じ PC の別のポートで開いたページから、声の削除やリセットを送らせない)。Origin の無い要求は CLI など。"""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        origin = self.headers.get("Origin")
        if host not in ("127.0.0.1", "localhost", "::1"):
            return False
        if not origin:
            return True
        o = urlparse(origin)
        return o.scheme == "http" and o.netloc.lower() == (self.headers.get("Host") or "").lower() and o.port == self.server.server_address[1]

    def _send(self, status, body, ctype, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, status=HTTPStatus.OK):
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            raise ApiError(t("srv.bad_body"))

    def _dispatch(self):
        if not self._allowed_host():
            return self._json({"error": t("srv.forbidden")}, HTTPStatus.FORBIDDEN)
        url = urlparse(self.path)
        path = unquote(url.path)
        try:
            if self.command == "GET":
                if path == "/api/events":
                    return self._sse()
                m = re.fullmatch(r"/api/live/audio/(\w+)\.wav", path)
                if m:
                    return self._audio(m.group(1))
                if not path.startswith("/api/"):
                    return self._static(path)
            for method, pattern, handler in ROUTES:
                m = pattern.match(path)
                if method == self.command and m:
                    body = self._read_body() if method in ("POST", "PUT", "DELETE") else {}
                    return self._json(handler(body, *m.groups()) if m.groups() else handler(body))
            self._json({"error": t("srv.not_found")}, HTTPStatus.NOT_FOUND)
        except ApiError as e:
            self._json({"error": str(e)}, e.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            self._json({"error": t("srv.internal", name=type(e).__name__, msg=e)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _dispatch

    def _static(self, path):
        rel = "index.html" if path in ("", "/") else path.removeprefix("/static/").lstrip("/")
        target = (STATIC / rel).resolve()
        if STATIC not in target.parents and target != STATIC / "index.html" or not target.is_file():
            return self._json({"error": t("srv.not_found")}, HTTPStatus.NOT_FOUND)
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith(("json", "javascript")):
            ctype += "; charset=utf-8"
        self._send(HTTPStatus.OK, target.read_bytes(), ctype)

    def _audio(self, line_id):
        path = live.wav_path(line_id)
        if not path:
            return self._json({"error": t("srv.not_found")}, HTTPStatus.NOT_FOUND)
        self._send(HTTPStatus.OK, path.read_bytes(), "audio/wav")

    def _peer_closed(self):
        """相手が接続を閉じたか(読み取れる状態で 0 バイトなら閉じている)。"""
        try:
            if not select.select([self.connection], [], [], 0)[0]:
                return False
            return self.connection.recv(1, socket.MSG_PEEK) == b""
        except (OSError, ValueError):
            return True

    def _sse(self):
        q = bus.subscribe()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            first = f"event: snapshot\ndata: {json.dumps(full_state(), ensure_ascii=False)}\n\n"
            self.wfile.write(first.encode("utf-8"))
            self.wfile.flush()
            quiet = 0
            while True:
                try:
                    payload = q.get(timeout=1)
                    quiet = 0
                except queue.Empty:
                    # 窓が閉じられたら、すぐ気づいて接続を数から外す(書き込みの失敗を待つと遅れる)
                    if self._peer_closed():
                        break
                    quiet += 1
                    if quiet < 10:
                        continue
                    quiet, payload = 0, ": keepalive\n\n"
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            bus.unsubscribe(q)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def pick_port(preferred):
    for port in range(preferred, preferred + 20):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit(t("srv.no_port"))


def main():
    parser = argparse.ArgumentParser(description="Kataribe")
    parser.add_argument("--port", type=int, default=7865)
    how = parser.add_mutually_exclusive_group()
    how.add_argument("--browser", action="store_true", help=t("srv.arg_browser"))
    how.add_argument("--no-browser", action="store_true", help=t("srv.arg_no_browser"))
    args = parser.parse_args()

    port = pick_port(args.port)
    httpd = Server(("127.0.0.1", port), Handler)

    closing = threading.Lock()
    closed = []

    def shutdown(*_):
        # 窓を閉じた時、画面の接続が切れた時、Ctrl+C が重なっても、止める処理は 1 回だけ
        with closing:
            if closed:
                return
            closed.append(True)
        window.close_desktop()
        llm.close()
        engine.stop()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), shutdown)

    def log(text):
        if sys.stdout:
            print(f"Kataribe: {text}", flush=True)

    url = f"http://127.0.0.1:{port}/"
    log(url)
    # CLI(irodori/cli.py)がポートを探さずに済むよう、起動中だけ控えを置く
    try:
        cli.PORT_FILE.write_text(str(port), encoding="utf-8")
    except OSError:
        pass

    def after_login(provider):
        live.after_login()
        bus.emit("live_notice", {"text": t("srv.login_done"), "kind": "ok"})
    llm.on_login = after_login
    engine.start()
    llm.warm()
    # サーバーは別スレッドで動かし、メインスレッドは窓のため(macOS は窓をメインスレッドで動かす必要がある)に空けておく
    serving = threading.Thread(target=httpd.serve_forever, daemon=True)
    serving.start()
    win = None
    try:
        if args.browser:
            threading.Timer(0.5, webbrowser.open, args=(url,)).start()
        elif not args.no_browser:
            # 既定は Kataribe 自身の窓(pywebview)。窓を閉じたらサーバーも終わる。
            # 使えなければ Edge / Chrome の --app 窓、さらに既定のブラウザへ。それらは画面との接続(SSE)が全部切れたことで閉じたと見る
            if window.run_desktop(url, log):
                shutdown()
            else:
                win = window.open_window(url, port, log)
                if win:
                    window.watch_gone(bus.count, shutdown, log)
        while serving.is_alive():
            serving.join(0.5)
    finally:
        shutdown()
        serving.join(5)
        window.close_window(win)
        live.close()
        llm.close()
        engine.stop()
        httpd.server_close()
        try:
            if cli.PORT_FILE.read_text(encoding="utf-8").strip() == str(port):
                cli.PORT_FILE.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
