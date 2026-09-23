"""Tray logic without installing real notification icons during unit tests."""

import importlib.util
import queue
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import headroom_tray as tray
from headroom_desktop import TEXT, presentation


class FakeIcon:
    def __init__(self, name, icon, title, menu):
        self.name, self.icon, self.title, self.menu = name, icon, title, menu
        self.visible = False
        self.done = threading.Event()
        self.menu_updates = 0

    def run(self, setup):
        setup(self)
        self.done.wait(5)

    def stop(self):
        self.visible = False
        self.done.set()

    def update_menu(self):
        self.menu_updates += 1


@unittest.skipUnless(sys.platform == "win32" and importlib.util.find_spec("pystray")
                     and importlib.util.find_spec("PIL"), "optional Windows tray dependencies")
class TrayTests(unittest.TestCase):
    def view(self, percent):
        return presentation({"left_percent": percent, "spent_points": 2, "cap_points": 10}, "en")

    def test_icon_is_transparent_legible_and_changes_with_budget(self):
        full = tray.draw_icon(100, "#2563eb")
        empty = tray.draw_icon(0, "#dc4a59")
        self.assertEqual(full.size, (64, 64))
        self.assertEqual(full.getpixel((0, 0))[3], 0)
        self.assertEqual(full.getpixel((32, 32)), (255, 255, 255, 255))
        self.assertNotEqual(full.tobytes(), empty.tobytes())

    def test_callbacks_only_queue_and_update_localized_title_and_menu(self):
        events = queue.Queue()
        with patch("pystray.Icon", FakeIcon):
            icon = tray.TrayIcon(events, TEXT["en"], self.view(75))
        icon.start()
        try:
            self.assertTrue(icon.icon.visible)
            self.assertEqual(icon.icon.title, "headroom · 75.00% left")
            menu = list(icon.icon.menu)
            self.assertTrue(menu[0].default)
            menu[0](icon.icon)
            self.assertEqual(events.get_nowait(), "toggle")
            menu[-1](icon.icon)
            self.assertEqual(events.get_nowait(), "exit")
            before = icon.icon.icon
            icon.update(TEXT["en"], self.view(75.01), False)
            self.assertIs(icon.icon.icon, before)  # Small score changes update only the tooltip.
            self.assertEqual(icon.icon.title, "headroom · 75.01% left")
            icon.update(TEXT["zh"], self.view(50), True)
            self.assertEqual(menu[0].text, "收起")
            self.assertEqual(menu[-1].text, "退出 headroom")
            self.assertEqual(icon.icon.menu_updates, 1)
            icon.update(TEXT["en"], presentation(None, "en"), False)
            self.assertIn("unavailable", icon.icon.title)
        finally:
            icon.close()
        self.assertFalse(icon.thread.is_alive())
        icon.close()

    def test_start_failure_is_reported_and_cleaned_up(self):
        class FailingIcon(FakeIcon):
            def run(self, setup):
                raise OSError("fixture startup failure")
        with patch("pystray.Icon", FailingIcon):
            icon = tray.TrayIcon(queue.Queue(), TEXT["en"], self.view(100))
        with self.assertRaisesRegex(RuntimeError, "Could not start"):
            icon.start()
        self.assertFalse(icon.thread.is_alive())
        self.assertTrue(icon.stopped.is_set())

    def test_missing_dependency_is_explicit_and_does_not_open_an_orb(self):
        with patch.dict(sys.modules, {"pystray": None}):
            with self.assertRaisesRegex(RuntimeError, "requirements-desktop.txt"):
                tray.TrayIcon(queue.Queue(), TEXT["en"], self.view(100))


if __name__ == "__main__":
    unittest.main(verbosity=2)
