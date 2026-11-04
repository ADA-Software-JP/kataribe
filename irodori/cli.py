"""起動中の Kataribe を、ターミナルやコーディングエージェントから動かす CLI。
`python server.py send "寝る前の話をして"` / `Kataribe.exe send …` のように、最初の引数に操作名を渡す。
画面(窓)が開いている Kataribe に、ローカルの HTTP API(127.0.0.1)で頼むだけなので、声はいつもどおり窓から鳴る。"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from . import VERSION
from .config import ROOT

PORTS = range(7865, 7885)   # server.py の pick_port と同じ範囲
SERVER = "IrodoriWebUI"     # server.py の Handler.server_version
PORT_FILE = ROOT / "kataribe.port"   # server.py が起動中だけ置く、待ち受けポートの控え
COMMANDS = ("send", "say", "pause", "resume", "reset", "status", "voices", "voice", "new-voice", "save-voice", "prompt")


# OS のプロキシ設定を通さず、自分の PC へ直接つなぐ
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class CliError(Exception):
    pass


def _request(port, method, path, body=None, timeout=10):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with OPENER.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}"), r.headers.get("Server", "")
    except urllib.error.HTTPError as e:
        try:
            message = json.loads(e.read().decode("utf-8")).get("error")
        except ValueError:
            message = ""
        raise CliError(message or f"HTTP {e.code}")


def find_port(preferred=None):
    """Kataribe が待ち受けているポート。--port が無ければ、起動中の Kataribe が書いたポートの控え(PORT_FILE)、
    次に 7865 から順に、Kataribe のサーバーかを確かめて探す。"""
    candidates = [preferred] if preferred else []
    if not preferred:
        try:
            candidates.append(int(PORT_FILE.read_text(encoding="utf-8").strip()))
        except (OSError, ValueError):
            pass
        candidates += [p for p in PORTS if p not in candidates]
    for port in candidates:
        try:
            _, server = _request(port, "GET", "/api/config", timeout=1.5)
        except (OSError, CliError, ValueError):
            continue
        if server.startswith(SERVER):
            return port
    raise CliError("Kataribe is not running. Start it first (Kataribe.exe / python server.py), then run this again.")


def _voices(port):
    return [v["name"] for v in _request(port, "GET", "/api/voices")[0].get("voices", [])]


def _print(data, as_json):
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif isinstance(data, dict):
        for key, value in data.items():
            print(f"{key}: {value}")
    elif isinstance(data, list):
        for item in data:
            print(item)
    else:
        print(data)


def _text(args):
    if args.file:
        return Path(args.file).read_text(encoding="utf-8")
    text = " ".join(args.text)
    if text == "-":
        return sys.stdin.read()
    return text


def build_parser():
    p = argparse.ArgumentParser(prog="kataribe", description=(
        "Control a running Kataribe from the terminal or a coding agent. "
        "The app window must be open; the voice plays there."))
    p.add_argument("--port", type=int, help="port of the running Kataribe (default: search 7865-7884)")
    p.add_argument("--json", action="store_true", help="print results as JSON")
    p.add_argument("--version", action="version", version=f"Kataribe {VERSION}")
    sub = p.add_subparsers(dest="command", required=True, metavar="command")

    def text_cmd(name, help_):
        c = sub.add_parser(name, help=help_)
        c.add_argument("text", nargs="*", help='the text ("-" reads stdin)')
        c.add_argument("--file", help="read the text from a UTF-8 file")
        return c

    text_cmd("send", "send a request or a remark mid-stream, like typing it in the input box")
    text_cmd("say", "read the text as is, without the LLM (one line = one clip)")
    sub.add_parser("pause", help="pause (same as Esc)")
    sub.add_parser("resume", help="resume after a pause")
    sub.add_parser("reset", help="start over: clear the conversation and the LLM session")
    sub.add_parser("status", help="show what is happening now")
    sub.add_parser("voices", help="list the saved voices")
    c = sub.add_parser("voice", help="switch to a saved voice")
    c.add_argument("name")
    sub.add_parser("new-voice", help="make a new voice from the next line")
    c = sub.add_parser("save-voice", help="save the current voice under a name")
    c.add_argument("name")
    c = sub.add_parser("prompt", help="show or change a text given to the LLM (the system prompt is 'style' + 'format')")
    c.add_argument("key", nargs="?", help="style, format, ready, request, steer, ... (omit to list the keys)")
    c.add_argument("--set", dest="value", help="new text (\"-\" reads stdin)")
    c.add_argument("--file", help="read the new text from a UTF-8 file")
    c.add_argument("--reset", action="store_true", help="go back to the built-in text")
    return p


def run(args):
    port = find_port(args.port)
    cmd = args.command
    if cmd in ("send", "say"):
        text = _text(args).strip()
        if not text:
            raise CliError("nothing to send")
        if cmd == "say":
            text = "".join(f"「{ln.strip()}」" for ln in text.splitlines() if ln.strip())
        _request(port, "POST", "/api/live/send", {"text": text})
        return {"ok": True}
    if cmd in ("pause", "resume", "reset"):
        _request(port, "POST", f"/api/live/{'stop' if cmd == 'pause' else cmd}", {})
        return {"ok": True}
    if cmd == "new-voice":
        _request(port, "POST", "/api/live/new_voice", {})
        return {"ok": True}
    if cmd == "voice":
        return _request(port, "POST", "/api/live/use_voice", {"name": args.name})[0]
    if cmd == "save-voice":
        return {"name": _request(port, "POST", "/api/live/save_voice", {"name": args.name})[0]["name"]}
    if cmd == "voices":
        return _voices(port)
    if cmd == "status":
        live = _request(port, "GET", "/api/live")[0]
        lines = [e for e in live.get("log", []) if e.get("kind") == "line"]
        return {"port": port, "running": live.get("running"), "voice": live.get("voice"), "voice_state": live.get("voice_state"),
                "how": live.get("how"), "writer": (live.get("agent") or {}).get("state"),
                "error": (live.get("agent") or {}).get("error") or live.get("tts_error") or "",
                "lines": len(lines), "played": sum(e.get("state") == "played" for e in lines),
                "last": next((e["text"] for e in reversed(lines) if e.get("state") in ("playing", "played")), "")}
    if cmd == "prompt":
        state = _request(port, "GET", "/api/state")[0]
        defaults, saved = state["prompt_defaults"], state["config"]["prompts"]
        if not args.key:
            return list(defaults)
        if args.key not in defaults:
            raise CliError(f"unknown key: {args.key} (one of {', '.join(defaults)})")
        if args.reset:
            value = ""
        elif args.file:
            value = Path(args.file).read_text(encoding="utf-8")
        elif args.value == "-":
            value = sys.stdin.read()
        elif args.value is not None:
            value = args.value
        else:
            return saved.get(args.key) or defaults[args.key]
        _request(port, "PUT", "/api/config", {"prompts": {args.key: value.strip()}})
        return {"ok": True, "key": args.key}
    raise CliError(f"unknown command: {cmd}")


def main(argv):
    # Windows のコンソールでも日本語を化けさせない
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    # --json と --port は操作名の後ろに書いても効くように、前へ移してから読む
    front, rest, i = [], [], 0
    while i < len(argv):
        if argv[i] == "--json":
            front.append(argv[i])
        elif argv[i] == "--port" and i + 1 < len(argv):
            front += argv[i:i + 2]
            i += 1
        elif argv[i].startswith("--port="):
            front.append(argv[i])
        else:
            rest.append(argv[i])
        i += 1
    args = build_parser().parse_args(front + rest)
    try:
        result = run(args)
    except CliError as e:
        print(f"kataribe: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"kataribe: {e}", file=sys.stderr)
        return 1
    _print(result, args.json)
    return 0
