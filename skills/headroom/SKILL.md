---
name: headroom
description: Track a playful daily headroom meter for direct Codex user turns, or report its remaining percentage. The optional local hook charges turns automatically; this is entertainment, not a cognitive measurement.
---

# headroom

> I need a reset.

This is an entertainment-only meter, not a measure of intelligence or health. The default scorer is a deterministic fake Jev API; `--backend laya` sends the prompt only to the user's loopback Laya service at `http://127.0.0.1:8765/predict`. No cloud Jev API is implemented.

The installed `UserPromptSubmit` hook, when trusted, owns automatic charging and defaults to the deterministic mock scorer. Set `HEADROOM_BACKEND=laya` only when the loopback Laya service is running. **Do not also run `turn` for a hook-covered turn**, or it may get a second event ID and debit twice. Without an active hook, use `python scripts/headroom.py turn --event-id <opaque-turn-id> --origin manual-user --mode normal --backend laya` from this skill directory, piping the UTF-8 user message on stdin. Never derive the event ID from message content.

Use `python scripts/headroom.py status` for a read-only balance. The browser dashboard is `python scripts/headroom_dashboard.py`, at `http://127.0.0.1:8766/`; its Refresh button and 10-second refresh never charge. The default shared ledger is `$CODEX_HOME/headroom/ledger.sqlite3` (or `~/.codex/headroom/ledger.sqlite3`). Set `HEADROOM_STATE_PATH` to override it. The ledger stores only opaque event IDs, local dates, scores, and provider names - not prompt bodies.

The daily cap is the highest count of all `userMessage` items in any of the previous seven complete Asia/Shanghai days, multiplied by five. Today's scores are 0-10 points per turn, displayed as percent left. Zero history yields a provisional five-point cap. The balance resets by calendar day; zero never blocks conversation.

Only direct human turns in normal interactive mode should charge. Skip plan/Goal mode, scheduled/background work, subagents, continuations, unknown provenance, and scorer errors. The hook rejects known non-interactive sources and plan mode, but Codex's current `UserPromptSubmit` payload does not independently certify human origin; this is a best-effort amusement, not an audit-grade exclusion. A duplicate session-plus-turn ID charges once.

Install the user hooks with `python hooks/install_hooks.py` (or `powershell -ExecutionPolicy Bypass -File hooks/install_windows.ps1` on Windows) from this skill directory. Existing `hooks.json` entries are safely preserved via non-destructive merge. Review and trust the two definitions in Codex `/hooks`, then start a new session. Plugin installation or implicit skill selection alone does not activate lifecycle hooks. The `SessionStart` hook launches the dashboard on loopback; `UserPromptSubmit` scores asynchronously and never blocks a response. Run `python tests/test_headroom.py` for the mechanical checks.

