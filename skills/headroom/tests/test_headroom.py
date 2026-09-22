"""Mechanical self-checks for headroom."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import headroom as budget  # noqa: E402


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
        self.assertEqual(result["cap_points"], 835)

    def test_zero_history_gets_five_point_floor(self):
        result = budget.baseline(self.history, date(2027, 1, 1))
        self.assertEqual(result["cap_points"], 5)
        self.assertTrue(result["provisional_floor"])

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

    def test_headroom_hook_eligibility_and_charge(self):
        hooks_dir = str(Path(__file__).resolve().parents[1] / "hooks")
        if hooks_dir not in sys.path:
            sys.path.insert(0, hooks_dir)
        import headroom_hook

        # is_chargeable checks
        self.assertFalse(headroom_hook.is_chargeable({}))
        self.assertFalse(headroom_hook.is_chargeable({"prompt": "", "session_id": "s1", "turn_id": "t1"}))
        self.assertFalse(headroom_hook.is_chargeable({"prompt": "hi", "session_id": "", "turn_id": "t1"}))
        self.assertFalse(headroom_hook.is_chargeable({"prompt": "hi", "session_id": "s1", "turn_id": "t1", "permission_mode": "plan"}))
        self.assertFalse(headroom_hook.is_chargeable({"prompt": "hi", "session_id": "s1", "turn_id": "t1", "origin": "scheduled"}))
        self.assertTrue(headroom_hook.is_chargeable({"prompt": "hi", "session_id": "s1", "turn_id": "t1"}))

        # charge execution with direct module loading
        self.insert("2026-09-20", 12, count=10)
        with patch.object(headroom_hook, "codex_home", return_value=self.root), \
             patch.object(headroom_hook, "state_path", return_value=self.state):
            event = {"prompt": "测试提问", "session_id": "sess_1", "turn_id": "turn_1"}
            headroom_hook.charge(event)
            self.assertTrue(self.state.is_file())
            # check recorded score
            with closing(sqlite3.connect(self.state)) as conn:
                count = conn.execute("SELECT COUNT(*) FROM debits").fetchone()[0]
                self.assertEqual(count, 1)

    def test_install_hooks_non_destructive_merge_and_uninstall(self):
        hooks_dir = str(Path(__file__).resolve().parents[1] / "hooks")
        if hooks_dir not in sys.path:
            sys.path.insert(0, hooks_dir)
        import install_hooks

        # Create pre-existing hooks.json with other tools
        existing_hooks = {
            "hooks": {
                "PreToolUse": [{"type": "command", "command": "echo guard"}],
                "SessionStart": [{"type": "command", "command": "echo preexisting"}]
            }
        }
        hooks_file = self.root / "hooks.json"
        hooks_file.write_text(json.dumps(existing_hooks), encoding="utf-8")

        # First install
        code = install_hooks.install(self.root)
        self.assertEqual(code, 0)

        data = json.loads(hooks_file.read_text(encoding="utf-8"))
        # Pre-existing hooks preserved
        self.assertEqual(data["hooks"]["PreToolUse"][0]["command"], "echo guard")
        self.assertEqual(data["hooks"]["SessionStart"][0]["command"], "echo preexisting")
        # Headroom hooks appended
        self.assertTrue(any("headroom_hook.py" in json.dumps(item) for item in data["hooks"]["SessionStart"]))
        self.assertTrue(any("headroom_hook.py" in json.dumps(item) for item in data["hooks"]["UserPromptSubmit"]))

        # Idempotent re-install
        code_repeat = install_hooks.install(self.root)
        self.assertEqual(code_repeat, 0)
        data_repeat = json.loads(hooks_file.read_text(encoding="utf-8"))
        ss_headroom = [x for x in data_repeat["hooks"]["SessionStart"] if "headroom_hook.py" in json.dumps(x)]
        self.assertEqual(len(ss_headroom), 1)

        # Uninstall
        code_un = install_hooks.uninstall(self.root)
        self.assertEqual(code_un, 0)
        data_un = json.loads(hooks_file.read_text(encoding="utf-8"))
        self.assertTrue(all("headroom_hook.py" not in json.dumps(x) for x in data_un["hooks"]["SessionStart"]))
        self.assertNotIn("UserPromptSubmit", data_un["hooks"])
        self.assertEqual(data_un["hooks"]["SessionStart"][0]["command"], "echo preexisting")


if __name__ == "__main__":
    unittest.main(verbosity=2)
