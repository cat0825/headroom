"""opencode local history: the shared ``opencode.db`` message table.

opencode stores one row per message with a JSON ``data`` blob; the role lives at
``$.role`` and the timestamp in ``time_created`` (epoch milliseconds).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date
from pathlib import Path

from .base import (AgentAdapter, AgentSourceError, day_start_ms, empty_counts,
                   sqlite_ro, table_columns)


class OpenCodeAdapter(AgentAdapter):
    name = "opencode"
    label = "opencode"
    source_kind = "sqlite"
    supports_hooks = False
    env_var = "OPENCODE_DATA_HOME"
    source_files = ("opencode.db",)

    @classmethod
    def fallback_root(cls) -> Path:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
        return base / "opencode"

    def database(self) -> Path:
        return self.root / "opencode.db"

    def is_available(self) -> bool:
        return self.database().is_file()

    def daily_counts(self, start: date, end: date) -> dict[str, int]:
        counts = empty_counts(start, end)
        conn = sqlite_ro(self.database())
        try:
            if not {"data", "time_created"} <= table_columns(conn, "message"):
                raise AgentSourceError("Unexpected opencode message schema")
            rows = conn.execute(
                """SELECT date(time_created / 1000.0, 'unixepoch', '+8 hours'), COUNT(*)
                   FROM message
                   WHERE json_extract(data, '$.role') = 'user'
                     AND time_created >= ? AND time_created < ?
                   GROUP BY 1""",
                (day_start_ms(start), day_start_ms(end)),
            )
            for local_date, count in rows:
                if local_date in counts:
                    counts[local_date] = count
        except sqlite3.Error as exc:
            # json_extract needs the JSON1 extension; say so instead of returning 0.
            raise AgentSourceError(f"Cannot read opencode history: {exc}") from exc
        finally:
            conn.close()
        return counts
