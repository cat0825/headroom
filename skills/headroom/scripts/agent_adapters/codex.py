"""Codex local history: the ``thread_history_1.sqlite`` index."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from .base import (AgentAdapter, AgentSourceError, day_start_ms, empty_counts,
                   sqlite_ro, table_columns)


class CodexAdapter(AgentAdapter):
    name = "codex"
    label = "Codex"
    source_kind = "sqlite"
    supports_hooks = True
    env_var = "CODEX_HOME"
    source_files = ("thread_history_1.sqlite",)

    @classmethod
    def fallback_root(cls) -> Path:
        return Path.home() / ".codex"

    def __init__(self, root: Path | str | None = None, database: Path | str | None = None):
        super().__init__(root)
        #: Explicit index path, for callers holding a file rather than a CODEX_HOME.
        self._database = Path(database).expanduser() if database is not None else None

    def history_db(self) -> Path:
        return self._database or (self.root / "thread_history_1.sqlite")

    def is_available(self) -> bool:
        return self.history_db().is_file()

    def daily_counts(self, start: date, end: date) -> dict[str, int]:
        counts = empty_counts(start, end)
        conn = sqlite_ro(self.history_db())
        try:
            if not {"item_type", "created_at_ms"} <= table_columns(conn, "thread_items"):
                raise AgentSourceError("Unexpected Codex thread_items schema")
            rows = conn.execute(
                """SELECT date(created_at_ms / 1000.0, 'unixepoch', '+8 hours'), COUNT(*)
                   FROM thread_items
                   WHERE item_type = 'userMessage'
                     AND created_at_ms >= ? AND created_at_ms < ?
                   GROUP BY 1""",
                (day_start_ms(start), day_start_ms(end)),
            )
            for local_date, count in rows:
                if local_date not in counts:
                    raise AgentSourceError("Unexpected local date in Codex index")
                counts[local_date] = count
        except sqlite3.Error as exc:
            raise AgentSourceError(f"Cannot read Codex history: {exc}") from exc
        finally:
            conn.close()
        return counts
