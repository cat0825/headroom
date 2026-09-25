"""Agent-agnostic history sources for headroom.

Every adapter answers exactly one question: how many direct human prompts did
this agent record on each of the last N complete local days? Nothing else about
an agent leaks into the ledger, the scorer, or the display layer.

Adapters never read prompt bodies into the ledger. They count records.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import ClassVar, Iterator


SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")


class AgentSourceError(RuntimeError):
    """The agent's local history exists but cannot be read."""


def day_bounds(start: date, end: date) -> list[date]:
    """Every local day in the half-open range [start, end)."""
    return [start + timedelta(days=i) for i in range((end - start).days)]


def empty_counts(start: date, end: date) -> dict[str, int]:
    return {day.isoformat(): 0 for day in day_bounds(start, end)}


def day_start_ms(day: date) -> int:
    return int(datetime.combine(day, time.min, SHANGHAI).timestamp() * 1000)


def ms_to_day(value: object) -> str | None:
    """Epoch milliseconds to a local Asia/Shanghai date."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, SHANGHAI).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def seconds_to_day(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return ms_to_day(value * 1000)


def iso_to_day(value: object) -> str | None:
    """Parse an ISO-8601 timestamp, tolerating a trailing Z and naive values."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        return moment.astimezone(SHANGHAI).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def bump(counts: dict[str, int], day: str | None, start: date, end: date) -> None:
    """Increment only days inside the window; ISO strings compare correctly."""
    if day is not None and start.isoformat() <= day < end.isoformat():
        counts[day] = counts.get(day, 0) + 1


def iter_json_lines(path: Path) -> Iterator[dict]:
    """Stream a JSONL file, skipping malformed lines instead of failing."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError as exc:
        raise AgentSourceError(f"Cannot read {path}: {exc}") from exc


def recent_files(paths: list[Path], start: date) -> list[Path]:
    """Drop append-only logs whose last write predates the window.

    A transcript's mtime is its final append, so a file untouched since before
    the window cannot contain a record inside it. This is what keeps a 159 MB
    history directory from being rescanned on every ten-second refresh.
    """
    cutoff = day_start_ms(start) / 1000
    keep: list[Path] = []
    for path in paths:
        try:
            if path.stat().st_mtime >= cutoff:
                keep.append(path)
        except OSError:
            continue
    return keep


def file_fingerprint(paths: list[Path]) -> str:
    """Cheap change detector: name, size, and mtime of every source file."""
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: str(item)):
        try:
            stat = path.stat()
        except OSError:
            continue
        digest.update(f"{path}\0{stat.st_size}\0{stat.st_mtime_ns}\0".encode("utf-8"))
    return digest.hexdigest()[:32]


class AgentAdapter:
    """One local AI agent's history, reduced to per-day prompt counts."""

    name: ClassVar[str] = ""
    label: ClassVar[str] = ""
    source_kind: ClassVar[str] = "unknown"
    supports_hooks: ClassVar[bool] = False
    env_var: ClassVar[str | None] = None
    #: Glob patterns, relative to root, naming the files this adapter reads.
    source_globs: ClassVar[tuple[str, ...]] = ()
    #: Fixed paths, relative to root, that must exist for this adapter to run.
    source_files: ClassVar[tuple[str, ...]] = ()

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root).expanduser() if root is not None else self.default_root()

    # ------------------------------------------------------------------ roots

    @classmethod
    def default_root(cls) -> Path:
        """Environment override first, then the platform default."""
        if cls.env_var and os.environ.get(cls.env_var):
            return Path(os.environ[cls.env_var]).expanduser()
        return cls.fallback_root()

    @classmethod
    def fallback_root(cls) -> Path:
        raise NotImplementedError

    # --------------------------------------------------------------- discovery

    def source_paths(self) -> list[Path]:
        """Concrete files/directories that prove this agent has local history."""
        paths = [self.root / name for name in self.source_files]
        for pattern in self.source_globs:
            paths.extend(sorted(self.root.glob(pattern)))
        return paths

    def is_available(self) -> bool:
        return any(path.exists() for path in self.source_paths())

    def fingerprint(self) -> str:
        return file_fingerprint([path for path in self.source_paths() if path.is_file()])

    # ------------------------------------------------------------------ counts

    def daily_counts(self, start: date, end: date) -> dict[str, int]:
        """Direct human prompts per local day across [start, end)."""
        raise NotImplementedError

    def describe(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "kind": self.source_kind,
            "hooks": self.supports_hooks,
            "root": str(self.root),
            "sources": [str(path) for path in self.source_paths()],
            "available": self.is_available(),
        }


def sqlite_ro(path: Path) -> sqlite3.Connection:
    """Read-only connection; never creates or locks the agent's database."""
    if not path.is_file():
        raise AgentSourceError(f"History database not found: {path}")
    try:
        return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        raise AgentSourceError(f"Cannot open {path}: {exc}") from exc


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error as exc:
        raise AgentSourceError(f"Cannot inspect table {table}: {exc}") from exc
