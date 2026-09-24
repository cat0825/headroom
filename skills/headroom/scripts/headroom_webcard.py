"""A small native WebView2 tray card sharing the dashboard's local media player."""

from __future__ import annotations

import ctypes
import logging
import queue
import threading
import time
from ctypes import wintypes
from http.server import ThreadingHTTPServer

from headroom_dashboard import create_handler
from headroom_desktop import (TEXT, clamp_position, presentation, read_preferences,
                              read_usage, save_preferences, work_area)
from headroom_tray import TrayIcon

WIDTH, HEIGHT = 320, 352


def hide_taskbar_button(window):
    """Mark our own form as a tool window without recreating its WebView handle."""
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user.GetWindowLongW.restype = wintypes.LONG
    user.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    user.SetWindowLongW.restype = wintypes.LONG
    handle = int(window.native.Handle.ToInt64())
    style = user.GetWindowLongW(handle, -20)
    user.SetWindowLongW(handle, -20, (style | 0x80) & ~0x40000)


def popup_position(window):
    """Convert physical taskbar coordinates to pywebview's logical coordinates."""
    user = ctypes.WinDLL("user32", use_last_error=True)
    point = wintypes.POINT()
    user.GetCursorPos(ctypes.byref(point))
    scale = 1.0
    try:
        user.GetDpiForWindow.argtypes = [wintypes.HWND]
        user.GetDpiForWindow.restype = wintypes.UINT
        scale = (user.GetDpiForWindow(int(window.native.Handle.ToInt64())) or 96) / 96
    except (AttributeError, TypeError):
        pass
    area = work_area(None, point.x, point.y)
    x, y = clamp_position(point.x-WIDTH*scale, point.y-HEIGHT*scale-12,
                          WIDTH*scale, HEIGHT*scale, area)
    return round(x/scale), round(y/scale)


class Bridge:
    """Only harmless display preferences/actions are exposed to the local page."""
    def __init__(self, controller):
        self._controller = controller

    def settings(self):
        return {"muted": self._controller.muted}

    def collapse(self):
        self._controller.actions.put("collapse")

    def language(self, lang):
        if lang not in ("zh", "en"):
            raise ValueError("Unsupported language")
        self._controller.actions.put(lang)

    def sound(self, muted):
        if type(muted) is not bool:
            raise ValueError("Mute must be boolean")
        self._controller.actions.put(("muted", muted))


class WebCard:
    def __init__(self, args):
        self.args = args
        saved = read_preferences(args.settings_path)
        self.lang = args.lang or saved.get("lang", "zh")
        self.muted = saved.get("muted", False)
        self.x, self.y = saved.get("x", 0), saved.get("y", 0)
        self.actions = queue.Queue()
        self.closed = threading.Event()
        self.expanded = False
        self.data = None
        self.window = self.tray = self.server = None
        self.server_thread = None
        self.destroy_requested = False
        self.cleaned = False
        self.url = ""
        self.bridge = Bridge(self)

    def save(self):
        save_preferences(self.args.settings_path, self.x, self.y, self.lang, self.muted)

    def refresh(self):
        try:
            self.data = read_usage(self.args.codex_home, self.args.state_path)
        except Exception:
            self.data = None
        self.update_tray()

    def update_tray(self):
        if self.tray:
            self.tray.update(TEXT[self.lang], presentation(self.data, self.lang), self.expanded)

    def evaluate(self, script):
        if self.window.events.loaded.is_set():
            try:
                self.window.evaluate_js(script)
            except Exception as exc:
                # A language reload/closing renderer can race with a tray click.
                logging.warning("headroom card script unavailable: %s", type(exc).__name__)

    def stop_media(self):
        self.evaluate("if(typeof stopClip==='function')stopClip()")

    def close_window(self):
        self.closed.set()
        if not self.destroy_requested and not self.window.events.closed.is_set():
            self.destroy_requested = True
            self.stop_media()
            self.window.destroy()

    def collapse(self):
        try:
            self.stop_media()
        finally:
            self.window.hide()
            self.expanded = False
            self.update_tray()

    def handle(self, action):
        if action == "toggle":
            if self.expanded:
                self.collapse()
            else:
                self.window.move(*popup_position(self.window))
                self.window.show()
                self.expanded = True
                self.refresh()
                self.evaluate("refresh()")
        elif action == "collapse":
            self.collapse()
        elif action == "refresh":
            self.refresh()
            self.evaluate("refresh()")
        elif action in ("zh", "en"):
            self.stop_media()
            self.lang = action
            self.save()
            self.window.load_url(self.url + "?lang=" + self.lang)
            self.update_tray()
        elif isinstance(action, tuple) and len(action) == 2 and action[0] == "muted":
            self.muted = action[1]
            self.save()
        elif action == "exit":
            self.close_window()

    def loop(self):
        if not self.window.events.shown.wait(15):
            self.close_window()
            return
        self.refresh()
        self.tray = TrayIcon(self.actions, TEXT[self.lang], presentation(self.data, self.lang))
        try:
            self.tray.start()
            if self.args.show:
                self.actions.put("toggle")
            refresh_at = time.monotonic() + 10
            while not self.closed.is_set():
                try:
                    self.handle(self.actions.get(timeout=.1))
                except queue.Empty:
                    pass
                if time.monotonic() >= refresh_at:
                    self.refresh()
                    refresh_at = time.monotonic() + 10
        finally:
            self.close_window()

    def setup(self, webview):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(
            self.args.codex_home, self.args.state_path, self.lang, desktop=True))
        self.server.daemon_threads = True
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True,
                                              name="headroom-card-http")
        self.server_thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/"
        self.window = webview.create_window(
            "headroom", self.url + "?lang=" + self.lang, js_api=self.bridge,
            width=WIDTH, height=HEIGHT, min_size=(WIDTH, HEIGHT), resizable=False,
            hidden=True, frameless=True, easy_drag=False, on_top=True, background_color="#ffffff")

        def before_show():
            # WinForms ShowInTaskbar=False recreates a handle while WebView2 is
            # initializing, aborting its controller. Change only extended styles.
            hide_taskbar_button(self.window)
        self.window.events.before_show += before_show
        self.window.events.closed += self.closed.set

    def cleanup(self):
        if self.cleaned:
            return
        self.cleaned = True
        self.closed.set()
        if self.tray:
            self.tray.close()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.server_thread:
            self.server_thread.join(timeout=2)
        self.save()


def run(args):
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError("Install requirements-desktop.txt for animated tray cards, or use --renderer tk") from exc
    controller = WebCard(args)
    try:
        controller.setup(webview)
        webview.start(controller.loop, gui="edgechromium", private_mode=True)
    finally:
        controller.cleanup()
    return 0
