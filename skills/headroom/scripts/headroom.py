"""Entertainment-only headroom meter, with fake Jev or local Laya scoring.

Agent history is read through ``agent_adapters``: one adapter per local AI tool,
each reduced to a per-day count of direct user prompts. The ledger is shared, so
a single daily budget covers every agent you talk to.

History queries select only item type and timestamp metadata. Message text is
never written to the ledger or printed by this program.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_adapters import (AgentAdapter, AgentSourceError,  # noqa: E402
                            CodexAdapter, UnknownAgentError, catalog,
                            collect_counts, discover, pooled_counts, resolve)


SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
OPAQUE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
LAYA_URL = "http://127.0.0.1:8765/predict"
LAYA_LABELS = [
    "几乎无需思考", "轻松阅读", "需要一点理解", "需要集中注意", "中等复杂",
    "多步理解", "专业理解", "复杂推理", "高强度分析", "极高强度分析",
    "极端复杂且费脑",
]
#: Length of the rolling window used to derive the daily cap.
WINDOW_DAYS = 7
#: Points charged per message when no hook has scored the turn yet. Matches the
#: cap's x2 so a day as busy as your busiest recorded day reads 0% left.
COUNT_POINTS = 2
#: How today's spend is derived. "auto" prefers scored ledger debits and falls
#: back to counting the day's messages, so a zero-install meter still moves.
SPENT_SOURCES = ("auto", "ledger", "counts")
#: Agents assumed when a caller supplies only a Codex history location.
DEFAULT_AGENT = "codex"
LEDGER_VERSION = 2


def day_start_ms(day: date) -> int:
    return int(datetime.combine(day, time.min, SHANGHAI).timestamp() * 1000)


def window(as_of: date) -> tuple[date, date]:
    """The seven complete local days before ``as_of``."""
    return as_of - timedelta(days=WINDOW_DAYS), as_of


def default_state_path() -> Path:
    """One shared ledger for every agent.

    ``HEADROOM_STATE_PATH`` wins. Otherwise prefer ``~/.headroom``, but keep
    reading a pre-existing Codex-era ledger so nobody silently starts at zero.
    """
    configured = os.environ.get("HEADROOM_STATE_PATH")
    if configured:
        return Path(configured).expanduser()
    modern = Path.home() / ".headroom" / "ledger.sqlite3"
    legacy = (Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
              / "headroom" / "ledger.sqlite3")
    if not modern.exists() and legacy.exists():
        return legacy
    return modern


def summarize(per_agent_counts: dict[str, dict[str, int]], as_of: date,
              agents: list[dict] | None = None) -> dict:
    """Pool every agent's counts into one cap for the day.

    ``per_agent_counts`` covers the cap window *and* ``as_of`` itself, so one
    scan answers both "how big is today's budget" and "how much of it is gone".

    The cap is the busiest of the previous seven complete local days, doubled.
    Pooling is deliberate: the meter answers "how much have I spent on AI
    today", not "how much on one particular tool".
    """
    start, end = window(as_of)
    days = [(start + timedelta(days=index)).isoformat() for index in range(WINDOW_DAYS)]
    today = as_of.isoformat()
    per_agent: dict[str, dict[str, int]] = {}
    per_agent_today: dict[str, int] = {}
    pooled = {day: 0 for day in days}
    today_messages = 0
    for name, counts in per_agent_counts.items():
        row = {day: int(counts.get(day, 0)) for day in days}
        per_agent[name] = row
        for day in days:
            pooled[day] += row[day]
        per_agent_today[name] = int(counts.get(today, 0))
        today_messages += per_agent_today[name]
    peak_date = max(days, key=lambda day: (pooled[day], day))
    peak = pooled[peak_date]
    return {
        "peak_date": peak_date,
        "peak_messages": peak,
        "cap_points": max(1, peak) * 2,
        "provisional_floor": peak == 0,
        "daily_message_counts": pooled,
        "per_agent_counts": per_agent,
        "today_messages": today_messages,
        "per_agent_today": per_agent_today,
        "agents": agents or [],
    }


#: Fields of an adapter report that are safe to expose to a display.
AGENT_FIELDS = ("name", "label", "kind", "hooks", "root", "available", "error")


def agent_summary(entry: dict) -> dict:
    """Adapter metadata for the UI, without per-day counts or cache internals."""
    return {key: entry.get(key) for key in AGENT_FIELDS}


def baseline(source, as_of: date) -> dict:
    """Daily cap for ``as_of``, plus the day's own message count.

    ``source`` may be a Codex history index path (single-agent compatibility),
    an iterable of :class:`AgentAdapter`, or a ``collect_counts`` report.

    The scan covers one extra day so the same pass answers both "how big is
    today's budget" and "how much of it is already gone".
    """
    start, _end = window(as_of)
    scan_end = as_of + timedelta(days=1)
    if isinstance(source, (str, Path)):
        adapter = CodexAdapter(root=Path(source).parent, database=Path(source))
        counts = adapter.daily_counts(start, scan_end)
        return summarize({adapter.name: counts}, as_of, [agent_summary({
            "name": adapter.name, "label": adapter.label, "kind": adapter.source_kind,
            "hooks": adapter.supports_hooks, "root": str(adapter.root),
            "available": True, "error": None,
        })])
    if isinstance(source, dict):
        return summarize({name: entry.get("counts", {}) for name, entry in source.items()},
                         as_of, [agent_summary(entry) for entry in source.values()])
    adapters = list(source)
    report = collect_counts(adapters, start, scan_end)
    return summarize({name: entry["counts"] for name, entry in report.items()},
                     as_of, [agent_summary(entry) for entry in report.values()])


def baseline_strict(adapters, as_of: date) -> dict:
    """``baseline`` that refuses to invent a cap when nothing could be read.

    Displays use this: showing 100% left because every source failed would be a
    silent lie, so the caller is told to surface an error instead.
    """
    base = baseline(adapters, as_of)
    if not any(entry.get("available") for entry in base.get("agents") or []):
        raise AgentSourceError("No readable agent history found")
    return base


def usage(adapters, as_of: date, state_path: Path) -> dict:
    """One read-only view of today, for any display surface.

    Kept free of display imports so a menu-bar app does not have to pull in Tk.
    """
    base = baseline_strict(adapters, as_of)
    resolved = resolve_spent(base, as_of, state_path)
    result = view(base, as_of, resolved["points"], origin=resolved["origin"])
    result["today_messages"] = base.get("today_messages", 0)
    result["per_agent_today"] = base.get("per_agent_today", {})
    result["peak_messages"] = base["peak_messages"]
    result["peak_date"] = base["peak_date"]
    return result


def validate_points(value: object) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value <= 10):
        raise ValueError("Score must be a finite number from 0 to 10")
    return round(float(value), 2)


def mock_jev_score(message: str, forced_score: float | None = None,
                   fail: bool = False) -> tuple[float, str]:
    """Replaceable imaginary API boundary; no network and no real Jev schema."""
    if fail:
        raise RuntimeError("Simulated Jev API failure")
    if forced_score is not None:
        score = validate_points(forced_score)
    else:
        if not message.strip():
            raise ValueError("Message text required on stdin unless --mock-score is set")
        score = min(10, 1 + min(len(message) // 180, 5)
                    + 2 * ("```" in message)
                    + min(message.count("?") + message.count("？"), 2))
    return validate_points(score), "jev-mock-local"


def laya_score(message: str) -> tuple[float, str]:
    """Call the existing loopback Laya service, never TypeSafe/remote Jev."""
    if not message.strip():
        raise ValueError("Message text required on stdin for local Laya scoring")
    payload = {
        "state": {"body": message},
        "questions": {"brain_load": {
            "type": "score",
            "instructions": "用户构思并提出这条请求、与助手协作时，大致需要付出多少思考？按信息密度、步骤数和专业知识门槛评分；这是娱乐性估计。",
            "criteria": LAYA_LABELS,
        }},
    }
    request = Request(LAYA_URL,
                      data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json; charset=utf-8"},
                      method="POST")
    # Never let HTTP_PROXY or system proxy settings route message text away
    # from the user's machine. The endpoint itself is fixed to loopback.
    opener = build_opener(ProxyHandler({}))
    with opener.open(request, timeout=15) as response:
        data = json.load(response)
    answer = data["answers"]["brain_load"]
    if answer["type"] != "score":
        raise ValueError("Local Laya did not return a score answer")
    raw = answer["score"]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw) or not 0 <= raw <= 10:
        raise ValueError("Local Laya returned an invalid 0–10 score")
    return validate_points(raw), "laya-local-proxy"


def eligibility(origin: str, mode: str) -> str | None:
    if origin != "manual-user":
        return "origin_not_confirmed_manual_user"
    if mode != "normal":
        return "normal_interactive_mode_not_confirmed"
    return None


def open_state(path: Path, create: bool) -> sqlite3.Connection | None:
    if not path.exists() and not create:
        return None
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    if create:
        conn.execute("""CREATE TABLE IF NOT EXISTS debits (
            event_id TEXT PRIMARY KEY,
            local_day TEXT NOT NULL,
            points REAL NOT NULL CHECK (points BETWEEN 0 AND 10),
            provider TEXT NOT NULL,
            agent TEXT NOT NULL DEFAULT 'codex'
        )""")
        migrate_state(conn)
        # Agents without a turn identifier need a durable per-session counter,
        # so a redelivered prompt still resolves to the same event id.
        conn.execute("""CREATE TABLE IF NOT EXISTS hook_turns (
            agent TEXT NOT NULL,
            session_key TEXT NOT NULL,
            sequence INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (agent, session_key)
        )""")
        conn.commit()
    return conn


def migrate_state(conn: sqlite3.Connection) -> None:
    """Add the multi-agent column to a ledger written by headroom 0.x."""
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(debits)")}
    except sqlite3.Error:
        return
    if columns and "agent" not in columns:
        conn.execute("ALTER TABLE debits ADD COLUMN agent TEXT NOT NULL DEFAULT 'codex'")
        conn.commit()


def allocate_event_id(state_path: Path, agent: str, session_key: str) -> str:
    """Next opaque event id for a session that has no turn identifier.

    The counter is incremented under ``BEGIN IMMEDIATE``, so two hooks racing on
    the same session still get distinct ids, and a redelivered hook for the same
    turn is caught by the caller's own duplicate detection, not by this.
    """
    if not OPAQUE_ID.fullmatch(session_key):
        raise ValueError("--session-key must be an opaque ID of 1–128 ASCII letters, digits, . _ : or -")
    conn = open_state(state_path, create=True)
    assert conn is not None
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""INSERT INTO hook_turns (agent, session_key, sequence) VALUES (?, ?, 1)
                        ON CONFLICT(agent, session_key) DO UPDATE SET sequence = sequence + 1""",
                     (agent, session_key))
        row = conn.execute("SELECT sequence FROM hook_turns WHERE agent = ? AND session_key = ?",
                           (agent, session_key)).fetchone()
        conn.commit()
        digest = hashlib.sha256(f"{agent}:{session_key}:{row[0]}".encode("utf-8")).hexdigest()
        return f"seq-{digest[:40]}"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def spent_points(conn: sqlite3.Connection | None, day: date) -> float:
    if conn is None:
        return 0
    row = conn.execute("SELECT COALESCE(SUM(points), 0) FROM debits WHERE local_day = ?",
                       (day.isoformat(),)).fetchone()
    return round(float(row[0]), 2)


