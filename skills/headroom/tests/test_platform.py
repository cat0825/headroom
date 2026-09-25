"""Hook launch and macOS display logic.

The installer has its own suite in test_installer.py. Every test here uses a
temporary HOME/CODEX_HOME and fake native objects, so the suite runs on
Windows, macOS, and Linux without touching real Codex config.
"""

import contextlib
import importlib.util
import io
import json
import os
import queue
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))
sys.path.insert(0, str(SKILL / "hooks"))
import headroom_desktop as desktop  # noqa: E402
import headroom_hook as hook  # noqa: E402
import headroom_tray as tray  # noqa: E402


class HookLaunchTests(unittest.TestCase):
    def test_display_processes_are_detached_per_platform(self):
        with patch.object(hook.subprocess, "Popen") as spawn:
            hook.spawn_detached(["python3", "display.py"], None)
        options = spawn.call_args.kwargs
        if sys.platform == "win32":
            self.assertIn("creationflags", options)
            self.assertNotIn("start_new_session", options)
        else:
            self.assertTrue(options["start_new_session"])
            self.assertNotIn("creationflags", options)
        self.assertIs(options["stdin"], subprocess.DEVNULL)

    def test_default_display_follows_the_platform(self):
        env = {"HEADROOM_DISABLE_DASHBOARD": "0", "HEADROOM_DISABLE_DESKTOP": "0"}
        with patch.dict(os.environ, env), patch.object(hook, "start_dashboard") as web, \
                patch.object(hook, "spawn_detached") as spawn:
            os.environ.pop("HEADROOM_DISPLAY", None)
            hook.start_display(None)
        if sys.platform in ("win32", "darwin"):
            # Both platforms have a working tray; macOS runs pystray's status
            # item on Tk's main-thread Aqua loop. A missing Tk or tray package
            # falls back to the dashboard from inside headroom_desktop.
            spawn.assert_called_once()
            web.assert_not_called()
        else:
            web.assert_called_once()
            spawn.assert_not_called()


class TrayIconTests(unittest.TestCase):
    """A custom icon has to survive both menu bar appearances."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        try:
            import PIL  # noqa: F401 — Pillow is an optional desktop extra
            import headroom_tray
        except ImportError:
            self.skipTest("Pillow is not installed")
        self.tray = headroom_tray

    def test_dark_pixels_lighten_and_the_accent_survives(self):
        from PIL import Image
        image = Image.new("RGBA", (2, 1))
        image.putpixel((0, 0), (16, 32, 64, 255))    # dark navy
        image.putpixel((1, 0), (32, 96, 240, 255))   # bright accent
        out = self.tray.lighten_for_dark(image)
        navy = out.getpixel((0, 0))
        accent = out.getpixel((1, 0))
        self.assertGreater(sum(navy[:3]), sum((16, 32, 64)))
        self.assertEqual(accent[:3], (32, 96, 240))
        self.assertEqual(out.getpixel((0, 0))[3], 255)

    def test_transparent_pixels_stay_transparent(self):
        from PIL import Image
        image = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        self.assertEqual(self.tray.lighten_for_dark(image).getpixel((0, 0)), (0, 0, 0, 0))

    def test_a_dark_sibling_wins_in_dark_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            light = Path(temp) / "menubar-icon.png"
            dark = Path(temp) / "menubar-icon-dark.png"
            light.write_bytes(b"x")
            env = {"HEADROOM_ICON": str(light)}
            with patch.dict(os.environ, env, clear=False):
                self.assertEqual(self.tray.custom_icon_path(False), light)
                # No sibling yet: the light file is reused.
                self.assertEqual(self.tray.custom_icon_path(True), light)
                dark.write_bytes(b"x")
                self.assertEqual(self.tray.custom_icon_path(True), dark)

    def test_appearance_probe_never_raises(self):
        self.assertIn(self.tray.system_is_dark(), (True, False))


class TclLibraryTests(unittest.TestCase):
    """A detached venv process loses Tcl's search path; headroom sets it."""

    def test_missing_variables_are_derived_from_a_prefix(self):
        import headroom_desktop as desktop
        with tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp)
            for name in ("tcl9.0", "tk9.0"):
                (prefix / "lib" / name).mkdir(parents=True)
            env = {k: v for k, v in os.environ.items()
                   if k not in ("TCL_LIBRARY", "TK_LIBRARY")}
            with patch.dict(os.environ, env, clear=True),                     patch.object(desktop.sys, "base_prefix", str(prefix)),                     patch.object(desktop.sys, "prefix", str(prefix)),                     patch.object(desktop.sys, "platform", "darwin"):
                desktop.ensure_tcl_library()
                self.assertEqual(os.environ["TCL_LIBRARY"], str(prefix / "lib" / "tcl9.0"))
                self.assertEqual(os.environ["TK_LIBRARY"], str(prefix / "lib" / "tk9.0"))

    def test_existing_variables_are_left_alone(self):
        import headroom_desktop as desktop
        env = {"TCL_LIBRARY": "/mine/tcl", "TK_LIBRARY": "/mine/tk"}
        with patch.dict(os.environ, env, clear=False):
            desktop.ensure_tcl_library()
            self.assertEqual(os.environ["TCL_LIBRARY"], "/mine/tcl")
            self.assertEqual(os.environ["TK_LIBRARY"], "/mine/tk")

    def test_non_darwin_is_untouched(self):
        import headroom_desktop as desktop
        env = {k: v for k, v in os.environ.items()
               if k not in ("TCL_LIBRARY", "TK_LIBRARY")}
        with patch.dict(os.environ, env, clear=True),                 patch.object(desktop.sys, "platform", "linux"):
            desktop.ensure_tcl_library()
            self.assertNotIn("TCL_LIBRARY", os.environ)


