"""Config-declared adapters for agents headroom has no built-in reader for.

Drop a JSON file at ``$HEADROOM_AGENTS_CONFIG`` (default
``~/.headroom/agents.json``) and describe where the agent keeps its history:

```json
{
  "agents": [
    {
      "name": "aider",
      "label": "Aider",
      "kind": "jsonl",
      "root": "~/.aider",
      "glob": "**/*.history",
      "where": {"role": "user"},
      "day_field": "timestamp",
      "day_format": "ms"
    },
    {
      "name": "example-sqlite",
      "kind": "sqlite",
      "root": "~/.example",
      "database": "history.db",
      "sql": "SELECT created_at, COUNT(*) FROM prompts GROUP BY 1",
      "day_format": "iso"
    }
  ]
}
```

This is the escape hatch: adding an agent should not require editing headroom.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

from .base import (AgentAdapter, AgentSourceError, bump, empty_counts,
                   iso_to_day, iter_json_lines, ms_to_day, recent_files,
                   seconds_to_day, sqlite_ro)

DEFAULT_CONFIG = Path.home() / ".headroom" / "agents.json"
DAY_FORMATS = {
    "ms": ms_to_day,
    "iso": iso_to_day,
    "epoch": seconds_to_day,
    "seconds": seconds_to_day,
}


def config_path() -> Path:
    configured = os.environ.get("HEADROOM_AGENTS_CONFIG")
    return Path(configured).expanduser() if configured else DEFAULT_CONFIG


def load_declarations() -> list[dict]:
    """Malformed config is ignored rather than crashing every display."""
    path = config_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    agents = payload.get("agents")
    if not isinstance(agents, list):
        return []
    return [entry for entry in agents if isinstance(entry, dict)
            and isinstance(entry.get("name"), str) and entry["name"].strip()]


class CustomAdapter(AgentAdapter):
    """One declared agent. Instances carry their own name and label."""

    source_kind = "declared"

    def __init__(self, declaration: dict):
        self.declaration = declaration
        self.name = declaration["name"].strip()
        self.label = str(declaration.get("label") or self.name)
        self.kind = str(declaration.get("kind") or "jsonl").lower()
        self.supports_hooks = bool(declaration.get("hooks", False))
        root = declaration.get("root")
        super().__init__(root if root else None)

    @classmethod
    def fallback_root(cls) -> Path:
        return Path.home()

    # --------------------------------------------------------------- discovery

    def _glob(self) -> str | None:
        pattern = self.declaration.get("glob")
        return pattern if isinstance(pattern, str) and pattern else None

    def source_paths(self) -> list[Path]:
        if self.kind == "sqlite":
            database = self.declaration.get("database")
            return [self.root / database] if isinstance(database, str) and database else [self.root]
        pattern = self._glob()
        return sorted(self.root.glob(pattern)) if pattern else [self.root]

    def is_available(self) -> bool:
        if self.kind == "sqlite":
            database = self.declaration.get("database")
            return isinstance(database, str) and (self.root / database).is_file()
        pattern = self._glob()
        return bool(pattern) and any(True for _ in self.root.glob(pattern))

    # ------------------------------------------------------------------ counts

    def daily_counts(self, start: date, end: date) -> dict[str, int]:
        counts = empty_counts(start, end)
        if self.kind == "sqlite":
            return self._sqlite_counts(counts, start, end)
        if self.kind != "jsonl":
            raise AgentSourceError(f"Unknown declared kind: {self.kind!r}")
        return self._jsonl_counts(counts, start, end)

    def _day_of(self, value):
        name = str(self.declaration.get("day_format") or "ms").lower()
        parser = DAY_FORMATS.get(name)
        if parser is None:
            raise AgentSourceError(f"Unknown day_format: {name!r}")
        return parser(value)

    def _jsonl_counts(self, counts, start, end) -> dict[str, int]:
        paths = recent_files(self.source_paths(), start)
        if not paths:
            raise AgentSourceError(f"No files matched {self._glob()!r} under {self.root}")
        field = str(self.declaration.get("day_field") or "timestamp")
        where = self.declaration.get("where")
        where = where if isinstance(where, dict) else {}
        for path in paths:
            for record in iter_json_lines(path):
                if any(record.get(key) != value for key, value in where.items()):
                    continue
                bump(counts, self._day_of(record.get(field)), start, end)
        return counts

    def _sqlite_counts(self, counts, start, end) -> dict[str, int]:
        database = self.declaration.get("database")
        sql = self.declaration.get("sql")
        if not isinstance(database, str) or not isinstance(sql, str):
            raise AgentSourceError("Declared sqlite agents need 'database' and 'sql'")
        conn = sqlite_ro(self.root / database)
        try:
            for row in conn.execute(sql):
                if not isinstance(row, (list, tuple)) or not row:
                    continue
                day = self._day_of(row[0])
                total = row[1] if len(row) > 1 and isinstance(row[1], int) else 1
                if day is not None and start.isoformat() <= day < end.isoformat():
                    counts[day] = counts.get(day, 0) + total
        except Exception as exc:  # sqlite3.Error and declared-schema problems
            raise AgentSourceError(f"Cannot read declared agent {self.name}: {exc}") from exc
        finally:
            conn.close()
        return counts
