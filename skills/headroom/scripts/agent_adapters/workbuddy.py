"""WorkBuddy local history: per-session JSONL transcripts.

WorkBuddy writes ``projects/<workspace>/<session>.jsonl`` where a human turn is
``{"type": "message", "role": "user", "timestamp": <epoch ms>}``. Tool traffic
uses its own ``function_call`` / ``function_call_result`` record types, so a
``role: "user"`` message is always a real turn and needs no content sniffing.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .base import (AgentAdapter, AgentSourceError, bump, empty_counts,
                   iter_json_lines, ms_to_day, recent_files)


class WorkBuddyAdapter(AgentAdapter):
    name = "workbuddy"
    label = "WorkBuddy"
    source_kind = "jsonl"
    supports_hooks = False
    env_var = "WORKBUDDY_HOME"

    @classmethod
    def fallback_root(cls) -> Path:
        return Path.home() / ".workbuddy-ai"

    def projects_dir(self) -> Path:
        return self.root / "projects"

    def source_paths(self) -> list[Path]:
        return [self.projects_dir()]

    def transcript_paths(self) -> list[Path]:
        if not self.projects_dir().is_dir():
            return []
        # file-rollback.ndjson sits beside each transcript and is not a transcript.
        return sorted(path for path in self.projects_dir().glob("*/*.jsonl")
                      if not path.name.endswith(".file-rollback.ndjson"))

    def is_available(self) -> bool:
        return bool(self.transcript_paths())

    def daily_counts(self, start: date, end: date) -> dict[str, int]:
        counts = empty_counts(start, end)
        transcripts = recent_files(self.transcript_paths(), start)
        if not transcripts:
            raise AgentSourceError("No WorkBuddy transcripts found")
        for path in transcripts:
            for record in iter_json_lines(path):
                if record.get("type") != "message" or record.get("role") != "user":
                    continue
                bump(counts, ms_to_day(record.get("timestamp")), start, end)
        return counts
