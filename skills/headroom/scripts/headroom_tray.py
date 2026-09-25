"""Tray adapter (Windows notification area, macOS menu bar).

Native callbacks only enqueue actions for the card controller.
"""

from __future__ import annotations

import sys
import threading


def hide_dock_icon():
    """Keep a menu-bar-only app out of the macOS Dock; harmless if AppKit is absent."""
    try:
        from AppKit import NSApplication
        NSApplication.sharedApplication().setActivationPolicy_(1)  # Accessory
    except Exception:
        pass


def draw_icon(percent, color):
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (64, 64))
    draw = ImageDraw.Draw(image)
    draw.ellipse((2, 2, 61, 61), fill="#14213d")
    draw.ellipse((5, 5, 58, 58), outline="#61708c", width=5)
    if percent:
        draw.arc((5, 5, 58, 58), -90, -90 + 3.6 * percent, fill=color, width=5)
    # A legible H at taskbar sizes, without depending on installed fonts.
    draw.line((23, 21, 23, 43), fill="white", width=6)
    draw.line((41, 21, 41, 43), fill="white", width=6)
    draw.line((23, 32, 41, 32), fill="white", width=6)
    return image


class TrayIcon:
    def __init__(self, actions, labels, view):
        try:
            import pystray
            from PIL import Image  # noqa: F401 — check both dependencies up front
        except ImportError as exc:
            raise RuntimeError(
                "Tray mode requires pystray and Pillow. Install requirements-desktop.txt "
                "or launch with --mode orb."
            ) from exc
        self.actions = actions
        self.labels, self.expanded = labels, False
        self.ready, self.stopped = threading.Event(), threading.Event()
        self.error = None
        self.thread = None
        self.icon_key = (view["orb"], view["color"])
        item, menu = pystray.MenuItem, pystray.Menu
        self.icon = pystray.Icon(
            "headroom", draw_icon(view["percent"], view["color"]), self.title(view),
            menu(
                item(lambda _: self.labels["collapse"] if self.expanded else self.labels["open"],
                     self.action("toggle"), default=True),
                item(lambda _: self.labels["refresh"], self.action("refresh")),
                item(lambda _: self.labels["language"], menu(
                    item(lambda _: self.labels["english"], self.action("en")),
                    item(lambda _: self.labels["chinese"], self.action("zh")))),
                menu.SEPARATOR,
                item(lambda _: self.labels["exit"], self.action("exit")),
            ),
        )

    def title(self, view):
        value = f"{view['value']} left" if view["percent"] is not None else self.labels["desktop_error"]
        return "headroom · " + value

    def action(self, name):
        def enqueue(_icon, _item):
            self.actions.put(name)
        return enqueue

    def start(self):
        if sys.platform == "darwin":
            self.start_detached()
            return

        def setup(icon):
            try:
                if not self.stopped.is_set():
                    icon.visible = True
            except Exception as exc:
                self.error = exc
            finally:
                self.ready.set()

        def run():
            try:
                self.icon.run(setup=setup)
            except Exception as exc:
                self.error = exc
            finally:
                self.ready.set()

        self.thread = threading.Thread(target=run, daemon=True, name="headroom-tray")
        self.thread.start()
        if not self.ready.wait(3) or self.error or not self.icon.visible:
            self.close()
            raise RuntimeError("Could not start the Windows headroom tray icon") from self.error

    def start_detached(self):
        """AppKit status items must live on the main thread.

        Call this from the Tk thread after ``tk.Tk()`` exists: Tk's Aqua event
        loop already pumps the shared NSApplication, so no extra thread runs.
        """
        try:
            hide_dock_icon()
            self.icon.run_detached(setup=lambda _icon: None)
            if not self.stopped.is_set():
                self.icon.visible = True
        except Exception as exc:
            self.error = exc
        finally:
            self.ready.set()
        if self.error or not self.icon.visible:
            self.close()
            raise RuntimeError("Could not start the macOS headroom menu bar icon") from self.error

    def update(self, labels, view, expanded):
        menu_changed = labels != self.labels or expanded != self.expanded
        self.labels, self.expanded = labels, expanded
        self.icon.title = self.title(view)
        key = (view["orb"], view["color"])
        if key != self.icon_key:
            self.icon.icon = draw_icon(view["percent"], view["color"])
            self.icon_key = key
        if menu_changed:
            self.icon.update_menu()

    def close(self):
        self.stopped.set()
        if self.thread is None and sys.platform == "darwin":
            # NSApplication belongs to Tk here; only remove our status item.
            try:
                self.icon.visible = False
            except Exception:
                pass
            return
        self.icon.stop()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
