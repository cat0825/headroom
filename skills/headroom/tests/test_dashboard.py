"""Language and read-only HTTP regression tests; uses only temporary data."""

import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import headroom_dashboard as dashboard


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "ledger.sqlite3"
        with contextlib.closing(sqlite3.connect(self.root / "thread_history_1.sqlite")) as conn:
            conn.execute("CREATE TABLE thread_items (item_type TEXT, created_at_ms INTEGER)")

    @contextlib.contextmanager
    def server(self, lang="zh", home=None):
        server = dashboard.ThreadingHTTPServer(
            ("127.0.0.1", 0), dashboard.create_handler(home or self.root, self.state, lang))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)

    def read(self, url):
        try:
            response = build_opener(ProxyHandler({})).open(url, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            return response.status, response.read()

    def test_full_english_render_and_chinese_default(self):
        english = dashboard.render_page("en")
        self.assertIn('<html lang="en-US">', english)
        self.assertIn('<button id="refresh">Refresh</button>', english)
        self.assertNotRegex(english, r"[\u3400-\u9fff]")
        self.assertNotIn("__TEXT__", english)
        self.assertIn("脑力剩余", dashboard.render_page("zh"))
        self.assertEqual(set(dashboard.TEXT["zh"]), set(dashboard.TEXT["en"]))

    def test_language_precedence_and_invalid_environment(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(dashboard.Path, "home", return_value=self.root):
            self.assertEqual(dashboard.parse_args([]).lang, "zh")
        with patch.dict(os.environ, {"HEADROOM_LANG": "en", "CODEX_HOME": str(self.root)}):
            self.assertEqual(dashboard.parse_args([]).lang, "en")
            self.assertEqual(dashboard.parse_args([]).codex_home, self.root)
            self.assertEqual(dashboard.parse_args(["--lang", "zh"]).lang, "zh")
        with patch.dict(os.environ, {"HEADROOM_LANG": "invalid"}), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                dashboard.parse_args([])
            self.assertEqual(dashboard.parse_args(["--lang", "en"]).lang, "en")
        with self.assertRaises(ValueError):
            dashboard.create_handler(self.root, self.state, "invalid")

    def test_query_override_fallback_assets_and_no_debits(self):
        with self.server("en") as url:
            for suffix in ("/", "/?lang=en", "/?lang=unknown", "/?lang=%3Cscript%3E"):
                status, body = self.read(url + suffix)
                self.assertEqual(status, 200)
                self.assertEqual(body.decode(), dashboard.render_page("en"))
            self.assertEqual(self.read(url + "/?lang=zh")[1].decode(), dashboard.render_page("zh"))
            for lang in ("en", "zh"):
                status, body = self.read(url + "/api/status?lang=" + lang)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)["spent_points"], 0)
                self.assertEqual(json.loads(body)["left_percent"], 100)
            status, body = self.read(url + "/assets/brain-full.png?lang=en")
            self.assertEqual(status, 200)
            self.assertEqual(body, (dashboard.ASSET_DIR / "brain-full.png").read_bytes())
            self.assertEqual(self.read(url + "/assets/../scripts/headroom.py")[0], 404)
        self.assertFalse(self.state.exists())

    def test_api_failure_is_localized_and_does_not_leak_paths(self):
        with self.server(home=self.root / "private-missing-history") as url:
            for lang in ("en", "zh"):
                status, body = self.read(url + "/api/status?lang=" + lang)
                self.assertEqual(status, 503)
                self.assertEqual(json.loads(body), {"error": dashboard.TEXT[lang]["status_error"]})
                self.assertNotIn("private-missing-history", body.decode())
                if lang == "en":
                    self.assertNotRegex(body.decode(), r"[\u3400-\u9fff]")
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