def spent_by_agent(conn: sqlite3.Connection | None, day: date) -> dict[str, float]:
    """Per-agent spend for the day; empty when the ledger predates the column."""
    if conn is None:
        return {}
    try:
        rows = conn.execute(
            "SELECT agent, COALESCE(SUM(points), 0) FROM debits WHERE local_day = ? GROUP BY 1",
            (day.isoformat(),)).fetchall()
    except sqlite3.Error:
        return {}
    return {str(name): round(float(total), 2) for name, total in rows}


def spent_source() -> str:
    configured = os.environ.get("HEADROOM_SPENT_SOURCE", "auto").strip().lower()
    if configured not in SPENT_SOURCES:
        raise ValueError("HEADROOM_SPENT_SOURCE must be auto, ledger, or counts")
    return configured


def resolve_spent(base: dict, as_of: date, state_path: Path) -> dict:
    """Today's spend, and where it came from.

    Scored debits need a hook on every agent, which most setups do not have. So
    ``auto`` counts the day's own messages until a hook scores something, then
    hands over to the ledger. Counting and scoring never mix for the same day,
    which is what keeps the number from being double-charged.
    """
    source = spent_source()
    counted = int(base.get("today_messages", 0))
    conn = open_state(state_path, create=False)
    try:
        ledger = spent_points(conn, as_of)
    finally:
        if conn is not None:
            conn.close()
    if source != "ledger" and (source == "counts" or ledger <= 0):
        return {"points": round(counted * COUNT_POINTS, 2), "origin": "counts",
                "messages": counted}
    return {"points": ledger, "origin": "ledger", "messages": None}


