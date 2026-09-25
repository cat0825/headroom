"""Claude Code local history: per-project JSONL transcripts.

A ``type: "user"`` record is not automatically a human prompt. Tool results are
replayed back as user-role messages, so the adapter requires either a plain
string body or a ``text`` block, and drops ``system``/``sdk`` prompt sources and
sidechain (subagent) records.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .base import (AgentAdapter, AgentSourceError, bump, empty_counts,
                   iso_to_day, iter_json_lines, ms_to_day, recent_files)

#: Prompt sources that are not a person typing into the CLI.
NON_HUMAN_SOURCES = {"system", "sdk"}


def is_human_prompt(record: dict) -> bool:
    if record.get("promptSource") in NON_HUMAN_SOURCES:
        return False
    message = record.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        for block in content:
            if (isinstance(block, dict) and block.get("type") == "text"
                    and isinstance(block.get("text"), str) and block["text"].strip()):
                return True
    return False


class ClaudeCodeAdapter(AgentAdapter):
    name = "claude"
    label = "Claude Code"
    source_kind = "jsonl"
    supports_hooks = True
    env_var = "CLAUDE_CONFIG_DIR"
    source_globs = ("projects/*/*.jsonl",)

    @classmethod
    def fallback_root(cls) -> Path:
        return Path.home() / ".claude"

    def projects_dir(self) -> Path:
        return self.root / "projects"

    def prompt_log(self) -> Path:
        """Flat prompt history: a cheap fallback when no transcripts survive."""
        return self.root / "history.jsonl"

    def source_paths(self) -> list[Path]:
        return [self.projects_dir(), self.prompt_log()]

    def transcript_paths(self) -> list[Path]:
        if not self.projects_dir().is_dir():
            return []
        return sorted(self.projects_dir().glob("*/*.jsonl"))

    def daily_counts(self, start: date, end: date) -> dict[str, int]:
        counts = empty_counts(start, end)
        # Stale transcripts are skipped, not treated as missing: a window with
        # no Claude Code activity is a real zero, not a reason to fall back.
        transcripts = self.transcript_paths()
        if transcripts:
            for path in recent_files(transcripts, start):
                for record in iter_json_lines(path):
                    if record.get("type") != "user" or record.get("isSidechain"):
                        continue
                    if not is_human_prompt(record):
                        continue
                    bump(counts, iso_to_day(record.get("timestamp")), start, end)
            return counts
        log = self.prompt_log()
        if not log.is_file():
            raise AgentSourceError("No Claude Code transcripts or prompt log found")
        for record in iter_json_lines(log):
            bump(counts, ms_to_day(record.get("timestamp")), start, end)
        return counts
