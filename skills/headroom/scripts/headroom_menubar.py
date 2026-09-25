"""macOS menu bar display for headroom.

A native ``NSStatusItem`` built on PyObjC — no Tk window, no browser tab. The
status bar shows the percent left; clicking it drops a usage card with the
per-agent breakdown.

AppKit owns the main thread, so the reader runs on a worker thread and hands
results back to the main run loop through a timer.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import queue
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import headroom  # noqa: E402

try:
    import objc
    from AppKit import (NSApplication, NSApplicationActivationPolicyAccessory,
                        NSColor, NSFont, NSFontAttributeName,
                        NSForegroundColorAttributeName, NSImage, NSImageLeading,
                        NSMenu, NSMenuItem, NSObject, NSScreen, NSStatusBar,
                        NSVariableStatusItemLength)
    from Foundation import NSAttributedString, NSTimer
except ImportError as exc:  # keep the module importable so its pure logic is testable
    objc = None
    NSObject = object
    APPKIT_ERROR: ImportError | None = exc
else:
    APPKIT_ERROR = None

INSTALL_HINT = (
    "The macOS menu bar display needs PyObjC. Install it with:\n"
    "  python3 -m venv ~/.headroom/venv\n"
    "  ~/.headroom/venv/bin/pip install pyobjc-framework-Cocoa Pillow"
)

#: Seconds between background reads.
REFRESH_SECONDS = 10.0
#: Percent below which the status bar text turns red.
LOW_PERCENT = 30.0
#: Percent above which the ring reads as comfortable.
HIGH_PERCENT = 70.0
DASHBOARD_PORT = 8766
#: Status item width budget: text is ~47pt, a ring is ~22pt.
RING_POINTS = 18.0
RING_SCALE = 2
RING_COLORS = ((HIGH_PERCENT, (0x25, 0x63, 0xEB)), (LOW_PERCENT, (0xC8, 0x79, 0x18)))
RING_LOW = (0xDC, 0x4A, 0x59)
TITLE_MODES = ("icon", "text", "both")
TEXT = {
    "zh": {"heading": "脑力剩余", "left": "剩余", "spent": "已用", "points": "点",
           "estimated": "（按对话条数估算）", "scored": "（按评分）",
           "today": "今天", "messages": "条", "sources": "来源",
           "refresh": "刷新", "dashboard": "打开网页面板", "language": "语言",
           "english": "English", "chinese": "中文", "quit": "退出 headroom",
           "unavailable": "暂时无法读取本机用量", "none": "无"},
    "en": {"heading": "Headroom", "left": "left", "spent": "Used", "points": "points",
           "estimated": " (estimated from message count)", "scored": " (scored)",
           "today": "Today", "messages": "messages", "sources": "Sources",
           "refresh": "Refresh", "dashboard": "Open web dashboard", "language": "Language",
           "english": "English", "chinese": "中文", "quit": "Exit headroom",
           "unavailable": "Local usage is unavailable", "none": "none"},
}

#: The single controller, so AppKit menu callbacks can reach it without ivars.
_APP: dict[str, "MenuBarApp"] = {}


def status_title(view: dict) -> str:
    """Compact status-bar text; the menu carries the precise value."""
    percent = view.get("left_percent")
    if percent is None:
        return "--"
    if 0 < percent < 1:
        return "<1%"
    return f"{percent:.0f}%"


def ring_color(percent: float | None) -> tuple[int, int, int]:
    if percent is None:
        return (0x61, 0x70, 0x8C)
    for threshold, color in RING_COLORS:
        if percent >= threshold:
            return color
    return RING_LOW


def ring_png(percent: float | None, *, points: float = RING_POINTS,
             scale: int = RING_SCALE) -> bytes | None:
    """A progress ring sized for the menu bar. None when Pillow is unavailable.

    A ring costs about half the width of "49%", which matters because macOS
    silently hides status items once the menu bar runs out of room.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    pixels = max(8, int(points * scale))
    image = Image.new("RGBA", (pixels, pixels), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    width = max(2, pixels // 7)
    pad = width / 2 + 1
    box = (pad, pad, pixels - pad - 1, pixels - pad - 1)
    # A translucent track reads correctly on both light and dark menu bars.
    draw.ellipse(box, outline=(0x80, 0x80, 0x80, 0x66), width=width)
    if percent:
        draw.arc(box, -90, -90 + 3.6 * min(100.0, percent),
                 fill=ring_color(percent) + (255,), width=width)
    import io
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def geometry(status_item) -> str:
    """Where the status item actually landed, in screen coordinates.

    A laid-out item sits in the right auxiliary area with the menu bar's height.
    ``origin=(0,0) size=(w,0)`` means macOS never placed it at all — the usual
    outcome when the menu bar has no room left.
    """
    try:
        button = status_item.button()
        window = button.window()
        frame = window.frame() if window else None
        screen = NSScreen.mainScreen()
        aux = None
        if screen is not None and hasattr(screen, "auxiliaryTopRightArea"):
            aux = screen.auxiliaryTopRightArea()
        return (f"isVisible={status_item.isVisible()} button={button.frame()} "
                f"window={frame} auxRight={aux}")
    except Exception as exc:  # never let diagnostics break the display
        return f"geometry unavailable: {type(exc).__name__}"


def log_line(path: str | None, message: str) -> None:
    """Append a diagnostic line. A broken log must never break the display."""
    if not path:
        return
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with target.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except (OSError, ValueError):
        pass


def menu_lines(view: dict, lang: str) -> list[tuple[str, str]]:
    """(label, kind) rows for the usage card. ``kind`` is plain/heading/error."""
    labels = TEXT[lang]
    if view.get("left_percent") is None:
        return [(labels["unavailable"], "error")]
    origin = labels["estimated"] if view.get("spent_origin") == "counts" else labels["scored"]
    rows = [
        (f"{labels['heading']} {view['left_percent']:.2f}%", "heading"),
        (f"{labels['spent']} {view['spent_points']:.2f} / {view['cap_points']:g} "
         f"{labels['points']}{origin}", "plain"),
        (f"{labels['today']} {view.get('today_messages', 0)} {labels['messages']}", "plain"),
    ]
    per_agent = view.get("per_agent_today") or {}
    active = sorted(((name, count) for name, count in per_agent.items() if count),
                    key=lambda item: (-item[1], item[0]))
    if active:
        summary = " · ".join(f"{name} {count}" for name, count in active)
        rows.append((f"{labels['sources']} {summary}", "plain"))
    return rows


class MenuBarApp(NSObject):
    """Owns the status item. All mutation happens on the main thread."""

    def initWithArgs_(self, args):
        self = objc.super(MenuBarApp, self).init()
        if self is None:
            return None
        self.args = args
        self.lang = args.lang or "zh"
        self.log_path = getattr(args, "log", None)
        self.title_mode = getattr(args, "title_mode", "text") or "text"
        self.view: dict = {"left_percent": None}
        self.failed = False
        self.busy = False
        self.placement_attempts = 0
        self.results: queue.Queue = queue.Queue(maxsize=1)
        # Actions must exist before the menu is built: it targets them.
        self.actions = Actions.alloc().init()
        self.status_item = None
        self.create_status_item()
        self.paint()
        return self

    # ------------------------------------------------------------------ menu

    def build_menu(self) -> None:
        labels = TEXT[self.lang]
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        self.info_items = []
        for _ in range(5):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
            item.setEnabled_(False)
            menu.addItem_(item)
            self.info_items.append(item)
        menu.addItem_(NSMenuItem.separatorItem())
        self.add_action(menu, labels["refresh"], b"refresh:")
        self.add_action(menu, labels["dashboard"], b"openDashboard:")
        languages = NSMenu.alloc().init()
        languages.addItem_(self.action_item(labels["english"], b"setLangEn:"))
        languages.addItem_(self.action_item(labels["chinese"], b"setLangZh:"))
        language = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            labels["language"], None, "")
        menu.setSubmenu_forItem_(languages, language)
        menu.addItem_(language)
        menu.addItem_(NSMenuItem.separatorItem())
        self.add_action(menu, labels["quit"], b"quit:")
        self.status_item.setMenu_(menu)

    def action_item(self, title: str, selector: bytes) -> NSMenuItem:
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, selector, "")
        item.setTarget_(self.actions)
        return item

    def add_action(self, menu, title: str, selector: bytes) -> None:
        menu.addItem_(self.action_item(title, selector))

    def paint(self) -> None:
        button = self.status_item.button()
        text = status_title(self.view)
        percent = self.view.get("left_percent")
        # A ring is roughly half the width of "49%"; the menu bar hides
        # whatever does not fit, so the compact form is the default.
        png = ring_png(percent) if self.title_mode in ("icon", "both") else None
        if png:
            graphic = NSImage.alloc().initWithData_(png)
            graphic.setSize_((RING_POINTS, RING_POINTS))
            graphic.setTemplate_(False)
            button.setImage_(graphic)
            button.setImagePosition_(NSImageLeading)
        else:
            button.setImage_(None)
        show_text = self.title_mode in ("text", "both") or png is None
        if show_text:
            if percent is None:
                color = NSColor.secondaryLabelColor()
            elif percent < LOW_PERCENT:
                color = NSColor.systemRedColor()
            else:
                color = NSColor.labelColor()
            font = NSFont.monospacedDigitSystemFontOfSize_weight_(12.0, 0.0)
            button.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(
                text, {NSFontAttributeName: font, NSForegroundColorAttributeName: color}))
        else:
            button.setAttributedTitle_(
                NSAttributedString.alloc().initWithString_(""))
        button.setToolTip_(text)
        self.paint_menu()

    def paint_menu(self) -> None:
        rows = menu_lines(self.view, self.lang)
        for index, item in enumerate(self.info_items):
            if index < len(rows):
                label, _kind = rows[index]
                item.setTitle_(label)
                item.setHidden_(False)
            else:
                item.setTitle_("")
                item.setHidden_(True)

    def set_language(self, lang: str) -> None:
        self.lang = lang
        self.build_menu()
        self.paint()

    # ---------------------------------------------------------------- reading

    def refresh(self) -> None:
        if self.busy:
            return
        self.busy = True

        def read() -> None:
            try:
                adapters = headroom.select_adapters(
                    self.args.agents, self.args.agent_home, self.args.codex_home)
                data = headroom.usage(adapters, datetime.now(headroom.SHANGHAI).date(),
                                      self.args.state_path)
            except Exception as exc:
                data = {"left_percent": None, "spent_points": 0, "cap_points": 1}
                log_line(self.log_path, f"read failed: {type(exc).__name__}: {exc}")
            try:
                self.results.put_nowait(data)
            except queue.Full:
                pass

        threading.Thread(target=read, daemon=True, name="headroom-reader").start()

    def create_status_item(self) -> None:
        """Register the item with the status bar."""
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength)
        self.status_item.retain()
        # Remember where the user Command-drags the icon, so a crowded menu bar
        # does not reset it to the leftmost (first-to-be-hidden) slot.
        self.status_item.setAutosaveName_("dev.headroom.menubar")
        self.build_menu()

    def is_placed(self) -> bool:
        window = self.status_item.button().window()
        return window is not None and window.frame().size.height > 0

    def ensure_placed(self) -> None:
        """Report a missing slot, but never fight the status bar for one.

        Toggling ``isVisible`` or rebuilding the item looks like a sensible
        self-heal, and it is actively harmful: on macOS 26 the geometry probe
        reports a zero-height window for items that *are* on screen, so the
        retry fires forever and keeps the item from ever settling. Logging is
        the useful half; a crowded menu bar has to be fixed by freeing a slot.
        """
        if self.placement_attempts or self.is_placed():
            return
        self.placement_attempts = 1
        log_line(self.log_path, f"no menu bar slot yet: {geometry(self.status_item)}")

    def poll_(self, _timer) -> None:
        self.ensure_placed()
        try:
            self.view = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.failed = self.view.get("left_percent") is None
            self.busy = False
            self.paint()
            log_line(self.log_path, "status=%s left=%s spent=%s/%s origin=%s | %s" % (
                status_title(self.view), self.view.get("left_percent"),
                self.view.get("spent_points"), self.view.get("cap_points"),
                self.view.get("spent_origin"), geometry(self.status_item)))
        # This timer is the only recurring event, so it also drives the read.
        # Without this the displayed value would freeze at startup.
        if not self.busy:
            self.refresh()

    def open_dashboard(self) -> None:
        script = Path(__file__).resolve().with_name("headroom_dashboard.py")
        command = [sys.executable, str(script), "--port", str(DASHBOARD_PORT),
                   "--lang", self.lang, "--state-path", str(self.args.state_path)]
        if self.args.agents:
            command += ["--agents", self.args.agents]
        for name in self.args.agent_home or []:
            command += ["--agent-home", name]
        subprocess.Popen(command, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.Popen(["open", f"http://127.0.0.1:{DASHBOARD_PORT}/?lang={self.lang}"])


class Actions(NSObject):
    """Menu target. Thin shim: every action defers to the controller."""

    def refresh_(self, _sender):
        _APP["app"].refresh()

    def openDashboard_(self, _sender):
        _APP["app"].open_dashboard()

    def setLangEn_(self, _sender):
        _APP["app"].set_language("en")

    def setLangZh_(self, _sender):
        _APP["app"].set_language("zh")

    def quit_(self, _sender):
        NSApplication.sharedApplication().terminate_(None)


class InstanceLock:
    """One display per ledger. ``flock`` is released automatically on exit."""

    def __init__(self, state_path: Path):
        self.path = state_path.with_name("menubar.lock")
        self.handle = None

    def acquire(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.path, "w")
        except OSError:
            return False
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()  # Another display already holds it.
            return False
        self.handle = handle
        self.handle.write(str(os.getpid()))
        self.handle.flush()
        return True

    def release(self) -> None:
        if self.handle is not None:
            try:
                fcntl.flock(self.handle, fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    parser.add_argument("--codex-home", type=Path, default=home)
    parser.add_argument("--agent-home", action="append", metavar="NAME=PATH")
    parser.add_argument("--agents", default=os.environ.get("HEADROOM_AGENTS", "auto"))
    parser.add_argument("--state-path", type=Path)
    parser.add_argument("--lang", choices=TEXT, default=os.environ.get("HEADROOM_LANG") or "zh")
    parser.add_argument("--log", default=os.environ.get("HEADROOM_MENUBAR_LOG"),
                        help="append startup and refresh diagnostics to this file")
    parser.add_argument("--title-mode", choices=TITLE_MODES,
                        default=os.environ.get("HEADROOM_MENUBAR_TITLE", "text"),
                        help="text shows the percent (default), icon is a compact ring, "
                             "both combines them")
    args = parser.parse_args(argv)
    args.state_path = args.state_path or headroom.default_state_path()
    return args


def main(argv=None) -> int:
    if sys.platform != "darwin":
        raise SystemExit("The menu bar display is macOS only; use --mode orb elsewhere.")
    if APPKIT_ERROR is not None:
        raise SystemExit(INSTALL_HINT) from APPKIT_ERROR
    args = parse_args(argv)
    lock = InstanceLock(args.state_path)
    if not lock.acquire():
        log_line(args.log, "another display already holds the lock; exiting")
        return 0
    try:
        app = NSApplication.sharedApplication()
        # Accessory: a menu bar item with no Dock icon and no window.
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        # Finish launching before creating the status item. macOS 26 hosts every
        # status item through Control Center and only adopts items from an app
        # that has completed its launch sequence.
        app.finishLaunching()
        controller = MenuBarApp.alloc().initWithArgs_(args)
        _APP["app"] = controller
        controller.refresh()
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            REFRESH_SECONDS, controller, b"poll:", None, True)
        log_line(args.log, f"started pid={os.getpid()} state={args.state_path} "
                           f"{geometry(controller.status_item)}")
        app.run()
        log_line(args.log, "run loop exited")
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