def view(base: dict, as_of: date, spent: float, *, origin: str | None = None) -> dict:
    cap = base["cap_points"]
    result = {"as_of": as_of.isoformat(), "timezone": "Asia/Shanghai",
              "prototype_only": True, "peak_date": base["peak_date"],
              "peak_messages": base["peak_messages"], "cap_points": cap,
              "spent_points": spent,
              "left_percent": round(max(0, (cap - spent) / cap * 100), 2),
              "provisional_floor": base["provisional_floor"]}
    if origin is not None:
        result["spent_origin"] = origin
    return result


def status(base: dict, as_of: date, state_path: Path) -> dict:
    resolved = resolve_spent(base, as_of, state_path)
    conn = open_state(state_path, create=False)
    try:
        result = {"action": "status",
                  **view(base, as_of, resolved["points"], origin=resolved["origin"])}
        by_agent = spent_by_agent(conn, as_of)
        if by_agent:
            result["spent_by_agent"] = by_agent
        result["per_agent_counts"] = base.get("per_agent_counts", {})
        result["per_agent_today"] = base.get("per_agent_today", {})
        result["today_messages"] = base.get("today_messages", 0)
        if base.get("agents"):
            result["agents"] = base["agents"]
        return result
    finally:
        if conn is not None:
            conn.close()


