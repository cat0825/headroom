"""Multi-agent adapter, ledger migration, and hook normalization checks.

Every fixture is a temporary directory. No test reads or writes a real agent's
history, config, or ledger.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))
import agent_adapters as adapters  # noqa: E402
import headroom as budget  # noqa: E402
import headroom_hook as hook  # noqa: E402

DAY = date(2026, 9, 20)
START, END = date(2026, 9, 14), date(2026, 9, 21)


def ms(day: date, hour: int = 12) -> int:
    return int((datetime.combine(day, datetime.min.time())
                .replace(tzinfo=budget.SHANGHAI) + timedelta(hours=hour)).timestamp() * 1000)


def write_lines(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
                    encoding="utf-8")


class AdapterFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        adapters.clear_cache()


class ClaudeAdapterTests(AdapterFixture):
    def build(self) -> adapters.ClaudeCodeAdapter:
        write_lines(self.root / "projects" / "-Users-me-app" / "session.jsonl", [
            # A real typed prompt: plain string body.
            {"type": "user", "isSidechain": False, "promptSource": "typed",
             "timestamp": "2026-09-20T02:00:00.000Z",
             "message": {"role": "user", "content": "重构这个模块"}},
            # A replayed tool result: user role, but not a human prompt.
            {"type": "user", "isSidechain": False, "timestamp": "2026-09-20T02:01:00.000Z",
             "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}},
            # A subagent sidechain.
            {"type": "user", "isSidechain": True, "timestamp": "2026-09-20T02:02:00.000Z",
             "message": {"role": "user", "content": "subagent prompt"}},
            # Programmatic prompt, not a person.
            {"type": "user", "isSidechain": False, "promptSource": "sdk",
             "timestamp": "2026-09-20T02:03:00.000Z",
             "message": {"role": "user", "content": "sdk prompt"}},
            # A real prompt delivered as content blocks.
            {"type": "user", "isSidechain": False, "timestamp": "2026-09-20T03:00:00.000Z",
             "message": {"role": "user", "content": [{"type": "text", "text": "第二个问题"}]}},
            # Assistant traffic never counts.
            {"type": "assistant", "timestamp": "2026-09-20T02:00:30.000Z",
             "message": {"role": "assistant", "content": "hi"}},
            # Outside the window.
            {"type": "user", "isSidechain": False, "timestamp": "2026-09-01T02:00:00.000Z",
             "message": {"role": "user", "content": "old"}},
        ])
        return adapters.ClaudeCodeAdapter(root=self.root)

    def test_only_human_prompts_inside_the_window_count(self):
        counts = self.build().daily_counts(START, END)
        self.assertEqual(counts["2026-09-20"], 2)
        self.assertEqual(sum(counts.values()), 2)

    def test_prompt_log_is_the_fallback_when_no_transcripts_exist(self):
        write_lines(self.root / "history.jsonl", [
            {"display": "hi", "timestamp": ms(DAY, 9), "project": "/tmp",
             "sessionId": "s1"},
            {"display": "again", "timestamp": ms(DAY, 10), "project": "/tmp",
             "sessionId": "s1"},
        ])
        adapter = adapters.ClaudeCodeAdapter(root=self.root)
        self.assertTrue(adapter.is_available())
        self.assertEqual(adapter.daily_counts(START, END)["2026-09-20"], 2)

    def test_a_quiet_window_is_zero_not_a_missing_source(self):
        # Transcripts exist but are all stale; that is a real zero, not an error.
        self.build()
        counts = adapters.ClaudeCodeAdapter(root=self.root).daily_counts(
            date(2027, 1, 1), date(2027, 1, 8))
        self.assertEqual(sum(counts.values()), 0)

    def test_no_source_at_all_is_reported(self):
        adapter = adapters.ClaudeCodeAdapter(root=self.root)
        self.assertFalse(adapter.is_available())
        with self.assertRaises(adapters.AgentSourceError):
            adapter.daily_counts(START, END)


class CodexAdapterTests(AdapterFixture):
    def test_schema_mismatch_raises_instead_of_reporting_zero(self):
        with closing(sqlite3.connect(self.root / "thread_history_1.sqlite")) as conn:
            conn.execute("CREATE TABLE thread_items (wrong TEXT)")
            conn.commit()
        with self.assertRaises(adapters.AgentSourceError):
            adapters.CodexAdapter(root=self.root).daily_counts(START, END)

    def test_missing_index_is_unavailable(self):
        adapter = adapters.CodexAdapter(root=self.root)
        self.assertFalse(adapter.is_available())
        with self.assertRaises(adapters.AgentSourceError):
            adapter.daily_counts(START, END)

    def test_explicit_database_path_overrides_the_root(self):
        database = self.root / "elsewhere" / "custom.sqlite"
        database.parent.mkdir()
        with closing(sqlite3.connect(database)) as conn:
            conn.execute("CREATE TABLE thread_items (item_type TEXT, created_at_ms INTEGER)")
            conn.executemany("INSERT INTO thread_items VALUES (?, ?)",
                             [("userMessage", ms(DAY))] * 3)
            conn.commit()
        adapter = adapters.CodexAdapter(root=self.root, database=database)
        self.assertTrue(adapter.is_available())
        self.assertEqual(adapter.daily_counts(START, END)["2026-09-20"], 3)


class OpenCodeAdapterTests(AdapterFixture):
    def build(self) -> adapters.OpenCodeAdapter:
        with closing(sqlite3.connect(self.root / "opencode.db")) as conn:
            conn.execute("""CREATE TABLE message (
                id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER,
                time_updated INTEGER, data TEXT)""")
            conn.executemany("INSERT INTO message VALUES (?,?,?,?,?)", [
                ("m1", "s1", ms(DAY, 9), ms(DAY, 9), json.dumps({"role": "user"})),
                ("m2", "s1", ms(DAY, 9), ms(DAY, 9), json.dumps({"role": "assistant"})),
                ("m3", "s1", ms(DAY, 10), ms(DAY, 10), json.dumps({"role": "user"})),
                ("m4", "s1", ms(date(2026, 9, 1), 10), ms(DAY, 10),
                 json.dumps({"role": "user"})),
            ])
            conn.commit()
        return adapters.OpenCodeAdapter(root=self.root)

    def test_role_is_read_from_the_json_blob(self):
        counts = self.build().daily_counts(START, END)
        self.assertEqual(counts["2026-09-20"], 2)
        self.assertEqual(sum(counts.values()), 2)


class AntigravityAdapterTests(AdapterFixture):
    def build(self) -> adapters.AntigravityAdapter:
        write_lines(
            self.root / "antigravity" / "brain" / "conv-1" / ".system_generated"
            / "logs" / "transcript_full.jsonl", [
                {"type": "USER_INPUT", "source": "USER_EXPLICIT",
                 "created_at": "2026-09-20T02:00:00Z"},
                {"type": "USER_INPUT", "source": "MODEL",
                 "created_at": "2026-09-20T02:01:00Z"},
                {"type": "PLANNER_RESPONSE", "created_at": "2026-09-20T02:02:00Z"},
                {"type": "USER_INPUT", "source": "USER_EXPLICIT",
                 "created_at": "2026-09-21T02:00:00Z"},
            ])
        return adapters.AntigravityAdapter(root=self.root)

    def test_only_explicit_user_inputs_count(self):
        counts = self.build().daily_counts(START, END)
        self.assertEqual(counts["2026-09-20"], 1)
        self.assertEqual(sum(counts.values()), 1)

    def test_gemini_cli_chat_json_is_read_best_effort(self):
        chat = self.root / "tmp" / "hash" / "chats" / "session-1.json"
        chat.parent.mkdir(parents=True)
        chat.write_text(json.dumps({"messages": [
            {"type": "user", "timestamp": "2026-09-20T02:00:00Z"},
            {"type": "gemini", "timestamp": "2026-09-20T02:01:00Z"},
            {"type": "user", "timestamp": "2026-09-20T05:00:00Z"},
        ]}), encoding="utf-8")
        adapter = adapters.AntigravityAdapter(root=self.root)
        self.assertTrue(adapter.is_available())
        self.assertEqual(adapter.daily_counts(START, END)["2026-09-20"], 2)

    def test_unknown_chat_shape_counts_zero_without_crashing(self):
        chat = self.root / "tmp" / "hash" / "chats" / "session-1.json"
        chat.parent.mkdir(parents=True)
        chat.write_text(json.dumps({"unexpected": True}), encoding="utf-8")
        counts = adapters.AntigravityAdapter(root=self.root).daily_counts(START, END)
        self.assertEqual(sum(counts.values()), 0)


class WorkBuddyAdapterTests(AdapterFixture):
    def build(self) -> adapters.WorkBuddyAdapter:
        projects = self.root / "projects" / "Users-me-workspace"
        write_lines(projects / "session-1.jsonl", [
            {"type": "message", "role": "user", "timestamp": ms(DAY, 9)},
            {"type": "message", "role": "assistant", "timestamp": ms(DAY, 9)},
            {"type": "function_call", "timestamp": ms(DAY, 9)},
            {"type": "message", "role": "user", "timestamp": ms(DAY, 11)},
        ])
        # The rollback sidecar sits beside a transcript and must never be parsed.
        write_lines(projects / "session-1.file-rollback.ndjson",
                    [{"type": "message", "role": "user", "timestamp": ms(DAY, 12)}])
        return adapters.WorkBuddyAdapter(root=self.root)

    def test_user_messages_only_and_rollback_sidecar_ignored(self):
        counts = self.build().daily_counts(START, END)
        self.assertEqual(counts["2026-09-20"], 2)
        self.assertEqual(sum(counts.values()), 2)


class CustomAdapterTests(AdapterFixture):
    def configure(self, declaration: dict) -> Path:
        config = self.root / "agents.json"
        config.write_text(json.dumps({"agents": [declaration]}), encoding="utf-8")
        return config

    def test_declared_jsonl_agent_is_discovered_and_counted(self):
        write_lines(self.root / "custom" / "log.jsonl", [
            {"role": "user", "timestamp": ms(DAY, 9)},
            {"role": "assistant", "timestamp": ms(DAY, 9)},
            {"role": "user", "timestamp": ms(DAY, 10)},
        ])
        config = self.configure({
            "name": "myagent", "label": "My Agent", "kind": "jsonl",
            "root": str(self.root / "custom"), "glob": "*.jsonl",
            "where": {"role": "user"}, "day_field": "timestamp", "day_format": "ms",
        })
        with patch.dict(os.environ, {"HEADROOM_AGENTS_CONFIG": str(config)}):
            adapter = adapters.resolve("myagent")
            self.assertTrue(adapter.is_available())
            self.assertEqual(adapter.daily_counts(START, END)["2026-09-20"], 2)

    def test_declared_sqlite_agent_reads_iso_days(self):
        with closing(sqlite3.connect(self.root / "custom.db")) as conn:
            conn.execute("CREATE TABLE prompts (created_at TEXT, n INTEGER)")
            conn.executemany("INSERT INTO prompts VALUES (?, ?)",
                             [("2026-09-20T10:00:00Z", 4), ("2026-09-01T10:00:00Z", 9)])
            conn.commit()
        config = self.configure({
            "name": "sqlagent", "kind": "sqlite", "root": str(self.root),
            "database": "custom.db", "day_format": "iso",
            "sql": "SELECT created_at, n FROM prompts",
        })
        with patch.dict(os.environ, {"HEADROOM_AGENTS_CONFIG": str(config)}):
            counts = adapters.resolve("sqlagent").daily_counts(START, END)
        self.assertEqual(counts["2026-09-20"], 4)
        self.assertEqual(sum(counts.values()), 4)

    def test_malformed_config_is_ignored_not_fatal(self):
        config = self.root / "agents.json"
        for contents in ("not json", "null", "[]", '{"agents": "nope"}',
                         '{"agents": [{"label": "no name"}]}'):
            config.write_text(contents, encoding="utf-8")
            with patch.dict(os.environ, {"HEADROOM_AGENTS_CONFIG": str(config)}):
                self.assertEqual(adapters.custom_adapters(), [])

    def test_declared_agent_wins_a_name_collision_with_a_builtin(self):
        config = self.configure({"name": "codex", "kind": "jsonl",
                                 "root": str(self.root), "glob": "*.jsonl"})
        with patch.dict(os.environ, {"HEADROOM_AGENTS_CONFIG": str(config)}):
            selected = [adapter for adapter in adapters.all_adapters()
                        if adapter.name == "codex"]
        self.assertEqual(len(selected), 1)
        self.assertIsInstance(selected[0], adapters.CustomAdapter)


class DiscoveryTests(AdapterFixture):
    def test_unknown_agent_is_a_clear_error(self):
        with patch.dict(os.environ, {"HEADROOM_AGENTS_CONFIG": str(self.root / "none.json")}):
            with self.assertRaises(adapters.UnknownAgentError):
                adapters.resolve("nope")

    def test_stale_files_are_filtered_by_mtime(self):
        fresh = self.root / "fresh.jsonl"
        stale = self.root / "stale.jsonl"
        write_lines(fresh, [{"a": 1}])
        write_lines(stale, [{"a": 1}])
        old = (datetime.now() - timedelta(days=30)).timestamp()
        os.utime(stale, (old, old))
        self.assertEqual(adapters.recent_files([fresh, stale], START), [fresh])

    def test_pooled_counts_sum_only_readable_agents(self):
        report = {
            "one": {"available": True, "counts": {"2026-09-20": 3}},
            "two": {"available": True, "counts": {"2026-09-20": 4}},
            "three": {"available": False, "counts": {"2026-09-20": 99}},
        }
        pooled = adapters.pooled_counts(report, START, END)
        self.assertEqual(pooled["2026-09-20"], 7)
        self.assertEqual(len(pooled), 7)

    def test_a_failing_agent_does_not_hide_the_others(self):
        class Broken(adapters.AgentAdapter):
            name, label, source_kind = "broken", "Broken", "jsonl"

            @classmethod
            def fallback_root(cls):
                return self.root

            def source_paths(self):
                return [self.root / "missing"]

            def daily_counts(self, start, end):
                raise adapters.AgentSourceError("nope")

        report = adapters.collect_counts(
            [Broken(root=self.root), adapters.CodexAdapter(root=self.root)],
            START, END, use_cache=False)
        self.assertFalse(report["broken"]["available"])
        self.assertIn("nope", report["broken"]["error"])
        self.assertIn("broken", json.dumps(report))


class BaselineTests(AdapterFixture):
    def test_pooled_baseline_uses_the_busiest_combined_day(self):
        class Fixed(adapters.AgentAdapter):
            source_kind = "jsonl"

            def __init__(self, name, counts, root):
                self.name, self.label, self._counts = name, name, counts
                super().__init__(root=root)

            @classmethod
            def fallback_root(cls):
                return Path.home()

            def source_paths(self):
                return [self.root]

            def is_available(self):
                return True

            def daily_counts(self, start, end):
                return dict(self._counts)

        left = Fixed("left", {"2026-09-20": 10}, self.root)
        right = Fixed("right", {"2026-09-20": 5, "2026-09-21": 2}, self.root)
        base = budget.baseline([left, right], date(2026, 9, 21))
        self.assertEqual(base["daily_message_counts"]["2026-09-20"], 15)
        self.assertEqual(base["peak_date"], "2026-09-20")
        self.assertEqual(base["peak_messages"], 15)
        self.assertEqual(base["cap_points"], 30)
        self.assertEqual(sorted(base["per_agent_counts"]), ["left", "right"])
        self.assertEqual({entry["name"] for entry in base["agents"]}, {"left", "right"})

    def test_baseline_strict_refuses_to_invent_a_cap(self):
        class Dead(adapters.AgentAdapter):
            name, label, source_kind = "dead", "Dead", "jsonl"

            @classmethod
            def fallback_root(cls):
                return Path.home()

            def source_paths(self):
                return [self.root / "missing"]

            def daily_counts(self, start, end):
                raise adapters.AgentSourceError("gone")

        with self.assertRaises(adapters.AgentSourceError):
            budget.baseline_strict([Dead(root=self.root)], date(2026, 9, 21))


class LedgerMigrationTests(AdapterFixture):
    def test_legacy_four_column_ledger_gains_the_agent_column(self):
        state = self.root / "ledger.sqlite3"
        with closing(sqlite3.connect(state)) as conn:
            conn.execute("""CREATE TABLE debits (
                event_id TEXT PRIMARY KEY, local_day TEXT NOT NULL,
                points REAL NOT NULL, provider TEXT NOT NULL)""")
            conn.execute("INSERT INTO debits VALUES ('old', '2026-09-20', 3.5, 'jev-mock-local')")
            conn.commit()
        with closing(budget.open_state(state, create=True)) as conn:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(debits)")]
            rows = conn.execute("SELECT event_id, agent FROM debits").fetchall()
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("agent", columns)
        self.assertEqual(rows, [("old", "codex")])
        self.assertIn("hook_turns", tables)

    def test_migration_is_idempotent(self):
        state = self.root / "ledger.sqlite3"
        for _ in range(3):
            with closing(budget.open_state(state, create=True)) as conn:
                pass
        with closing(sqlite3.connect(state)) as conn:
            columns = [row[1] for row in conn.execute("PRAGMA table_info(debits)")]
        self.assertEqual(columns.count("agent"), 1)

    def test_spend_is_attributed_per_agent(self):
        state = self.root / "ledger.sqlite3"
        base = {"peak_date": "2026-09-20", "peak_messages": 10, "cap_points": 20,
                "provisional_floor": False, "per_agent_counts": {}, "agents": []}
        for agent, score in (("codex", 2), ("claude", 3), ("claude", 1.5)):
            budget.score_event(base, DAY, state, f"{agent}-{score}", "manual-user",
                               "normal", "mock", "", score, False, agent)
        with closing(budget.open_state(state, create=False)) as conn:
            self.assertEqual(budget.spent_by_agent(conn, DAY), {"codex": 2.0, "claude": 4.5})
            self.assertEqual(budget.spent_points(conn, DAY), 6.5)
        result = budget.status(base, DAY, state)
        self.assertEqual(result["spent_by_agent"], {"codex": 2.0, "claude": 4.5})

    def test_session_sequence_makes_repeat_prompts_distinct(self):
        state = self.root / "ledger.sqlite3"
        first = budget.allocate_event_id(state, "claude", "sessionhash")
        second = budget.allocate_event_id(state, "claude", "sessionhash")
        other = budget.allocate_event_id(state, "codex", "sessionhash")
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, other)
        for event_id in (first, second, other):
            self.assertRegex(event_id, budget.OPAQUE_ID)

    def test_session_key_must_be_opaque(self):
        with self.assertRaises(ValueError):
            budget.allocate_event_id(self.root / "l.sqlite3", "claude", "bad key!")


class SpendSourceTests(AdapterFixture):
    """Today's spend resolves per agent, not once for the whole day.

    One switch for the day means a single Codex charge erases every other
    agent's day, which is both surprising and wrong.
    """

    def base(self, **today):
        per_agent = {"codex": 10, "workbuddy": 84}
        per_agent.update(today)
        counts = {day.isoformat(): 0 for day in
                  adapters.day_bounds(*budget.window(date(2026, 9, 25)))}
        return {"peak_date": "2026-09-23", "peak_messages": 105, "cap_points": 210,
                "provisional_floor": False, "per_agent_counts": {},
                "per_agent_today": per_agent,
                "today_messages": sum(per_agent.values()),
                "agents": [], "daily_message_counts": counts}

    def test_no_hook_counts_every_agent(self):
        resolved = budget.resolve_spent(self.base(), date(2026, 9, 25), self.root / "l.sqlite3")
        self.assertEqual(resolved["origin"], "counts")
        self.assertEqual(resolved["points"], 94 * budget.COUNT_POINTS)
        self.assertEqual(resolved["scored_agents"], [])

    def test_a_scored_agent_does_not_erase_the_counted_ones(self):
        state = self.root / "l.sqlite3"
        base = self.base()
        budget.score_event(base, date(2026, 9, 25), state, "c1", "manual-user",
                           "normal", "mock", "", 3.0, False, "codex")
        resolved = budget.resolve_spent(base, date(2026, 9, 25), state)
        self.assertEqual(resolved["origin"], "mixed")
        # 3.0 scored for codex + 84 messages x 2 for workbuddy, counted once each.
        self.assertEqual(resolved["points"], 3.0 + 84 * budget.COUNT_POINTS)
        self.assertEqual(resolved["scored_agents"], ["codex"])
        self.assertEqual(resolved["counted_agents"], ["workbuddy"])

    def test_scoring_every_agent_switches_wholly_to_the_ledger(self):
        state = self.root / "l.sqlite3"
        base = self.base()
        for agent, points in (("codex", 3.0), ("workbuddy", 5.0)):
            budget.score_event(base, date(2026, 9, 25), state, f"{agent}-1",
                               "manual-user", "normal", "mock", "", points, False, agent)
        resolved = budget.resolve_spent(base, date(2026, 9, 25), state)
        self.assertEqual(resolved["origin"], "ledger")
        self.assertEqual(resolved["points"], 8.0)
        self.assertEqual(resolved["counted_agents"], [])

    def test_explicit_modes_ignore_the_other_side(self):
        state = self.root / "l.sqlite3"
        base = self.base()
        budget.score_event(base, date(2026, 9, 25), state, "c1", "manual-user",
                           "normal", "mock", "", 3.0, False, "codex")
        with patch.dict(os.environ, {"HEADROOM_SPENT_SOURCE": "counts"}):
            self.assertEqual(budget.resolve_spent(base, date(2026, 9, 25), state)["points"],
                             94 * budget.COUNT_POINTS)
        with patch.dict(os.environ, {"HEADROOM_SPENT_SOURCE": "ledger"}):
            self.assertEqual(budget.resolve_spent(base, date(2026, 9, 25), state)["points"], 3.0)
        with patch.dict(os.environ, {"HEADROOM_SPENT_SOURCE": "nonsense"}), \
                self.assertRaises(ValueError):
            budget.resolve_spent(base, date(2026, 9, 25), state)

    def test_an_agent_with_no_messages_and_no_debits_adds_nothing(self):
        state = self.root / "l.sqlite3"
        base = self.base(claude=0)
        budget.score_event(base, date(2026, 9, 25), state, "c1", "manual-user",
                           "normal", "mock", "", 3.0, False, "codex")
        resolved = budget.resolve_spent(base, date(2026, 9, 25), state)
        self.assertNotIn("claude", resolved["counted_agents"])
        self.assertNotIn("claude", resolved["scored_agents"])
        self.assertEqual(resolved["points"], 3.0 + 84 * budget.COUNT_POINTS)


class HookNormalizationTests(AdapterFixture):
    def test_field_aliases_cover_other_agents(self):
        self.assertEqual(hook.first_string({"user_prompt": "hi"},
                                           hook.FIELD_ALIASES["prompt"]), "hi")
        self.assertEqual(hook.first_string({"promptId": "p1"},
                                           hook.FIELD_ALIASES["turn_id"]), "p1")
        self.assertEqual(hook.first_string({"sessionId": "s1"},
                                           hook.FIELD_ALIASES["session_id"]), "s1")
        self.assertIsNone(hook.first_string({"prompt": "   "},
                                            hook.FIELD_ALIASES["prompt"]))

    def test_agent_detection_prefers_the_explicit_variable(self):
        with patch.dict(os.environ, {"HEADROOM_AGENT": "workbuddy"}):
            self.assertEqual(hook.detect_agent({"turn_id": "t"}), "workbuddy")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(hook.detect_agent({"turn_id": "t"}), "codex")
            self.assertEqual(hook.detect_agent(
                {"transcript_path": "/Users/me/.claude/projects/x/y.jsonl"}), "claude")
            self.assertEqual(hook.detect_agent(
                {"transcript_path": "/Users/me/.gemini/antigravity/brain/x/t.jsonl"}),
                "antigravity")
            self.assertEqual(hook.detect_agent({}), "unknown")

    def test_turn_identity_uses_the_native_turn_id_when_present(self):
        arguments = hook.turn_identity("codex", {"session_id": "s", "turn_id": "t"})
        self.assertEqual(arguments[0], "--event-id")
        self.assertEqual(arguments, hook.turn_identity(
            "codex", {"session_id": "s", "turn_id": "t"}))
        self.assertNotEqual(arguments, hook.turn_identity(
            "codex", {"session_id": "s", "turn_id": "t2"}))

    def test_turn_identity_falls_back_to_an_opaque_session_key(self):
        arguments = hook.turn_identity("claude", {"session_id": "s"})
        self.assertEqual(arguments[0], "--session-key")
        self.assertNotIn("s", arguments[1])  # The raw session id never leaves the hook.

    def test_chargeability_matches_each_agents_payload_shape(self):
        self.assertTrue(hook.is_chargeable({"session_id": "s", "turn_id": "t", "prompt": "hi"}))
        self.assertTrue(hook.is_chargeable({"session_id": "s", "prompt": "hi"}))
        self.assertFalse(hook.is_chargeable({"session_id": "s", "turn_id": "", "prompt": "hi"}))
        self.assertFalse(hook.is_chargeable({"session_id": "s", "prompt": ""}))
        self.assertFalse(hook.is_chargeable({"prompt": "hi"}))
        self.assertFalse(hook.is_chargeable(
            {"session_id": "s", "prompt": "hi", "permissionMode": "plan"}))
        self.assertFalse(hook.is_chargeable(
            {"session_id": "s", "prompt": "hi", "mode": "goal"}))
        self.assertFalse(hook.is_chargeable(
            {"session_id": "s", "prompt": "hi", "origin": "subagent"}))

    def test_strict_mode_rejects_missing_provenance(self):
        event = {"session_id": "s", "prompt": "hi"}
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(hook.is_chargeable(event))
        with patch.dict(os.environ, {"HEADROOM_HOOK_STRICT": "1"}):
            self.assertFalse(hook.is_chargeable(event))


if __name__ == "__main__":
    unittest.main(verbosity=2)
