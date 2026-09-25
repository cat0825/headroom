"""Google Antigravity / Gemini local history.

Antigravity writes one transcript per conversation under
``antigravity/brain/<conversation>/.system_generated/logs/transcript_full.jsonl``.
Human prompts appear as ``type: "USER_INPUT"`` with ``source: "USER_EXPLICIT"``.

The classic Gemini CLI chat files (``tmp/<hash>/chats/session-*.json``) are read
best-effort as well; that layout is not present on every install.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .base import (AgentAdapter, AgentSourceError, bump, empty_counts,
                   iso_to_day, iter_json_lines, recent_files)

TRANSCRIPT_GLOB = "antigravity/brain/*/.system_generated/logs/transcript_full.jsonl"
CLI_CHAT_GLOB = "tmp/*/chats/session-*.json"
HUMAN_SOURCES = {None, "USER_EXPLICIT"}


class AntigravityAdapter(AgentAdapter):
    name = "antigravity"
    label = "Antigravity / Gemini"
    source_kind = "jsonl"
    supports_hooks = True
    env_var = "GEMINI_DIR"

    @classmethod
    def fallback_root(cls) -> Path:
        return Path.home() / ".gemini"

    def brain_dir(self) -> Path:
        return self.root / "antigravity" / "brain"

    def transcript_paths(self) -> list[Path]:
        if not self.brain_dir().is_dir():
            return []
        return sorted(self.brain_dir().glob("*/.system_generated/logs/transcript_full.jsonl"))

    def cli_chat_paths(self) -> list[Path]:
        tmp = self.root / "tmp"
        if not tmp.is_dir():
            return []
        return sorted(tmp.glob("*/chats/session-*.json"))

    def source_paths(self) -> list[Path]:
        return [self.brain_dir(), self.root / "tmp"]

    def is_available(self) -> bool:
        return bool(self.transcript_paths() or self.cli_chat_paths())

    def daily_counts(self, start: date, end: date) -> dict[str, int]:
        counts = empty_counts(start, end)
        transcripts = recent_files(self.transcript_paths(), start)
        chats = recent_files(self.cli_chat_paths(), start)
        if not transcripts and not chats:
            raise AgentSourceError("No Antigravity or Gemini transcripts found")
        for path in transcripts:
            for record in iter_json_lines(path):
                if record.get("type") != "USER_INPUT":
                    continue
                if record.get("source") not in HUMAN_SOURCES:
                    continue
                bump(counts, iso_to_day(record.get("created_at")), start, end)
        for path in chats:
            self._count_cli_chat(path, counts, start, end)
        return counts

    @staticmethod
    def _count_cli_chat(path: Path, counts: dict[str, int], start: date, end: date) -> None:
        """Tolerant reader for the Gemini CLI chat JSON; unknown shapes count 0."""
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return
        for message in messages:
            if not isinstance(message, dict) or message.get("type") != "user":
                continue
            stamp = message.get("timestamp") or message.get("createdAt")
            bump(counts, iso_to_day(stamp), start, end)