class CliTests(unittest.TestCase):
    def test_status_cli_honors_codex_home_like_the_hook_and_dashboard(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            with contextlib.closing(sqlite3.connect(home / "thread_history_1.sqlite")) as conn:
                conn.execute("CREATE TABLE thread_items (item_type TEXT, created_at_ms INTEGER)")
                conn.commit()
            env = {**os.environ, "CODEX_HOME": str(home), "HOME": str(home / "elsewhere")}
            env.pop("HEADROOM_STATE_PATH", None)
            result = subprocess.run([sys.executable, str(SKILL / "scripts" / "headroom.py"), "status"],
                                    capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["left_percent"], 100)
            self.assertFalse((home / "headroom" / "ledger.sqlite3").exists())


class DesktopPlatformTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_tray_card_opens_above_bottom_taskbar_and_below_top_menu_bar(self):
        area = (0, 25, 1440, 900)
        self.assertEqual(desktop.tray_card_top(880, area), 880 - desktop.CARD_H - 12)
        self.assertEqual(desktop.tray_card_top(12, area), 24)
        x, y = desktop.clamp_position(1200 - desktop.CARD_W, desktop.tray_card_top(12, area),
                                      desktop.CARD_W, desktop.CARD_H, area)
        self.assertGreaterEqual(y, area[1])
        self.assertLessEqual(y + desktop.CARD_H, area[3])

    @unittest.skipIf(sys.platform == "win32", "POSIX flock lock")
    def test_posix_instance_lock_is_per_ledger_and_released(self):
        first = desktop.InstanceLock(self.root / "a.sqlite3")
        second = desktop.InstanceLock(self.root / "a.sqlite3")
        other = desktop.InstanceLock(self.root / "b.sqlite3")
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            self.assertTrue(other.acquire())
            first.close()
            self.assertTrue(second.acquire())
        finally:
            for lock in (first, second, other):
                lock.close()

    def test_fallback_starts_web_dashboard_or_reuses_running_one(self):
        args = SimpleNamespace(codex_home=self.root, state_path=self.root / "ledger.sqlite3",
                               settings_path=self.root / "desktop.json", lang="en", show=False)
        stderr = io.StringIO()
        with patch.object(desktop.os, "execv") as execv, patch("socket.create_connection",
                                                                side_effect=OSError), \
                contextlib.redirect_stderr(stderr):
            desktop.fall_back_to_web(args, "no tray here")
        command = execv.call_args.args[1]
        self.assertTrue(command[1].endswith("headroom_dashboard.py"))
        self.assertEqual(command[command.index("--state-path") + 1], str(args.state_path))
        self.assertEqual(command[command.index("--lang") + 1], "en")
        self.assertIn("no tray here", stderr.getvalue())
        self.assertIn("http://127.0.0.1:8766/?lang=en", stderr.getvalue())
        with patch.object(desktop.os, "execv") as execv, patch("socket.create_connection"), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(desktop.fall_back_to_web(args, "no tray here"), 0)
        execv.assert_not_called()

    def test_windows_only_webview_renderer_is_not_the_default_elsewhere(self):
        with patch.dict(os.environ, {"CODEX_HOME": str(self.root)}):
            args = desktop.parse_args([])
        self.assertEqual(args.renderer, "webview" if sys.platform == "win32" else "tk")


class FakeDetachedIcon:
    def __init__(self, name, icon, title, menu):
        self.name, self.icon, self.title, self.menu = name, icon, title, menu
        self.visible = False
        self.detached = 0

    def run(self, setup=None):
        raise AssertionError("macOS must not start a separate AppKit loop")

    def run_detached(self, setup=None):
        self.detached += 1

    def stop(self):
        raise AssertionError("macOS must not stop Tk's NSApplication")

    def update_menu(self):
        pass


def tray_dependencies_importable():
    if not (importlib.util.find_spec("pystray") and importlib.util.find_spec("PIL")):
        return False
    try:
        import pystray  # noqa: F401  (backend selection needs a desktop session)
    except Exception:
        return False
    return True


@unittest.skipUnless(tray_dependencies_importable(), "optional tray dependencies")
class MacMenuBarTests(unittest.TestCase):
    def test_macos_status_item_runs_detached_on_the_calling_thread(self):
        view = desktop.presentation({"left_percent": 80, "spent_points": 2, "cap_points": 10}, "en")
        events = queue.Queue()
        with patch("pystray.Icon", FakeDetachedIcon), patch.object(tray.sys, "platform", "darwin"), \
                patch.object(tray, "hide_dock_icon") as hide_dock:
            icon = tray.TrayIcon(events, desktop.TEXT["en"], view)
            icon.start()
            self.assertIsNone(icon.thread)
            self.assertEqual(icon.icon.detached, 1)
            self.assertTrue(icon.icon.visible)
            hide_dock.assert_called_once()
            list(icon.icon.menu)[0](icon.icon)
            self.assertEqual(events.get_nowait(), "toggle")
            icon.close()
            self.assertFalse(icon.icon.visible)


if __name__ == "__main__":
    unittest.main(verbosity=2)
