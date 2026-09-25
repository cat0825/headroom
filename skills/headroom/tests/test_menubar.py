"""Menu bar presentation checks.

The module's AppKit classes need PyObjC, but its formatting logic must not: these
tests import it with no GUI dependency and verify the strings a user actually
reads in the status bar and the dropdown.
"""

from __future__ import annotations

import contextlib
import io
import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import headroom_menubar as menubar  # noqa: E402


class StatusTitleTests(unittest.TestCase):
    def test_rounds_and_floors_at_the_extremes(self):
        self.assertEqual(menubar.status_title({"left_percent": 64.76}), "65%")
        self.assertEqual(menubar.status_title({"left_percent": 100.0}), "100%")
        self.assertEqual(menubar.status_title({"left_percent": 0.0}), "0%")
        # 0.4% must not read as 0%, which would look like the meter is broken.
        self.assertEqual(menubar.status_title({"left_percent": 0.4}), "<1%")
        self.assertEqual(menubar.status_title({"left_percent": 1.0}), "1%")

    def test_unreadable_history_shows_a_placeholder(self):
        self.assertEqual(menubar.status_title({"left_percent": None}), "--")
        self.assertEqual(menubar.status_title({}), "--")


class MenuLineTests(unittest.TestCase):
    def view(self, **overrides):
        base = {"left_percent": 64.76, "spent_points": 74.0, "cap_points": 210,
                "spent_origin": "counts", "today_messages": 37,
                "per_agent_today": {"codex": 4, "workbuddy": 33, "claude": 0}}
        return {**base, **overrides}

    def test_rows_cover_balance_spend_today_and_sources(self):
        rows = menubar.menu_lines(self.view(), "zh")
        self.assertEqual(rows[0], ("脑力剩余 64.76%", "heading"))
        self.assertIn("已用 74.00 / 210 点", rows[1][0])
        self.assertIn("按对话条数估算", rows[1][0])
        self.assertIn("今天 37 条", rows[2][0])
        # Silent agents are dropped; the rest are ordered by volume.
        self.assertEqual(rows[3][0], "来源 workbuddy 33 · codex 4")

    def test_scored_spend_is_labelled_differently_from_an_estimate(self):
        counted = menubar.menu_lines(self.view(), "en")[1][0]
        scored = menubar.menu_lines(self.view(spent_origin="ledger"), "en")[1][0]
        self.assertIn("estimated from message count", counted)
        self.assertIn("scored", scored)
        self.assertNotIn("estimated", scored)

    def test_no_active_agent_omits_the_sources_row(self):
        rows = menubar.menu_lines(self.view(per_agent_today={"codex": 0}), "zh")
        self.assertEqual(len(rows), 3)

    def test_unavailable_view_is_a_single_error_row(self):
        self.assertEqual(menubar.menu_lines({"left_percent": None}, "zh"),
                         [("暂时无法读取本机用量", "error")])
        self.assertEqual(menubar.menu_lines({"left_percent": None}, "en"),
                         [("Local usage is unavailable", "error")])

    def test_both_languages_define_the_same_keys(self):
        self.assertEqual(set(menubar.TEXT["zh"]), set(menubar.TEXT["en"]))
        for lang in menubar.TEXT:
            for row in menubar.menu_lines(self.view(), lang):
                self.assertTrue(row[0].strip())


class ParseArgsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_language_and_state_path_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            args = menubar.parse_args([])
        self.assertEqual(args.lang, "zh")
        self.assertEqual(args.agents, "auto")
        self.assertTrue(str(args.state_path).endswith("ledger.sqlite3"))

    def test_environment_and_flags_are_honoured(self):
        env = {"HEADROOM_LANG": "en", "HEADROOM_AGENTS": "codex,claude",
               "HEADROOM_STATE_PATH": str(self.root / "ledger.sqlite3")}
        with patch.dict(os.environ, env, clear=True):
            args = menubar.parse_args(["--agent-home", "codex=/tmp/codex"])
        self.assertEqual(args.lang, "en")
        self.assertEqual(args.agents, "codex,claude")
        self.assertEqual(args.state_path, self.root / "ledger.sqlite3")
        self.assertEqual(args.agent_home, ["codex=/tmp/codex"])


class LockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "ledger.sqlite3"

    def test_only_one_display_per_ledger(self):
        first, second = menubar.InstanceLock(self.state), menubar.InstanceLock(self.state)
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
        finally:
            first.release()
            second.release()

    def test_release_is_idempotent(self):
        lock = menubar.InstanceLock(self.state)
        lock.acquire()
        lock.release()
        lock.release()


class ImportSafetyTests(unittest.TestCase):
    def test_module_imports_without_pyobjc(self):
        # The formatting helpers must be usable on any platform, so the AppKit
        # import is optional rather than fatal.
        self.assertTrue(hasattr(menubar, "APPKIT_ERROR"))
        self.assertTrue(hasattr(menubar, "status_title"))
        if menubar.APPKIT_ERROR is not None:
            self.assertIn("PyObjC", menubar.INSTALL_HINT)


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.log = Path(self.temp.name) / "nested" / "menubar.log"

    def test_no_path_is_a_silent_no_op(self):
        menubar.log_line(None, "ignored")  # Must not raise.

    def test_lines_append_with_a_timestamp(self):
        menubar.log_line(str(self.log), "first")
        menubar.log_line(str(self.log), "second")
        lines = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].endswith(" first"))
        self.assertTrue(lines[1].endswith(" second"))

    def test_an_unwritable_path_never_breaks_the_display(self):
        menubar.log_line(str(Path(self.temp.name) / "missing" / "\0bad"), "x")


