"""専用の窓で開く。pywebview(Windows は WebView2、macOS は WKWebView)で Kataribe 自身の窓を開く。
使えない時(ソースから起動して pywebview が未インストール、WebView2 が無い等)は、Edge / Chrome / Brave のアプリモード(--app)、それも無ければ既定のブラウザの順に落とす。"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

from .config import BUNDLE, IS_MAC, IS_WINDOWS
from .i18n import t

# 窓の画面(SSE)がすべて切れてから、この秒数戻らなければ終了する(再読み込みでは戻るので止まらない)。
GONE_SECONDS = 10
# 起動してから、最初の画面がつながるまで待つ最長(秒)。これを過ぎてもつながらなければ終了する。
FIRST_SECONDS = 120


def _candidates():
    if IS_WINDOWS:
        bases = [os.environ.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        rels = [Path("Microsoft/Edge/Application/msedge.exe"), Path("Google/Chrome/Application/chrome.exe"),
                Path("BraveSoftware/Brave-Browser/Application/brave.exe")]
        return [Path(b) / r for r in rels for b in bases if b]
    if IS_MAC:
        apps = [("Google Chrome", "Google Chrome"), ("Microsoft Edge", "Microsoft Edge"), ("Brave Browser", "Brave Browser")]
        roots = [Path("/Applications"), Path.home() / "Applications"]
        return [root / f"{app}.app" / "Contents" / "MacOS" / exe for app, exe in apps for root in roots]
    names = ("microsoft-edge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "brave-browser")
    return [Path(p) for p in map(shutil.which, names) if p]


def find_browser():
    """アプリモードで開けるブラウザの実行ファイル。無ければ None。"""
    return next((p for p in _candidates() if p.is_file()), None)


def _settings_base():
    if IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    elif IS_MAC:
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "Kataribe"


def profile_dir(port):
    """--app の窓の大きさなどを覚えておく専用のプロファイル。アプリのフォルダーではなく OS の設定フォルダーに置く。"""
    return _settings_base() / f"window-{port}"


def app_command(browser, url, port):
    return [str(browser), f"--app={url}", f"--user-data-dir={profile_dir(port)}", "--window-size=1280,860",
            "--no-first-run", "--no-default-browser-check"]


TITLE = "Kataribe"
APP_ID = "jp.ada-software.kataribe"
_desktop = {"window": None}


# 右クリックで出す項目(WebView2 の名前)。入力欄の編集に要るものだけ。戻る・再読み込み・印刷などは出さない
_EDIT_MENU = {"undo", "redo", "cut", "copy", "paste", "pasteAndMatchStyle", "selectAll", "emoji", "spellCheck"}


def _enable_edit_menu():
    """pywebview は debug の時しか右クリックのメニューを出さない。Windows では編集の項目だけを出す。"""
    if not IS_WINDOWS:
        return
    try:
        from webview.platforms.edgechromium import EdgeChrome
    except ImportError:
        return
    ready = EdgeChrome.on_webview_ready
    if getattr(ready, "_kataribe", False):
        return

    def on_menu(_, args):
        items = args.MenuItems
        for i in range(items.Count - 1, -1, -1):
            if items[i].Name not in _EDIT_MENU:
                items.RemoveAt(i)
        # 項目を抜いた後に残る区切り線(先頭・末尾・続けて並んだもの)を消す
        sep = lambda k: items[k].Kind.ToString() == "Separator"
        for i in range(items.Count - 1, -1, -1):
            if sep(i) and (i == 0 or i == items.Count - 1 or sep(i - 1)):
                items.RemoveAt(i)
        if not items.Count:
            args.Handled = True   # 出す項目が無い場所では、空のメニューを出さない

    def patched(self, sender, args):
        ready(self, sender, args)
        try:
            sender.CoreWebView2.Settings.AreDefaultContextMenusEnabled = True
            sender.CoreWebView2.ContextMenuRequested += on_menu
        except Exception:
            pass

    patched._kataribe = True
    EdgeChrome.on_webview_ready = patched


def _install_pywebview(log):
    """ソースから起動して pywebview が無い時だけ、requirements.txt を pip で入れる(入っていれば何もしない)。
    配布版(exe / .app)は同梱なので通らない。入れられなければ False(ブラウザの窓に落とす)。"""
    if getattr(sys, "frozen", False):
        return False
    log(t("win.installing_pywebview"))
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(BUNDLE / "requirements.txt")]
    if not (sys.prefix != sys.base_prefix):   # 仮想環境の外では、ユーザーの場所に入れる(管理者権限を要らなくする)
        cmd.insert(4, "--user")
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=120,
                              creationflags=subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0)
    except (OSError, subprocess.SubprocessError) as e:
        log(f"pip: {e}")
        return False
    if done.returncode != 0:
        log("pip: " + done.stderr.decode("utf-8", "replace").strip()[-300:])
        return False
    import importlib
    import site
    importlib.invalidate_caches()
    user_site = site.getusersitepackages()
    if user_site not in sys.path:
        sys.path.append(user_site)
    return True


def run_desktop(url, log):
    """pywebview の窓で開き、窓が閉じるまで戻らない(メインスレッドで呼ぶ。macOS の制約)。
    窓を開いて閉じるまで通ったら True。pywebview を使えなかった時は False(呼び出し側が --app 窓に落とす)。"""
    try:
        import webview
    except ImportError:
        if not _install_pywebview(log):
            log(t("win.no_pywebview"))
            return False
        try:
            import webview
        except ImportError:
            log(t("win.no_pywebview"))
            return False
    if IS_WINDOWS:
        try:
            import ctypes
            # タスクバーに python.exe ではなく Kataribe として出す
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (OSError, AttributeError):
            pass
    _enable_edit_menu()
    try:
        _desktop["window"] = webview.create_window(TITLE, url, width=1280, height=860, min_size=(900, 600))
        storage = _settings_base() / "webview"
        storage.mkdir(parents=True, exist_ok=True)
        icon = BUNDLE / "packaging" / ("icon.ico" if IS_WINDOWS else "icon.png")
        # private_mode=False で、テーマなどの設定(localStorage)と、マイクの許可を次回以降に残す
        webview.start(icon=str(icon) if icon.is_file() else None, private_mode=False, storage_path=str(storage))
    except Exception as e:
        log(t("win.desktop_failed", e=f"{type(e).__name__}: {e}"))
        return False
    finally:
        _desktop["window"] = None
    log(t("win.closed"))
    return True


def close_desktop():
    """pywebview の窓を閉じる(別のスレッドから呼べる)。窓が無ければ何もしない。"""
    win = _desktop["window"]
    if win is not None:
        try:
            win.destroy()
        except Exception:
            pass


def open_window(url, port, log):
    """専用の窓で開く。開いた窓のプロセスを返す。窓を開けず既定のブラウザで開いた場合は None。
    窓が閉じたかどうかはプロセスでは見ない(Edge がすでに動いていると、窓は既存のプロセスに渡って、こちらのプロセスはすぐ終わるため)。"""
    browser = find_browser()
    if not browser:
        log(t("win.no_browser"))
        webbrowser.open(url)
        return None
    cmd = app_command(browser, url, port)
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        log(t("win.browser_failed", name=browser.name, e=e))
        webbrowser.open(url)
        return None
    log(t("win.app_window", name=browser.name))
    return proc


def watch_gone(count, on_gone, log, gone=GONE_SECONDS, first=FIRST_SECONDS, tick=0.5):
    """画面とのつながり(count() が返す数)を見張り、全部切れて gone 秒戻らなければ on_gone を呼ぶ。
    最初のつながりが first 秒たっても来なければ、窓が開かなかったとみなして同じく呼ぶ。"""
    def run():
        start = time.time()
        seen, empty_since = False, None
        while True:
            time.sleep(tick)
            now = time.time()
            if count() > 0:
                seen, empty_since = True, None
                continue
            if not seen:
                if now - start > first:
                    log(t("win.never_connected"))
                    return on_gone()
                continue
            empty_since = empty_since or now
            if now - empty_since >= gone:
                log(t("win.closed"))
                return on_gone()
    threading.Thread(target=run, daemon=True).start()


def close_window(proc):
    if proc and proc.poll() is None:
        try:
            proc.terminate()
        except OSError:
            pass
