"""Tray WebView lifecycle tests. All history, ledgers and preferences are fixtures."""

import contextlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import headroom as budget
import headroom_dashboard as dashboard
import headroom_desktop as desktop
import headroom_webcard as card


def fixture(root):
    args = desktop.parse_args(["--codex-home", str(root), "--state-path", str(root / "ledger.sqlite3"), "--lang", "en"])
    today = datetime.now(budget.SHANGHAI).date()
    with contextlib.closing(sqlite3.connect(root / "thread_history_1.sqlite")) as conn:
        conn.execute("CREATE TABLE thread_items (item_type TEXT, created_at_ms INTEGER)")
        conn.executemany("INSERT INTO thread_items VALUES (?,?)", [("userMessage", budget.day_start_ms(today-timedelta(days=1)))]*10)
        conn.commit()
    with contextlib.closing(budget.open_state(args.state_path, create=True)) as conn:
        conn.execute("INSERT INTO debits (event_id, local_day, points, provider) "
                     "VALUES (?,?,?,?)", ("fixture", today.isoformat(), 2.5, "fixture"))
        conn.commit()
    desktop.save_preferences(args.settings_path, 10, 20, "en", True)
    return args


class WebCardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.args = fixture(Path(self.temp.name))
        self.before = self.args.state_path.read_bytes()
        self.app = card.WebCard(self.args)
        loaded, closed = threading.Event(), threading.Event()
        loaded.set()
        self.app.window = Mock(events=SimpleNamespace(loaded=loaded, closed=closed))
        self.app.tray = Mock()
        self.addCleanup(self.app.cleanup)

    def test_bridge_preferences_and_lifecycle_never_score(self):
        self.assertTrue(self.app.bridge.settings()["muted"])
        with self.assertRaises(ValueError):
            self.app.bridge.sound("false")
        with self.assertRaises(ValueError):
            self.app.bridge.language("invalid")
        with patch.object(budget, "score_event", side_effect=AssertionError("must not score")), \
                patch.object(card, "popup_position", return_value=(200, 300)):
            self.app.handle("toggle")
            self.assertTrue(self.app.expanded)
            self.assertEqual(self.app.data["left_percent"], 87.5)
            self.app.window.move.assert_called_once_with(200, 300)
            self.app.bridge.collapse()
            self.app.handle(self.app.actions.get_nowait())
            self.assertFalse(self.app.expanded)
            self.app.window.hide.assert_called_once()
            self.assertIn("stopClip", self.app.window.evaluate_js.call_args.args[0])
            self.app.bridge.sound(False)
            self.app.handle(self.app.actions.get_nowait())
            self.app.bridge.language("zh")
            self.app.handle(self.app.actions.get_nowait())
            self.assertEqual(self.app.lang, "zh")
            self.assertFalse(desktop.read_preferences(self.args.settings_path)["muted"])
            desktop.save_preferences(self.args.settings_path, 30, 40, "en")
            self.assertFalse(desktop.read_preferences(self.args.settings_path)["muted"])
            self.app.handle("exit")
            self.app.close_window()
            self.app.window.destroy.assert_called_once()
        self.assertEqual(self.args.state_path.read_bytes(), self.before)
        self.app.cleanup()
        self.app.cleanup()
        self.app.tray.close.assert_called_once()

    def test_renderer_reload_does_not_kill_controller(self):
        self.app.window.evaluate_js.side_effect = RuntimeError("renderer closing")
        with self.assertLogs(level="WARNING"):
            self.app.collapse()
        self.app.window.hide.assert_called_once()
        self.app.window.events.closed.set()
        self.app.close_window()
        self.app.window.destroy.assert_not_called()

    def test_desktop_html_and_loopback_server(self):
        for lang in ("en", "zh"):
            page = dashboard.render_page(lang, desktop=True)
            self.assertIn('id="collapse"', page)
            self.assertIn("window.pywebview.api.sound", page)
            if lang == "en":
                self.assertNotRegex(page, r"[\u3400-\u9fff]")
        self.assertNotIn("pywebview", dashboard.render_page("en"))
        class Event:
            def __iadd__(self, callback):
                return self
        api = Mock()
        api.create_window.return_value = Mock(events=SimpleNamespace(before_show=Event(), closed=Event()))
        self.app.setup(api)
        self.assertEqual(self.app.server.server_address[0], "127.0.0.1")
        with urlopen(self.app.url, timeout=5) as response:
            self.assertIn('id="collapse"', response.read().decode())
        with urlopen(self.app.url + "api/status", timeout=5) as response:
            self.assertEqual(json.load(response)["left_percent"], 87.5)
        self.assertEqual(self.args.state_path.read_bytes(), self.before)
        self.assertTrue(api.create_window.call_args.kwargs["hidden"])
        self.assertFalse(api.create_window.call_args.kwargs["easy_drag"])

    @unittest.skipUnless(sys.platform == "win32" and os.environ.get("HEADROOM_TEST_WEBVIEW") == "1",
                         "opt-in native WebView2 smoke uses a temporary ledger and muted media")
    def test_native_webview(self):
        result = subprocess.run([sys.executable, __file__, "--native-smoke"], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Native WebView2 passed", result.stdout)


def native_smoke():
    import webview
    with tempfile.TemporaryDirectory() as directory:
        args = fixture(Path(directory))
        before = args.state_path.read_bytes()
        app = card.WebCard(args)
        app.setup(webview)
        failures = []

        def wait_for(script):
            deadline = time.monotonic()+10
            while time.monotonic() < deadline:
                if app.window.evaluate_js(script):
                    return
                time.sleep(.05)
            raise AssertionError("Timed out: " + script)

        def exercise():
            worker = threading.Thread(target=app.loop, daemon=True)
            worker.start()
            try:
                assert app.window.events.loaded.wait(15), "WebView did not load"
                wait_for("document.getElementById('value').textContent==='87.50%' && typeof window.pywebview.api.settings==='function' && muted")
                app.actions.put("toggle")
                wait_for("document.getElementById('refresh').getBoundingClientRect().bottom <= innerHeight")
                app.window.evaluate_js("document.getElementById('mood').click()")
                wait_for("!video.paused && video.currentTime>0")
                app.actions.put("collapse")
                wait_for("video.paused && !video.getAttribute('src')")
                app.actions.put("en")
                wait_for("LANGUAGE==='en' && muted")
                app.window.evaluate_js("document.getElementById('sound-toggle').click()")
                deadline = time.monotonic()+5
                while app.muted and time.monotonic() < deadline:
                    time.sleep(.05)
                assert not app.muted
                app.actions.put("zh")
                wait_for("LANGUAGE==='zh' && !muted")
                assert args.state_path.read_bytes() == before
                assert app.tray.icon.visible
            except BaseException as exc:
                failures.append(exc)
            finally:
                app.actions.put("exit")
                worker.join(timeout=5)

        try:
            webview.start(exercise, gui="edgechromium", private_mode=True)
        finally:
            app.cleanup()
        if failures:
            raise failures[0]
        assert args.state_path.read_bytes() == before
        print("Native WebView2 passed: media, collapse/stop, language, mute, ledger unchanged")


if __name__ == "__main__":
    if "--native-smoke" in sys.argv:
        native_smoke()
    else:
        unittest.main()