class RingTests(unittest.TestCase):
    """The ring is the compact form: the menu bar hides whatever does not fit."""

    def test_colour_follows_the_same_thresholds_as_the_rest_of_the_ui(self):
        self.assertEqual(menubar.ring_color(100), (0x25, 0x63, 0xEB))
        self.assertEqual(menubar.ring_color(70), (0x25, 0x63, 0xEB))
        self.assertEqual(menubar.ring_color(69.99), (0xC8, 0x79, 0x18))
        self.assertEqual(menubar.ring_color(30), (0xC8, 0x79, 0x18))
        self.assertEqual(menubar.ring_color(29.99), (0xDC, 0x4A, 0x59))
        self.assertEqual(menubar.ring_color(None), (0x61, 0x70, 0x8C))

    def test_ring_is_a_transparent_png_at_the_requested_size(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is an optional desktop extra")
        import io
        data = menubar.ring_png(50.0)
        self.assertIsNotNone(data)
        image = Image.open(io.BytesIO(data))
        self.assertEqual(image.format, "PNG")
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.size, (int(menubar.RING_POINTS * menubar.RING_SCALE),) * 2)
        # Corners stay clear so the ring reads as a circle, not a tile.
        self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_zero_and_unknown_percent_still_draw_a_track(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow is an optional desktop extra")
        import io
        for percent in (0, None):
            image = Image.open(io.BytesIO(menubar.ring_png(percent)))
            self.assertTrue(any(pixel[3] for pixel in image.getdata()))

    def test_a_ring_is_narrower_than_the_text_it_replaces(self):
        # 18pt of icon versus ~47pt for "49%": the whole point of icon mode.
        self.assertLess(menubar.RING_POINTS, 30)


class AppBundleTests(unittest.TestCase):
    """The .app wrapper is what makes macOS 26 render the status item.

    ``NSBundle.mainBundle()`` resolves from the main executable's path, so the
    interpreter has to live inside the bundle or the process ends up with no
    bundle identity and never gets a menu bar slot.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
        import install_macos_app
        self.installer = install_macos_app

    def test_launcher_execs_the_interpreter_beside_it(self):
        text = self.installer.launcher_script(Path("/l.log"), Path("/s.py"), "text")
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        self.assertIn('here=$(cd "$(dirname "$0")" && pwd)', text)
        self.assertIn('exec "$here/python" "/s.py"', text)
        self.assertIn('--log "/l.log"', text)
        self.assertIn("--title-mode text", text)

    def test_launcher_can_request_the_compact_icon_form(self):
        text = self.installer.launcher_script(Path("/l.log"), Path("/s.py"), "icon")
        self.assertIn("--title-mode icon", text)

    def test_real_interpreter_follows_the_venv_symlink(self):
        resolved = self.installer.real_interpreter(Path(sys.executable))
        self.assertTrue(resolved.is_absolute())
        self.assertNotIn("..", str(resolved))

    def test_version_dir_matches_the_interpreter_name(self):
        self.assertEqual(self.installer.version_dir(Path("/x/python3.13")), "python3.13")
        self.assertEqual(self.installer.version_dir(Path("/x/python3.12.1")), "python3.12")

    def test_bundle_puts_the_interpreter_inside_and_a_ui_element_plist(self):
        app = self.root / "headroom.app"
        built = self.installer.build(app, Path(sys.executable), Path("/s.py"),
                                     Path("/l.log"))
        contents = app / "Contents"
        # The interpreter must be *inside* the bundle, or it has no identity.
        self.assertTrue((contents / "MacOS" / "python").is_file())
        # pyvenv.cfg sits above MacOS/: codesign rejects non-code there.
        self.assertTrue((contents / "pyvenv.cfg").is_file())
        self.assertTrue((contents / "lib").is_dir())
        self.assertTrue(built["launcher"].is_file())
        self.assertTrue(os.access(built["launcher"], os.X_OK))
        plist = plistlib.loads((contents / "Info.plist").read_bytes())
        self.assertTrue(plist["LSUIElement"])
        self.assertEqual(plist["CFBundleIdentifier"], self.installer.BUNDLE_ID)
        self.assertEqual(plist["CFBundleExecutable"], "headroom")
        # Without this the ten-second refresh is throttled in the background.
        self.assertTrue(plist["NSAppSleepDisabled"])
        # site-packages is linked, never copied.
        packages = next((contents / "lib").glob("python*/site-packages"))
        self.assertTrue(packages.is_symlink())

    def test_build_rejects_an_interpreter_that_does_not_exist(self):
        with self.assertRaises(FileNotFoundError):
            self.installer.build(self.root / "x.app", self.root / "nope",
                                 Path("/s.py"), Path("/l.log"))

    def test_print_only_writes_nothing(self):
        app = self.root / "headroom.app"
        with contextlib.redirect_stdout(io.StringIO()) as output, \
                patch.object(self.installer.sys, "platform", "darwin"), \
                patch.object(self.installer, "MENUBAR_SCRIPT", Path(__file__)):
            code = self.installer.main(["--app", str(app), "--python", sys.executable,
                                        "--print-only"])
        self.assertEqual(code, 0)
        self.assertFalse(app.exists())
        self.assertIn("dev.headroom.menubar", output.getvalue())

    def test_non_macos_is_rejected(self):
        with patch.object(self.installer.sys, "platform", "win32"), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                self.installer.main([])

    def test_uninstall_removes_the_bundle(self):
        app = self.root / "headroom.app"
        self.installer.build(app, Path(sys.executable), Path("/s.py"), Path("/l.log"))
        with patch.object(self.installer.subprocess, "run"):
            self.installer.uninstall(app)
        self.assertFalse(app.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