def score_event(base: dict, as_of: date, state_path: Path, event_id: str,
                origin: str, mode: str, backend: str, message: str,
                forced_score: float | None = None, fail: bool = False,
                agent: str = DEFAULT_AGENT) -> dict:
    if not OPAQUE_ID.fullmatch(event_id):
        raise ValueError("--event-id must be an opaque ID of 1–128 ASCII letters, digits, . _ : or -")
    if not OPAQUE_ID.fullmatch(agent):
        raise ValueError("agent must be an opaque ID of 1–128 ASCII letters, digits, . _ : or -")
    reason = eligibility(origin, mode)
    if reason:
        return {**status(base, as_of, state_path), "action": "skipped", "reason": reason,
                "charged_points": 0}
    conn = open_state(state_path, create=True)
    assert conn is not None
    try:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute("SELECT local_day, points FROM debits WHERE event_id = ?",
                                (event_id,)).fetchone()
        if previous is not None:
            result = {"action": "duplicate", "charged_points": 0,
                      "original_day": previous[0], "original_points": previous[1],
                      **view(base, as_of, spent_points(conn, as_of))}
            conn.rollback()
            return result
        try:
            points, provider = (laya_score(message) if backend == "laya"
                                else mock_jev_score(message, forced_score, fail))
        except (OSError, RuntimeError, ValueError, KeyError, TypeError,
                UnicodeError, json.JSONDecodeError):
            result = {"action": "skipped", "reason": "scorer_failed",
                      "charged_points": 0,
                      **view(base, as_of, spent_points(conn, as_of))}
            conn.rollback()
            return result
        conn.execute("INSERT INTO debits VALUES (?, ?, ?, ?, ?)",
                     (event_id, as_of.isoformat(), points, provider, agent))
        total = spent_points(conn, as_of)
        conn.commit()
        return {"action": "charged", "charged_points": points, "agent": agent,
                "provider": provider, **view(base, as_of, total)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def select_adapters(only: str | None, homes: list[str] | None,
                    codex_home: Path | None = None) -> list[AgentAdapter]:
    """Resolve the requested agents, applying any per-agent root overrides."""
    overrides: dict[str, Path] = {}
    if codex_home is not None:
        overrides[DEFAULT_AGENT] = codex_home
    for item in homes or []:
        name, separator, path = item.partition("=")
        if not separator or not path.strip():
            raise ValueError(f"--agent-home expects NAME=PATH, got {item!r}")
        overrides[name.strip()] = Path(path).expanduser()
    if only in (None, "", "auto"):
        chosen = discover()
    else:
        chosen = discover([name.strip() for name in only.split(",") if name.strip()])
    adapters = []
    for adapter in chosen:
        if adapter.name in overrides:
            adapter.root = overrides[adapter.name]
        adapters.append(adapter)
    return adapters


def agents_report(as_of: date, adapters: list[AgentAdapter] | None = None) -> dict:
    """Discovery result: what exists on this machine and what it recorded."""
    start, end = window(as_of)
    adapters = discover() if adapters is None else adapters
    report = collect_counts(adapters, start, end)
    known = {entry["name"] for entry in catalog()}
    return {
        "action": "agents",
        "as_of": as_of.isoformat(),
        "timezone": "Asia/Shanghai",
        "window": [start.isoformat(), end.isoformat()],
        "selected": [entry["name"] for entry in report.values()],
        "pooled_counts": pooled_counts(report, start, end),
        "agents": [
            {**agent_summary(entry), "daily_message_counts": entry["counts"]}
            for entry in report.values()
        ],
        "known": sorted(known),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    # CODEX_HOME is honoured by the codex adapter's default root, and
    # default_state_path() falls back to the legacy Codex-era ledger.
    parser.add_argument("--codex-home", type=Path, default=None,
                        help="Codex home; overrides the codex adapter's default root")
    parser.add_argument("--agent-home", action="append", metavar="NAME=PATH",
                        help="override one agent's root; repeatable")
    parser.add_argument("--agents", default=os.environ.get("HEADROOM_AGENTS", "auto"),
                        help="'auto' to discover every installed agent, or a comma list")
    parser.add_argument("--state-path", type=Path, default=default_state_path())
    parser.add_argument("--as-of", type=date.fromisoformat,
                        default=datetime.now(SHANGHAI).date())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="read balance only; does not charge")
    sub.add_parser("agents", help="list discovered agents and their recent counts")
    turn = sub.add_parser("turn", help="score this direct user turn and return balance")
    turn.add_argument("--light", action="store_true",
                      help="skip history discovery; charge only, for hook latency")
    scoring = sub.add_parser("score")
    for command in (turn, scoring):
        identity = command.add_mutually_exclusive_group(required=True)
        identity.add_argument("--event-id",
                              help="opaque turn id, when the agent provides one")
        identity.add_argument("--session-key",
                              help="opaque session id; headroom allocates the turn id")
        command.add_argument("--origin", choices=["manual-user", "scheduled", "subagent",
                                                   "background", "automatic", "unknown"],
                             default="unknown")
        command.add_argument("--mode", choices=["normal", "goal", "task-automation", "unknown"],
                             default="unknown")
        command.add_argument("--backend", choices=["mock", "laya"], default="mock")
        command.add_argument("--agent", default=DEFAULT_AGENT,
                             help="which agent this turn came from")
        fake = command.add_mutually_exclusive_group()
        fake.add_argument("--mock-score", type=float)
        fake.add_argument("--mock-error", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        adapters = select_adapters(args.agents, args.agent_home, args.codex_home)
    except (UnknownAgentError, ValueError) as exc:
        parser.error(str(exc))
    if args.command == "agents":
        print(json.dumps(agents_report(args.as_of, adapters), ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "turn" and args.light:
        # A hook only reads action/charged_points/provider; scanning every agent's
        # history on each prompt would be pure latency. The cap here is a stub.
        base = {"peak_date": args.as_of.isoformat(), "peak_messages": 0,
                "cap_points": 1, "provisional_floor": True, "per_agent_counts": {},
                "agents": []}
    else:
        base = baseline(adapters, args.as_of)
    if args.command == "status":
        result = status(base, args.as_of, args.state_path)
    else:
        if args.backend == "laya" and (args.mock_score is not None or args.mock_error):
            parser.error("--mock-score/--mock-error require --backend mock")
        event_id = args.event_id or allocate_event_id(args.state_path, args.agent,
                                                      args.session_key)
        read_text = (eligibility(args.origin, args.mode) is None
                     and (args.backend == "laya" or (args.mock_score is None and not args.mock_error)))
        # PowerShell pipes UTF-8 here even when Python reports a GBK stdin
        # encoding on Windows. Decode explicitly to avoid surrogate text.
        message = sys.stdin.buffer.read().decode("utf-8") if read_text else ""
        result = score_event(base, args.as_of, args.state_path, event_id,
                             args.origin, args.mode, args.backend, message,
                             args.mock_score, args.mock_error, args.agent)
        if args.command == "turn":
            result["action_scope"] = "current_user_turn"
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, sqlite3.Error, RuntimeError, ValueError, KeyError, TypeError,
            UnicodeError, AgentSourceError) as exc:
        print(f"HEADROOM ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
