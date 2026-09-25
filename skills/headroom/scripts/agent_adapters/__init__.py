"""Adapter registry, discovery, and per-day count collection.

Discovery is the point of this package: rather than hard-coding one vendor, ask
every known adapter whether its local history exists, then read the ones that
answer yes. A declared config file extends the set without touching code.
"""

from __future__ import annotations

import os
import time
from datetime import date, timedelta

from .antigravity import AntigravityAdapter
from .base import (AgentAdapter, AgentSourceError, day_bounds, empty_counts,
                   file_fingerprint, recent_files)
from .claude import ClaudeCodeAdapter
from .codex import CodexAdapter
from .custom import CustomAdapter, load_declarations
from .opencode import OpenCodeAdapter
from .workbuddy import WorkBuddyAdapter

__all__ = [
    "AgentAdapter", "AgentSourceError", "AntigravityAdapter", "ClaudeCodeAdapter",
    "CodexAdapter", "CustomAdapter", "OpenCodeAdapter", "WorkBuddyAdapter",
    "all_adapters", "catalog", "collect_counts", "discover", "names",
    "pooled_counts", "recent_files", "resolve",
]

#: Built-in adapters, in the order they are reported.
BUILTIN = (
    CodexAdapter,
    ClaudeCodeAdapter,
    OpenCodeAdapter,
    AntigravityAdapter,
    WorkBuddyAdapter,
)

#: How long a scan stays valid even when nothing on disk changed.
CACHE_TTL = float(os.environ.get("HEADROOM_AGENT_CACHE_TTL", "15"))
_CACHE: dict[tuple, tuple[float, dict[str, int]]] = {}


class UnknownAgentError(ValueError):
    """A requested adapter name is not in the registry."""


def custom_adapters() -> list[AgentAdapter]:
    return [CustomAdapter(entry) for entry in load_declarations()]


def all_adapters() -> list[AgentAdapter]:
    """Every known adapter, available or not. Declared ones win name collisions."""
    builtin = [factory() for factory in BUILTIN]
    declared = custom_adapters()
    taken = {adapter.name for adapter in declared}
    return [adapter for adapter in builtin if adapter.name not in taken] + declared


def names() -> list[str]:
    return [adapter.name for adapter in all_adapters()]


def resolve(name: str) -> AgentAdapter:
    for adapter in all_adapters():
        if adapter.name == name:
            return adapter
    raise UnknownAgentError(f"Unknown agent {name!r}; known: {', '.join(names())}")


def discover(only: list[str] | None = None) -> list[AgentAdapter]:
    """Adapters that can actually read history right now.

    ``only`` restricts the search; an explicitly requested but unavailable agent
    still appears, so the caller can report it instead of silently dropping it.
    """
    if only:
        selected = [resolve(name) for name in only]
    else:
        selected = all_adapters()
    return [adapter for adapter in selected
            if adapter.is_available() or (only is not None)]


def catalog() -> list[dict]:
    """Describe every known adapter, including the ones with nothing on disk."""
    return [adapter.describe() for adapter in all_adapters()]


def _window(start: date, end: date) -> tuple[date, date]:
    if end <= start:
        raise ValueError("end must be after start")
    return start, end


def collect_counts(adapters: list[AgentAdapter], start: date, end: date,
                   *, use_cache: bool = True) -> dict[str, dict]:
    """Per-adapter daily counts for [start, end), plus an error slot per agent."""
    _window(start, end)
    report: dict[str, dict] = {}
    for adapter in adapters:
        key = (adapter.name, str(adapter.root), start.isoformat(), end.isoformat())
        try:
            fingerprint = adapter.fingerprint()
        except (OSError, AgentSourceError):
            fingerprint = "unreadable"
        cached = _CACHE.get(key) if use_cache else None
        if cached and cached[0] > time.monotonic() and cached[1]["fingerprint"] == fingerprint:
            report[adapter.name] = dict(cached[1])
            continue
        entry = {
            "name": adapter.name,
            "label": adapter.label,
            "kind": adapter.source_kind,
            "hooks": adapter.supports_hooks,
            "root": str(adapter.root),
            "available": True,
            "error": None,
            "counts": empty_counts(start, end),
        }
        try:
            counts = adapter.daily_counts(start, end)
        except (AgentSourceError, OSError) as exc:
            entry.update(available=False, error=f"{type(exc).__name__}: {exc}")
        else:
            entry["counts"] = {day: counts.get(day, 0) for day in entry["counts"]}
        entry["fingerprint"] = fingerprint
        if use_cache:
            _CACHE[key] = (time.monotonic() + CACHE_TTL, entry)
        report[adapter.name] = dict(entry)
    return report


def pooled_counts(report: dict[str, dict], start: date, end: date) -> dict[str, int]:
    """Sum every readable agent's counts into one cross-agent ledger view."""
    total = empty_counts(start, end)
    for entry in report.values():
        if not entry.get("available"):
            continue
        for day, count in entry["counts"].items():
            if day in total:
                total[day] += count
    return total


def clear_cache() -> None:
    _CACHE.clear()
