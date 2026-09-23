"""Local, read-only headroom Windows tray meter, with an optional desktop orb."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import queue
import sqlite3
import sys
import tempfile
import threading
import tkinter as tk
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import headroom
from headroom_dashboard import ASSET_DIR, TEXT as WEB_TEXT


BALL = 64
CARD_W, CARD_H = 320, 306
KEY = "#ff00ff"
INK, BLUE, MUTED = "#14213d", "#2563eb", "#61708c"
TEXT = {
    "zh": {**WEB_TEXT["zh"], "open": "展开用量", "collapse": "收起",
           "exit": "退出 headroom", "language": "语言", "english": "英文", "chinese": "中文",
           "desktop_error": "暂时无法读取本机用量", "switch": "EN"},
    "en": {**WEB_TEXT["en"], "open": "Show usage", "collapse": "Collapse",
           "exit": "Exit headroom", "language": "Language", "english": "English", "chinese": "Chinese",
           "desktop_error": "Local usage is unavailable", "switch": "ZH"},
}


def read_usage(codex_home: Path, state_path: Path) -> dict:
    """No HTTP server, scorer, writes, or creation of a missing ledger."""
    today = datetime.now(headroom.SHANGHAI).date()
    base = headroom.baseline(codex_home / "thread_history_1.sqlite", today)
    spent = 0.0
    if state_path.exists():
        conn = sqlite3.connect(state_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            spent = headroom.spent_points(conn, today)
        finally:
            conn.close()
    return headroom.view(base, today, spent)


def presentation(data: dict | None, lang: str) -> dict:
    labels = TEXT[lang]
    if data is not None:
        values = [data.get(key) for key in ("left_percent", "spent_points", "cap_points")]
        if (not all(type(v) in (int, float) and math.isfinite(v) for v in values)
                or not 0 <= values[0] <= 100 or values[1] < 0 or values[2] <= 0):
            data = None
    if data is None:
        return {"percent": None, "value": "--", "orb": "--", "color": MUTED,
                "meta": labels["desktop_error"], "image": None}
    percent, spent, cap = values
    mood = ("brain-full.png" if percent >= 70 else
            "brain-low.png" if percent < 30 else "brain-declining.webp")
    color = BLUE if percent >= 70 else "#c87918" if percent >= 30 else "#dc4a59"
    # Floor the compact number: 0.4% must not look like 1%, 99.9% like 100%.
    orb = "<1" if 0 < percent < 1 else str(math.floor(percent))
    return {"percent": percent, "value": f"{percent:.2f}%", "orb": orb,
            "color": color, "image": mood,
            "meta": f"{labels['spent']} {spent:.2f} / {cap:g} {labels['points']}"}


def read_preferences(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        clean = {key: data[key] for key in ("x", "y")
                 if type(data.get(key)) is int and abs(data[key]) <= 100000}
        if data.get("lang") in ("zh", "en"):
            clean["lang"] = data["lang"]
        return clean
    except (OSError, ValueError, UnicodeError):
        return {}


def save_preferences(path: Path, x: int, y: int, lang: str) -> None:
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".headroom-desktop-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"x": int(x), "y": int(y), "lang": lang}, handle)
        os.replace(temporary, path)
    except OSError:
        pass  # Display remains usable on read-only filesystems.
    finally:
        if temporary:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


def clamp_position(x, y, width, height, area):
    left, top, right, bottom = area
    return (max(left, min(int(x), right - width)),
            max(top, min(int(y), bottom - height)))


def card_position(x, y, area):
    top = y - CARD_H - 12
    if top < area[1]:
        top = y + BALL + 12
    return clamp_position(x + BALL - CARD_W, top, CARD_W, CARD_H, area)


def work_area(root, x=0, y=0):
    if sys.platform == "win32":
        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user.MonitorFromPoint.restype = wintypes.HANDLE
        user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
        monitor = user.MonitorFromPoint(wintypes.POINT(int(x), int(y)), 2)
        if user.GetMonitorInfoW(monitor, ctypes.byref(info)):
            r = info.rcWork
            return r.left, r.top, r.right, r.bottom
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def place_window(window, width, height, x, y):
    window.geometry(f"{width}x{height}")
    window.update_idletasks()
    if sys.platform == "win32":
        # Absolute desktop coordinates also work on monitors left of primary.
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user.GetAncestor.restype = wintypes.HWND
        user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wintypes.UINT]
        handle = user.GetAncestor(window.winfo_id(), 2)
        user.SetWindowPos(handle, None, int(x), int(y), width, height, 0x0010 | 0x0004)
    else:
        window.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")


class InstanceLock:
    """One display per ledger per Windows login session; no open network port."""
    def __init__(self, state_path: Path):
        self.handle = None
        digest = hashlib.sha256(os.path.normcase(str(state_path.resolve())).encode()).hexdigest()[:24]
        self.name = "Local\\HeadroomDesktop-" + digest

    def acquire(self):
        if sys.platform != "win32":
            raise RuntimeError("The desktop display currently supports Windows only")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        ctypes.set_last_error(0)
        self.handle = self.kernel.CreateMutexW(None, False, self.name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            self.close()
            return False
        return True

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def rounded(canvas, x1, y1, x2, y2, radius, **options):
    points = [x1+radius,y1,x2-radius,y1,x2,y1,x2,y1+radius,x2,y2-radius,
              x2,y2,x2-radius,y2,x1+radius,y2,x1,y2,x1,y2-radius,x1,y1+radius,x1,y1]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **options)


class DesktopOrb:
    def __init__(self, root, codex_home, state_path, settings_path, lang=None, *, visible=True, mode="orb"):
        if mode not in ("tray", "orb"):
            raise ValueError("Display mode must be tray or orb")
        self.root, self.codex_home, self.state_path = root, codex_home, state_path
        self.settings_path, self.visible = settings_path, visible
        self.mode, self.tray, self.tray_anchor = mode, None, None
        self.actions = queue.Queue()
        saved = read_preferences(settings_path)
        self.lang = lang or saved.get("lang", "zh")
        self.data, self.updated, self.failed = None, "", False
        self.expanded, self.busy, self.closed = False, False, False
        self.results = queue.Queue(maxsize=1)
        self.images = {}
        self.menu = None
        self.poll_id = self.refresh_id = None
        self.press = None
        self.dragged = False
        area = work_area(root, saved.get("x", 0), saved.get("y", 0))
        self.x, self.y = clamp_position(saved.get("x", area[2]-BALL-24),
                                       saved.get("y", area[3]-BALL-32), BALL, BALL, area)
        self.setup_window(root, "headroom")
        self.orb = tk.Canvas(root, width=BALL, height=BALL, bg=KEY, highlightthickness=0,
                             cursor="hand2", takefocus=True)
        self.orb.pack()
        self.orb.bind("<ButtonPress-1>", self.on_press)
        self.orb.bind("<B1-Motion>", self.on_drag)
        self.orb.bind("<ButtonRelease-1>", self.on_release)
        self.orb.bind("<Button-3>", self.show_menu)
        self.orb.bind("<Return>", lambda _event: self.toggle())
        self.orb.bind("<space>", lambda _event: self.toggle())
        root.bind("<Escape>", lambda _event: self.collapse())
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.panel = tk.Toplevel(root)
        self.setup_window(self.panel, "headroom usage")
        self.panel.bind("<Escape>", lambda _event: self.collapse())
        self.panel.protocol("WM_DELETE_WINDOW", self.collapse)
        self.card = tk.Canvas(self.panel, width=CARD_W, height=CARD_H,
                              bg=KEY, highlightthickness=0)
        self.card.pack()
        self.language_button = tk.Button(self.panel, command=self.switch_language, relief="flat",
                                        bg="white", fg=MUTED, bd=0, cursor="hand2", font=("Segoe UI", 10))
        self.language_button.place(x=231, y=17, width=34, height=28)
        self.collapse_button = tk.Button(self.panel, text="−", command=self.collapse, relief="flat",
                                       bg="white", fg=MUTED, bd=0, cursor="hand2", font=("Segoe UI", 17))
        self.collapse_button.place(x=271, y=15, width=28, height=30)
        self.refresh_button = tk.Button(self.panel, command=self.request_refresh, relief="flat",
                                      bg=INK, fg="white", activebackground="#263b61", activeforeground="white",
                                      bd=0, cursor="hand2", font=("Segoe UI", 11))
        self.refresh_button.place(x=32, y=251, width=256, height=32)
        self.paint()
        if visible and mode == "orb":
            root.deiconify()
        if mode == "orb":
            place_window(root, BALL, BALL, self.x, self.y)
        self.panel.withdraw()

    @staticmethod
    def setup_window(window, title):
        window.withdraw()
        window.title(title)
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.configure(bg=KEY)
        if sys.platform == "win32":
            window.attributes("-transparentcolor", KEY)
            window.attributes("-toolwindow", True)

    def start(self):
        if self.mode == "tray":
            from headroom_tray import TrayIcon
            self.tray = TrayIcon(self.actions, TEXT[self.lang], presentation(self.data, self.lang))
            self.tray.start()
        self.request_refresh()
        self.poll_id = self.root.after(100, self.poll)
        self.refresh_id = self.root.after(10000, self.periodic_refresh)

    def request_refresh(self):
        if self.busy or self.closed:
            return
        self.busy = True
        def read():
            try:
                data = read_usage(self.codex_home, self.state_path)
            except (OSError, sqlite3.Error, RuntimeError, ValueError, TypeError):
                data = None
            self.results.put((data, datetime.now().strftime("%H:%M:%S")))
        threading.Thread(target=read, daemon=True, name="headroom-reader").start()

    def periodic_refresh(self):
        if not self.closed:
            self.request_refresh()
            self.refresh_id = self.root.after(10000, self.periodic_refresh)

    def poll(self):
        if self.closed:
            return
        self.drain_actions()
        if self.closed:
            return
        try:
            self.data, self.updated = self.results.get_nowait()
            self.failed = self.data is None
            self.busy = False
            self.paint()
            self.reposition()
        except queue.Empty:
            pass
        self.poll_id = self.root.after(100, self.poll)

    def drain_actions(self):
        """Called on the Tk thread, never from a native tray callback."""
        while not self.closed:
            try:
                action = self.actions.get_nowait()
            except queue.Empty:
                return
            if action == "toggle":
                if not self.expanded:
                    self.tray_anchor = (self.root.winfo_pointerx(), self.root.winfo_pointery())
                self.toggle()
            elif action == "refresh":
                self.request_refresh()
            elif action in ("en", "zh"):
                self.switch_language(action)
            elif action == "exit":
                self.close()

    def update_tray(self):
        if self.tray is not None:
            self.tray.update(TEXT[self.lang], presentation(self.data, self.lang), self.expanded)

    def mood_image(self, filename):
        if filename not in self.images:
            try:
                from PIL import Image, ImageTk
                with Image.open(ASSET_DIR / filename) as source:
                    source = source.convert("RGBA")
                    source.thumbnail((54, 54), Image.Resampling.LANCZOS)
                    self.images[filename] = ImageTk.PhotoImage(source, master=self.root)
            except ImportError:
                try:
                    source = tk.PhotoImage(file=str(ASSET_DIR / filename), master=self.root)
                    factor = max(1, math.ceil(max(source.width(), source.height()) / 54))
                    self.images[filename] = source.subsample(factor)
                except tk.TclError:
                    self.images[filename] = None
            except (OSError, tk.TclError, ValueError):
                self.images[filename] = None
        return self.images[filename]

    def paint(self):
        labels, view = TEXT[self.lang], presentation(self.data, self.lang)
        self.orb.delete("all")
        self.orb.create_oval(2, 2, 62, 62, fill=INK, outline="#dae4f5", width=1)
        self.orb.create_oval(6, 6, 58, 58, outline="#34445f", width=3)
        if view["percent"]:
            if view["percent"] == 100:
                self.orb.create_oval(6, 6, 58, 58, outline="#58c7ab", width=3)
            else:
                self.orb.create_arc(6, 6, 58, 58, start=90, extent=-3.6*view["percent"],
                                    style="arc", outline="#58c7ab" if view["percent"] >= 70 else view["color"], width=3)
        self.orb.create_text(32, 27, text=view["orb"], fill="white", font=("Segoe UI", 16, "bold"))
        self.orb.create_text(32, 44, text="%", fill="#adc2e7", font=("Segoe UI", 9))
        self.card.delete("all")
        rounded(self.card, 1, 1, CARD_W-1, CARD_H-1, 18, fill="white", outline="#dce4ef")
        self.card.create_text(24, 31, anchor="w", text=labels["heading"], fill=INK, font=("Microsoft YaHei UI", 14, "bold"))
        self.card.create_text(24, 99, anchor="w", text=view["value"], fill=view["color"], font=("Segoe UI", 34, "bold"))
        self.card.create_text(26, 137, anchor="w", text="left", fill=MUTED, font=("Segoe UI", 10))
        if view["image"]:
            picture = self.mood_image(view["image"])
            if picture:
                self.card.create_image(268, 101, image=picture)
            else:
                self.card.create_text(267, 101, text=":)" if view["percent"] >= 70 else ":(" if view["percent"] >= 30 else ":O",
                                      fill=view["color"], font=("Segoe UI", 24, "bold"))
        self.card.create_line(28, 169, 292, 169, width=8, fill="#e8edf6", capstyle="round")
        if view["percent"]:
            self.card.create_line(28, 169, 28+264*view["percent"]/100, 169,
                                  width=8, fill=view["color"], capstyle="round")
        meta = labels["loading"] if self.data is None and not self.failed else view["meta"]
        self.card.create_text(24, 199, anchor="w", text=meta, fill=INK if not self.failed else "#b42318",
                              font=("Microsoft YaHei UI", 10), tags="usage")
        if self.updated and not self.failed:
            self.card.create_text(24, 224, anchor="w", text=labels["updated"]+self.updated,
                                  fill=MUTED, font=("Microsoft YaHei UI", 9))
        rounded(self.card, 24, 245, 296, 289, 10, fill=INK, outline="")
        self.language_button.configure(text=labels["switch"])
        self.refresh_button.configure(text=labels["refresh"])
        self.update_tray()

    def reposition(self):
        if self.mode == "tray":
            if self.expanded:
                ax, ay = self.tray_anchor or (self.x, self.y)
                area = work_area(self.root, ax, ay)
                x, y = clamp_position(ax-CARD_W, ay-CARD_H-12, CARD_W, CARD_H, area)
                place_window(self.panel, CARD_W, CARD_H, x, y)
            return
        area = work_area(self.root, self.x+BALL//2, self.y+BALL//2)
        self.x, self.y = clamp_position(self.x, self.y, BALL, BALL, area)
        place_window(self.root, BALL, BALL, self.x, self.y)
        if self.expanded:
            x, y = card_position(self.x, self.y, area)
            place_window(self.panel, CARD_W, CARD_H, x, y)

    def toggle(self):
        if self.expanded:
            self.collapse()
        else:
            self.expanded = True
            if self.visible:
                self.panel.deiconify()
            self.reposition()
            self.request_refresh()
            if self.visible:
                self.refresh_button.focus_set()
            self.update_tray()

    def collapse(self):
        self.expanded = False
        self.panel.withdraw()
        self.update_tray()

    def on_press(self, event):
        self.press = event.x_root, event.y_root, self.x, self.y
        self.dragged = False

    def on_drag(self, event):
        if self.press is None:
            return
        sx, sy, ox, oy = self.press
        dx, dy = event.x_root-sx, event.y_root-sy
        if abs(dx)+abs(dy) > 5:
            self.dragged = True
        if self.dragged:
            self.x, self.y = ox+dx, oy+dy
            self.reposition()

    def on_release(self, _event):
        if self.press is None:
            return
        self.press = None
        if self.dragged:
            self.save()
        else:
            self.toggle()

    def switch_language(self, lang=None):
        self.lang = lang or ("en" if self.lang == "zh" else "zh")
        self.paint()
        self.save()

    def show_menu(self, event):
        labels = TEXT[self.lang]
        if self.menu is not None:
            self.menu.destroy()
        menu = tk.Menu(self.root, tearoff=False)
        self.menu = menu
        menu.add_command(label=labels["collapse"] if self.expanded else labels["open"], command=self.toggle)
        menu.add_command(label=labels["refresh"], command=self.request_refresh)
        languages = tk.Menu(menu, tearoff=False)
        languages.add_command(label=labels["english"], command=lambda: self.switch_language("en"))
        languages.add_command(label=labels["chinese"], command=lambda: self.switch_language("zh"))
        menu.add_cascade(label=labels["language"], menu=languages)
        menu.add_separator()
        menu.add_command(label=labels["exit"], command=self.close)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def save(self):
        save_preferences(self.settings_path, self.x, self.y, self.lang)

    def close(self):
        if not self.closed:
            self.closed = True
            self.save()
            for timer in (self.poll_id, self.refresh_id):
                if timer:
                    self.root.after_cancel(timer)
            if self.tray is not None:
                self.tray.close()
            self.root.destroy()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    parser.add_argument("--codex-home", type=Path, default=home)
    parser.add_argument("--state-path", type=Path)
    parser.add_argument("--settings-path", type=Path)
    parser.add_argument("--lang", choices=TEXT, default=os.environ.get("HEADROOM_LANG"))
    parser.add_argument("--mode", choices=("tray", "orb"),
                        default=os.environ.get("HEADROOM_DESKTOP_MODE", "tray"))
    args = parser.parse_args(argv)
    if args.lang is not None and args.lang not in TEXT:
        parser.error("HEADROOM_LANG must be zh or en")
    if args.mode not in ("tray", "orb"):
        parser.error("HEADROOM_DESKTOP_MODE must be tray or orb")
    args.state_path = args.state_path or Path(os.environ.get("HEADROOM_STATE_PATH") or args.codex_home / "headroom" / "ledger.sqlite3")
    args.settings_path = args.settings_path or args.state_path.with_name("desktop.json")
    protected = {args.state_path.resolve(), (args.codex_home / "thread_history_1.sqlite").resolve(),
                 (args.codex_home / "headroom" / "config.json").resolve()}
    if args.settings_path.resolve() in protected:
        parser.error("--settings-path must not overwrite the ledger, history, or scoring config")
    return args


def main():
    args = parse_args()
    lock = InstanceLock(args.state_path)
    if not lock.acquire():
        return 0
    app = None
    root = None
    try:
        root = tk.Tk()
        app = DesktopOrb(root, args.codex_home, args.state_path, args.settings_path, args.lang, mode=args.mode)
        app.start()
        root.mainloop()
    finally:
        if app is not None:
            app.close()
        elif root is not None:
            root.destroy()
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
