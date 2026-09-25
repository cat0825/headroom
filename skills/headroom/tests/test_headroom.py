"""Mechanical self-checks for headroom."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.request import ProxyHandler, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import headroom as budget  # noqa: E402
import headroom_dashboard as dashboard  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import headroom_hook as hook_module  # noqa: E402


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FakeOpener:
    def __init__(self, payload: dict):
        self.payload = payload
        self.request = None

    def open(self, request, timeout):
        self.request = request
        assert timeout == 15
        return FakeResponse(self.payload)


class HeadroomTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.history = self.root / "thread_history_1.sqlite"
        self.state = self.root / "ledger.sqlite3"
        with closing(sqlite3.connect(self.history)) as conn:
            with conn:
                conn.execute("CREATE TABLE thread_items (item_type TEXT, created_at_ms INTEGER)")

    def insert(self, local_date: str, hour: int = 12, item_type: str = "userMessage",
               count: int = 1):
        local = datetime.fromisoformat(local_date).replace(tzinfo=budget.SHANGHAI)
        stamp = int((local + timedelta(hours=hour)).timestamp() * 1000)
        with closing(sqlite3.connect(self.history)) as conn:
            with conn:
                conn.executemany("INSERT INTO thread_items VALUES (?, ?)",
                                 [(item_type, stamp)] * count)

    def test_prior_seven_complete_days_and_timezone(self):
        self.insert("2026-09-13", 23)  # Too old.
        self.insert("2026-09-14", 0)
        self.insert("2026-09-19", 12, count=143)
        self.insert("2026-09-20", 23, count=167)
        self.insert("2026-09-20", 23, "agentMessage", 200)
        self.insert("2026-09-21", 0, count=999)  # Today is excluded.
        result = budget.baseline(self.history, date(2026, 9, 21))
        self.assertEqual(result["daily_message_counts"]["2026-09-14"], 1)
        self.assertEqual(result["peak_date"], "2026-09-20")
        self.assertEqual(result["peak_messages"], 167)
        self.assertEqual(result["cap_points"], 334)

    def test_zero_history_gets_two_point_floor(self):
        result = budget.baseline(self.history, date(2027, 1, 1))
        self.assertEqual(result["cap_points"], 2)
        self.assertTrue(result["provisional_floor"])

    def test_cap_change_recalculates_percentage_without_rewriting_debits(self):
        day = date(2026, 9, 21)
        self.insert("2026-09-20", count=167)
        old_base = {"peak_date": "2026-09-20", "peak_messages": 167,
                    "cap_points": 835, "provisional_floor": False}
        budget.score_event(old_base, day, self.state, "existing-debit", "manual-user",
                           "normal", "mock", "", 7.25)
        before = self.state.read_bytes()
        with patch.object(budget, "mock_jev_score", side_effect=AssertionError("must not rescore")), \
                patch.object(budget, "laya_score", side_effect=AssertionError("must not rescore")):
            result = budget.status(budget.baseline(self.history, day), day, self.state)
        self.assertEqual(result["cap_points"], 334)
        self.assertEqual(result["spent_points"], 7.25)
        self.assertEqual(result["left_percent"], 97.83)
        self.assertEqual(self.state.read_bytes(), before)

    def test_goal_unknown_and_automation_skip_without_scorer_or_state(self):
        base = {"peak_date": "2026-09-20", "peak_messages": 167,
                "cap_points": 835, "provisional_floor": False}
        for origin, mode in [("manual-user", "goal"), ("unknown", "normal"),
                             ("scheduled", "normal"), ("subagent", "normal"),
                             ("automatic", "normal")]:
            with patch.object(budget, "mock_jev_score", side_effect=AssertionError("called")):
                result = budget.score_event(base, date(2026, 9, 21), self.state,
                                            "test-event", origin, mode, "mock", "", 7)
            self.assertEqual(result["action"], "skipped")
            self.assertEqual(result["charged_points"], 0)
        self.assertFalse(self.state.exists())

    def test_score_duplicate_failure_and_cross_day(self):
        base = {"peak_date": "2026-09-20", "peak_messages": 167,
                "cap_points": 835, "provisional_floor": False}
        day = date(2026, 9, 21)
        first = budget.score_event(base, day, self.state, "stable-id", "manual-user",
                                   "normal", "mock", "", 7.25)
        self.assertEqual(first["charged_points"], 7.25)
        self.assertEqual(first["spent_points"], 7.25)
        with patch.object(budget, "mock_jev_score", side_effect=AssertionError("called")):
            duplicate = budget.score_event(base, day, self.state, "stable-id", "manual-user",
                                           "normal", "mock", "", 10)
        self.assertEqual(duplicate["action"], "duplicate")
        self.assertEqual(duplicate["spent_points"], 7.25)
        failed = budget.score_event(base, day, self.state, "api-failure", "manual-user",
                                    "normal", "mock", "", fail=True)
        self.assertEqual(failed["action"], "skipped")
        self.assertEqual(failed["reason"], "scorer_failed")
        self.assertEqual(failed["left_percent"], first["left_percent"])
        self.assertEqual(budget.status(base, day, self.state)["spent_points"], 7.25)
        self.assertEqual(budget.status(base, day + timedelta(days=1), self.state)["spent_points"], 0)

    def test_score_boundaries_and_over_cap_does_not_block(self):
        for bad in [-1, 10.01, float("nan"), float("inf"), True, "5"]:
            with self.assertRaises(ValueError):
                budget.validate_points(bad)
        self.assertEqual(budget.validate_points(0), 0)
        self.assertEqual(budget.validate_points(10), 10)
        base = {"peak_date": "2026-09-20", "peak_messages": 1,
                "cap_points": 5, "provisional_floor": False}
        result = budget.score_event(base, date(2026, 9, 21), self.state, "over",
                                    "manual-user", "normal", "mock", "", 10)
        self.assertEqual(result["left_percent"], 0)
        self.assertEqual(result["spent_points"], 10)
        more = budget.score_event(base, date(2026, 9, 21), self.state, "over-again",
                                  "manual-user", "normal", "mock", "", 1)
        self.assertEqual(more["action"], "charged")
        self.assertEqual(more["spent_points"], 11)

    def test_laya_adapter_uses_loopback_score_contract(self):
        payload = {"answers": {"brain_load": {"type": "score", "score": 4.8308}}}
        opener = FakeOpener(payload)
        with patch.object(budget, "build_opener", return_value=opener) as factory:
            points, provider = budget.laya_score("设计一个架构方案")
        self.assertEqual(points, 4.83)
        self.assertEqual(provider, "laya-local-proxy")
        self.assertEqual(opener.request.full_url, budget.LAYA_URL)
        self.assertEqual(factory.call_args.args[0].proxies, {})
        self.assertEqual(len(budget.LAYA_LABELS), 11)
        self.assertNotIn("设计一个架构方案", str(self.state))

    def test_unavailable_laya_skips_and_keeps_balance(self):
        base = {"peak_date": "2026-09-20", "peak_messages": 167,
                "cap_points": 835, "provisional_floor": False}
        with patch.object(budget, "build_opener", side_effect=OSError("offline")):
            result = budget.score_event(base, date(2026, 9, 21), self.state,
                                        "laya-down", "manual-user", "normal",
                                        "laya", "hello")
        self.assertEqual(result["action"], "skipped")
        self.assertEqual(result["left_percent"], 100)
        self.assertEqual(result["spent_points"], 0)

    def test_hook_argv_shape_is_not_an_ambiguous_option_abbreviation(self):
        """The exact argv the hook shells out with must parse.

        The subcommands take ``--agent`` while the top-level parser takes
        ``--agent-home`` and ``--agents``. Up to Python 3.11, argparse resolves
        option abbreviations while pre-scanning *every* argument, so it read the
        subcommand's ``--agent`` as an ambiguous abbreviation of the two
        top-level options and exited 2 with "ambiguous option: --agent could
        match --agent-home, --agents" — before the subcommand ever saw it. The
        hook passes exactly this argv, so on 3.10/3.11 every turn silently
        failed to charge.
        """
        parser = budget.build_parser()
        argv = ["--state-path", str(self.state), "turn",
                "--event-id", "hook-" + "a" * 40, "--light",
                "--origin", "manual-user", "--mode", "normal",
                "--backend", "mock", "--agent", "claude"]
        args = parser.parse_args(argv)
        self.assertEqual(args.command, "turn")
        self.assertEqual(args.agent, "claude")
        self.assertTrue(args.light)
        # The two top-level options that made it ambiguous must still parse.
        self.assertEqual(parser.parse_args(["--agents", "codex", "status"]).agents, "codex")
        self.assertEqual(parser.parse_args(["--agent-home", "codex=/tmp", "status"]).agent_home,
                         ["codex=/tmp"])

    def invoke_hook(self, payload: bytes, via_powershell: bool = False):
        hook = Path(__file__).resolve().parents[1] / "hooks" / "headroom_hook.py"
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("HEADROOM_") and key != "PLUGIN_ROOT"}
        env.update(CODEX_HOME=str(self.root), HEADROOM_STATE_PATH=str(self.state),
                   HEADROOM_DEBUG_PATH=str(self.root / "diagnostic.json"),
                   HEADROOM_BACKEND="mock", HEADROOM_DISABLE_DASHBOARD="1", PYTHONUTF8="0",
                   PYTHONIOENCODING="gbk:surrogateescape")
        command = [sys.executable, str(hook), "--user-prompt"]
        if via_powershell:
            template = json.loads(hook.with_name("hooks.json.template").read_text())
            windows = template["hooks"]["UserPromptSubmit"][0]["hooks"][0]["commandWindows"]
            windows = windows.replace("__PYTHON__", sys.executable.replace("\\", "/"))
            windows = windows.replace("__PLUGIN_ROOT__", hook.parent.parent.as_posix())
            command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", windows]
        result = subprocess.run(command,
                                input=payload, capture_output=True, env=env, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"")
        return json.loads((self.root / "diagnostic.json").read_text(encoding="utf-8"))

    def test_hook_utf8_under_gbk_and_duplicate(self):
        message = "查询本周AI新模型发布 🧠"
        event = {"session_id": "private-test-session", "turn_id": "private-test-turn",
                 "prompt": message, "permission_mode": "default"}
        payload = json.dumps(event, ensure_ascii=False).encode("utf-8")
        self.assertEqual(self.invoke_hook(payload)["outcome"], "charged")
        self.assertEqual(self.invoke_hook(payload)["outcome"], "duplicate")
        with closing(sqlite3.connect(self.state)) as conn:
            rows = conn.execute("SELECT * FROM debits").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], 1)
        stored = (self.root / "diagnostic.json").read_text() + str(rows)
        for secret in (message, "private-test-session", "private-test-turn"):
            self.assertNotIn(secret, stored)

    def test_hook_bom_and_bad_input_fail_open(self):
        for payload in (b"not-json", b"\xff", b"null"):
            self.assertEqual(self.invoke_hook(payload)["outcome"], "invalid_input")
            self.assertFalse(self.state.exists())
        payload = json.dumps({"session_id": "s", "turn_id": "t", "prompt": "hello"}).encode()
        self.assertEqual(self.invoke_hook(b"\xef\xbb\xbf" + payload)["outcome"], "charged")

    def test_hook_ineligible_events_never_charge(self):
        base = {"session_id": "s", "turn_id": "t", "prompt": "hi"}
        for patch in ({"permission_mode": "plan"}, {"source": "scheduled"},
                      {"source": "subagent"}, {"origin": "automatic"},
                      {"turn_id": ""}, {"prompt": ""}):
            payload = json.dumps({**base, **patch}).encode()
            self.assertEqual(self.invoke_hook(payload)["outcome"], "ineligible")
        self.assertFalse(self.state.exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows PowerShell regression")
    def test_windows_hook_template_invokes_quoted_python(self):
        payload = json.dumps({"session_id": "s", "turn_id": "t",
                              "prompt": "查询本周AI新模型发布 🧠"}, ensure_ascii=False).encode("utf-8")
        self.assertEqual(self.invoke_hook(payload, via_powershell=True)["outcome"], "charged")

    def test_lifecycle_smoke_cannot_start_dashboard(self):
        with patch.dict(os.environ, {"HEADROOM_DISABLE_DASHBOARD": "1"}), \
                patch.object(hook_module, "dashboard_is_up") as probe, \
                patch.object(hook_module.subprocess, "Popen") as spawn:
            hook_module.start_dashboard(None)
        probe.assert_not_called()
        spawn.assert_not_called()

    def test_hook_backend_config_is_live_and_environment_wins(self):
        config = self.root / "headroom" / "config.json"
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(hook_module, "codex_home", return_value=self.root):
            self.assertEqual(hook_module.scoring_backend(), "mock")
            config.parent.mkdir()
            config.write_text('{"backend":"laya"}', encoding="utf-8-sig")
            self.assertEqual(hook_module.scoring_backend(), "laya")
            with patch.dict(os.environ, {"HEADROOM_BACKEND": "mock"}):
                self.assertEqual(hook_module.scoring_backend(), "mock")
            config.write_text('{"backend":"mock"}', encoding="utf-8")
            self.assertEqual(hook_module.scoring_backend(), "mock")

    def test_hook_invalid_backend_config_never_silently_uses_mock(self):
        config = self.root / "headroom" / "config.json"
        config.parent.mkdir()
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(hook_module, "codex_home", return_value=self.root):
            for contents in ('not-json', 'null', '[]', '{"backend":"remote"}',
                             '{"backend":null}', '{"backend":[]}'):
                config.write_text(contents, encoding="utf-8")
                with self.assertRaises(ValueError):
                    hook_module.scoring_backend()
            with patch.dict(os.environ, {"HEADROOM_BACKEND": "invalid"}):
                with self.assertRaises(ValueError):
                    hook_module.scoring_backend()

    def test_hook_invalid_backend_skips_worker_and_ledger(self):
        with patch.object(hook_module, "scoring_backend", side_effect=ValueError), \
                patch.object(hook_module, "debug_hook_event") as diagnostic, \
                patch.object(hook_module.subprocess, "run") as score:
            hook_module.charge({"session_id": "s", "turn_id": "t", "prompt": "hi"})
        score.assert_not_called()
        diagnostic.assert_called_with(
            {"session_id": "s", "turn_id": "t", "prompt": "hi"}, True, "invalid_backend_config")
        self.assertFalse(self.state.exists())

    def test_dashboard_launch_uses_same_home_and_ledger(self):
        env = {"HEADROOM_DISABLE_DASHBOARD": "0", "CODEX_HOME": str(self.root),
               "HEADROOM_STATE_PATH": str(self.state)}
        with patch.dict(os.environ, env), \
                patch.object(hook_module, "dashboard_is_up", return_value=False), \
                patch.object(hook_module.subprocess, "Popen") as spawn:
            hook_module.start_dashboard(None)
        args = spawn.call_args.args[0]
        self.assertEqual(args[args.index("--codex-home") + 1], str(self.root))
        self.assertEqual(args[args.index("--state-path") + 1], str(self.state))

    def test_dashboard_reads_live_ledger_without_charging(self):
        today = datetime.now(budget.SHANGHAI).date()
        self.insert((today - timedelta(days=1)).isoformat(), count=2)
        base = budget.baseline(self.history, today)
        server = dashboard.ThreadingHTTPServer(
            ("127.0.0.1", 0), dashboard.create_handler(self.root, self.state))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/api/status"
            opener = build_opener(ProxyHandler({}))
            def read_status():
                with opener.open(url, timeout=3) as response:
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                    return json.load(response)
            self.assertEqual(read_status()["spent_points"], 0)
            self.assertFalse(self.state.exists())  # A refresh never creates debits.
            budget.score_event(base, today, self.state, "dashboard-regression",
                               "manual-user", "normal", "mock", "", 2)
            for _ in range(2):
                self.assertEqual(read_status(), budget.status(base, today, self.state))
            with closing(sqlite3.connect(self.state)) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM debits").fetchone()[0], 1)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
