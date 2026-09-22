"""Entertainment-only headroom meter, with fake Jev or local Laya scoring.

History queries select only item type and timestamp metadata. Message text is
never written to the ledger or printed by this program.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener


SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
OPAQUE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
LAYA_URL = "http://127.0.0.1:8765/predict"
LAYA_LABELS = [
    "几乎无需思考", "轻松阅读", "需要一点理解", "需要集中注意", "中等复杂",
    "多步理解", "专业理解", "复杂推理", "高强度分析", "极高强度分析",
    "极端复杂且费脑",
]


def day_start_ms(day: date) -> int:
    return int(datetime.combine(day, time.min, SHANGHAI).timestamp() * 1000)


def baseline(history_db: Path, as_of: date) -> dict:
    """Count every userMessage in the prior seven *complete* local days."""
    if not history_db.is_file():
        raise FileNotFoundError(f"Codex history index not found: {history_db}")
    start = as_of - timedelta(days=7)
    days = [start + timedelta(days=i) for i in range(7)]
    counts = {day.isoformat(): 0 for day in days}
    conn = sqlite3.connect(history_db.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(thread_items)")}
        if not {"item_type", "created_at_ms"} <= columns:
            raise RuntimeError("Unexpected Codex thread_items schema")
        rows = conn.execute(
            """SELECT date(created_at_ms / 1000.0, 'unixepoch', '+8 hours'), COUNT(*)
               FROM thread_items
               WHERE item_type = 'userMessage'
                 AND created_at_ms >= ? AND created_at_ms < ?
               GROUP BY 1""",
            (day_start_ms(start), day_start_ms(as_of)),
        )
        for local_date, count in rows:
            if local_date not in counts:
                raise RuntimeError("Unexpected local date in Codex index")
            counts[local_date] = count
    finally:
        conn.close()
    peak_date = max(counts, key=lambda day: (counts[day], day))
    peak = counts[peak_date]
    return {"peak_date": peak_date, "peak_messages": peak,
            "cap_points": max(1, peak) * 5, "provisional_floor": peak == 0,
            "daily_message_counts": counts}


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
            provider TEXT NOT NULL
        )""")
    return conn


def spent_points(conn: sqlite3.Connection | None, day: date) -> float:
    if conn is None:
        return 0
    row = conn.execute("SELECT COALESCE(SUM(points), 0) FROM debits WHERE local_day = ?",
                       (day.isoformat(),)).fetchone()
    return round(float(row[0]), 2)


def view(base: dict, as_of: date, spent: float) -> dict:
    cap = base["cap_points"]
    return {"as_of": as_of.isoformat(), "timezone": "Asia/Shanghai",
            "prototype_only": True, "peak_date": base["peak_date"],
            "peak_messages": base["peak_messages"], "cap_points": cap,
            "spent_points": spent,
            "left_percent": round(max(0, (cap - spent) / cap * 100), 2),
            "provisional_floor": base["provisional_floor"]}


def status(base: dict, as_of: date, state_path: Path) -> dict:
    conn = open_state(state_path, create=False)
    try:
        return {"action": "status", **view(base, as_of, spent_points(conn, as_of))}
    finally:
        if conn is not None:
            conn.close()


def score_event(base: dict, as_of: date, state_path: Path, event_id: str,
                origin: str, mode: str, backend: str, message: str,
                forced_score: float | None = None, fail: bool = False) -> dict:
    if not OPAQUE_ID.fullmatch(event_id):
        raise ValueError("--event-id must be an opaque ID of 1–128 ASCII letters, digits, . _ : or -")
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
        conn.execute("INSERT INTO debits VALUES (?, ?, ?, ?)",
                     (event_id, as_of.isoformat(), points, provider))
        total = spent_points(conn, as_of)
        conn.commit()
        return {"action": "charged", "charged_points": points,
                "provider": provider, **view(base, as_of, total)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    default_state = Path(os.environ.get(
        "HEADROOM_STATE_PATH",
        str(Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "headroom" / "ledger.sqlite3"),
    ))
    parser.add_argument("--state-path", type=Path, default=default_state)
    parser.add_argument("--as-of", type=date.fromisoformat,
                        default=datetime.now(SHANGHAI).date())
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="read balance only; does not charge")
    turn = sub.add_parser("turn", help="score this direct user turn and return balance")
    scoring = sub.add_parser("score")
    for command in (turn, scoring):
        command.add_argument("--event-id", required=True)
        command.add_argument("--origin", choices=["manual-user", "scheduled", "subagent",
                                                   "background", "automatic", "unknown"],
                             default="unknown")
        command.add_argument("--mode", choices=["normal", "goal", "task-automation", "unknown"],
                             default="unknown")
        command.add_argument("--backend", choices=["mock", "laya"], default="mock")
        fake = command.add_mutually_exclusive_group()
        fake.add_argument("--mock-score", type=float)
        fake.add_argument("--mock-error", action="store_true")
    args = parser.parse_args()
    base = baseline(args.codex_home / "thread_history_1.sqlite", args.as_of)
    if args.command == "status":
        result = status(base, args.as_of, args.state_path)
    else:
        if args.backend == "laya" and (args.mock_score is not None or args.mock_error):
            parser.error("--mock-score/--mock-error require --backend mock")
        read_text = (eligibility(args.origin, args.mode) is None
                     and (args.backend == "laya" or (args.mock_score is None and not args.mock_error)))
        # PowerShell pipes UTF-8 here even when Python reports a GBK stdin
        # encoding on Windows. Decode explicitly to avoid surrogate text.
        message = sys.stdin.buffer.read().decode("utf-8") if read_text else ""
        result = score_event(base, args.as_of, args.state_path, args.event_id,
                             args.origin, args.mode, args.backend, message,
                             args.mock_score, args.mock_error)
        if args.command == "turn":
            result["action_scope"] = "current_user_turn"
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, sqlite3.Error, RuntimeError, ValueError, KeyError, TypeError,
            UnicodeError) as exc:
        print(f"HEADROOM ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
