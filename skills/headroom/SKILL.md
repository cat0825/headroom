---
name: headroom
description: Track a playful daily headroom meter pooled across every local AI agent (Codex, Claude Code, opencode, Antigravity/Gemini, WorkBuddy). Reads each agent's own local history; optional per-agent hooks charge turns automatically. Entertainment, not a cognitive measurement.
---

# headroom

> I need a reset.

This is an entertainment-only meter, not a measure of intelligence or health. The default scorer is a deterministic fake Jev API; `--backend laya` sends the prompt only to the user's loopback Laya service at `http://127.0.0.1:8765/predict`. No cloud Jev API is implemented.

## Which agents are supported

Each agent gets an adapter that reads its own local history and answers one question: how many direct human prompts did this agent record per day? Nothing about an agent leaks into the ledger, the scorer, or the display.

| Agent | History source | Hook |
| --- | --- | --- |
| `codex` | `$CODEX_HOME/thread_history_1.sqlite` → `thread_items` | yes |
| `claude` | `$CLAUDE_CONFIG_DIR/projects/*/*.jsonl` (falls back to `history.jsonl`) | yes |
| `opencode` | `$XDG_DATA_HOME/opencode/opencode.db` → `message` | no |
| `antigravity` | `$GEMINI_DIR/antigravity/brain/*/.system_generated/logs/transcript_full.jsonl` | yes |
| `workbuddy` | `$WORKBUDDY_HOME/projects/*/*.jsonl` | no |

`python scripts/headroom.py agents` lists every adapter, whether it is readable on this machine, and its recent per-day counts. Discovery is automatic: `--agents auto` (the default) reads every adapter that has local history.

A JSONL transcript is not a turn log. Claude Code replays tool results as user-role messages, so the Claude adapter requires a plain string body or a `text` block and drops `system`/`sdk` prompt sources and sidechain records. opencode and Codex are read straight from SQL. This is why the counts are not raw record counts.

## One shared ledger

The daily cap pools every readable agent: take the busiest of the previous seven complete Asia/Shanghai days, sum that day across all agents, and multiply by two. Today's scores are 0–10 points per turn, displayed as percent left. Zero history yields a provisional two-point cap. The balance resets by calendar day; zero never blocks conversation.

The ledger defaults to `~/.headroom/ledger.sqlite3`, or the pre-existing `$CODEX_HOME/headroom/ledger.sqlite3` when that already exists. `HEADROOM_STATE_PATH` overrides it. The `debits` table carries an `agent` column; opening a ledger written by headroom 0.x migrates it in place and attributes existing rows to `codex`. The ledger stores only opaque event IDs, local dates, scores, provider names, and agent names — never prompt bodies.

## What the number means

Two independent halves:

- **The cap (the scale)** comes from history: the busiest of the previous seven complete Asia/Shanghai days, summed across every readable agent, doubled. Today is excluded from the cap window.
- **The spend (the numerator)** is today only, and resets at local midnight.

`HEADROOM_SPENT_SOURCE` picks how the spend is derived:

| Value | Behaviour |
| --- | --- |
| `auto` (default) | Count today's messages until a hook scores something, then switch to the ledger |
| `ledger` | Scored debits only. Without hooks, spend stays 0 and the meter reads 100% |
| `counts` | Always `today_messages x 2`, ignoring the ledger |

`COUNT_POINTS` is 2, matching the cap's x2, so a day as busy as your busiest recorded day reads 0% left. Counting and scoring never mix within one day, which is what stops a turn being charged twice.

## Reading the balance

- `python3 scripts/headroom.py status` — read-only balance. Never charges.
- `python3 scripts/headroom.py agents` — discovery report with per-agent counts.
- `python3 scripts/headroom_dashboard.py` — browser dashboard at `http://127.0.0.1:8766/`. It refreshes every 10 seconds, shows a per-agent source breakdown, and its Refresh button and 10-second timer never charge.
- **macOS menu bar** — the percent left lives in the system status bar; clicking it drops a usage card with today's per-agent breakdown, Refresh, Open web dashboard, language, and Quit.

```text
python3 -m venv ~/.headroom/venv
~/.headroom/venv/bin/pip install -r requirements-desktop.txt
python3 hooks/install_macos_app.py          # builds ~/Applications/headroom.app and launches it
python3 hooks/install_macos_app.py --uninstall
```

**Install it as a `.app`, do not run `scripts/headroom_menubar.py` directly.** A bare interpreter process registers a status item with LaunchServices but on macOS 26 the item frequently never renders — the process is registered without a bundle identity. Wrapping the same code in a minimal bundle (`LSUIElement=true`, `NSAppSleepDisabled=true`) gives the menu bar a real application to attach to. The bundle is a thin shell wrapper around the Python source, so edits take effect on the next launch.

Diagnostics go to `~/.headroom/logs/menubar.log` (`--log`, or `HEADROOM_MENUBAR_LOG`). Check it first if the icon is missing: it records startup, the resolved ledger path, `statusItem.isVisible()`, the status item's geometry, and every refresh.

### When the icon never appears

Two independent causes, and the first one is silent — no error anywhere.

**1. The interpreter is not inside the bundle.** `NSBundle.mainBundle()` resolves from the *main executable's* path. If the launcher is a shell script that `exec`s an interpreter living outside the bundle, the process ends up with `bundleIdentifier() == None` and macOS never assigns it a menu bar slot. `install_macos_app.py` avoids this by copying the interpreter into `Contents/MacOS/python` and pointing the launcher at that copy. Verify with:

```text
lsappinfo list | grep -A3 '"headroom"'
```

`executable path=` must point inside `headroom.app/Contents/MacOS/`. If it points at a venv or system interpreter, that is the bug.

**2. A menu bar manager is hiding it, or the strip is full.** Managers are the far more common cause. Thaw/Ice keep a **hidden section** and implement it by moving items off-screen with the Accessibility API — every new item lands there by default, and the manager will show it under a generic name until it learns the app's label. If a manager is running, look for the item in its hidden section first and fix it there, not in headroom. Managers generally expose no CLI for this; the user has to drag the item in the manager's own panel.

Only after ruling that out, consider capacity. `NSScreen.auxiliaryTopRightArea()` is the strip status items share — about 664 points on a notched 14" MacBook. Free a slot and the item appears; Command-drag it afterwards (`autosaveName` remembers where).

**The only reliable way to see what is actually on the menu bar** is `Quartz.CGWindowListCopyWindowInfo` filtered to `kCGWindowLayer == 25`, checking whether `kCGWindowBounds.X` falls inside the screen. It needs `pyobjc-framework-Quartz` but no special permission. On macOS 26 every status item is hosted by Control Center, so `kCGWindowOwnerName` is always "控制中心" — identify your own item by measuring the delta across a start/stop, not by name.

**Do not trust `NSStatusItem` geometry.** On macOS 26 `NSSceneStatusItem` uses virtual coordinates: the same item reports `height=0` and `height=33` on different runs, `x` anywhere from `-4798` to `350`, and `isVisible()` is always `True`. And do **not** try to self-heal by toggling `isVisible` or rebuilding the item on a schedule — because the probe never reads as "placed", the retry fires forever and keeps the item from settling, making things worse.

The hook finds the venv automatically (`HEADROOM_DESKTOP_PYTHON` overrides it). On Windows the equivalent is the taskbar tray card, `pythonw scripts/headroom_desktop.py`. On macOS, add `headroom.app` to System Settings → General → Login Items to start it at login.

`HEADROOM_DISPLAY=desktop|web|both|off` chooses what a `SessionStart` launches; the default is `desktop` on Windows and macOS, `web` elsewhere. All displays read the same ledger and never charge.

Because the display re-reads local history on every refresh, **no hook is required to see the cap, the per-agent breakdown, or the spend**. Hooks only upgrade the spend from a message count to a scored value.

## Charging turns

`python hooks/install_hooks.py` previews the merge into each agent's config; `--apply` writes it. It supports `codex` (`$CODEX_HOME/hooks.json`), `claude` (`$CLAUDE_CONFIG_DIR/settings.json`), and `gemini` (`$GEMINI_DIR/settings.json`), preserves unrelated hook entries, backs up every modified file, and refuses to rewrite an unparseable config. Review and trust the new definitions in each agent before they take effect.

The hook normalizes each agent's payload — `prompt`/`user_prompt`, `session_id`/`sessionId`, `turn_id`/`promptId` — and sets `HEADROOM_AGENT` explicitly so the ledger attributes the debit correctly. Codex supplies a `turn_id`; Claude Code and Gemini do not, so headroom allocates a durable per-session sequence from the ledger's `hook_turns` table. Two different prompts in one session charge twice; a redelivered hook for the same turn charges once.

Without an installed hook, charge manually from this skill directory:

```text
python scripts/headroom.py turn --event-id <opaque-id> --origin manual-user --mode normal --backend mock
```

Pipe the UTF-8 user message on stdin. Never derive the event ID from message content. **Do not also run `turn` for a hook-covered turn**, or it may get a second event ID and debit twice.

Only direct human turns in normal interactive mode should charge. Skip plan mode, Goal/automation mode, scheduled work, subagents, continuations, unknown provenance, and scorer errors. The hook rejects known non-interactive sources, but current payloads do not independently certify human origin — this is best-effort amusement, not an audit-grade exclusion.

## Adding an agent without touching code

Declare it in `$HEADROOM_AGENTS_CONFIG` (default `~/.headroom/agents.json`):

```json
{"agents": [
  {"name": "aider", "label": "Aider", "kind": "jsonl", "root": "~/.aider",
   "glob": "**/*.history", "where": {"role": "user"},
   "day_field": "timestamp", "day_format": "ms"}
]}
```

`kind` is `jsonl` or `sqlite`; `day_format` is `ms`, `iso`, or `epoch`. A declared agent overrides a built-in of the same name.

## Overriding a root

`--agent-home NAME=PATH` (repeatable) overrides one adapter's root. `--codex-home PATH` is kept for compatibility and is equivalent to `--agent-home codex=PATH`. `--agents name,name` restricts the selection; naming an unreadable agent reports it instead of silently dropping it.

## Privacy

Local scoring, local ledger, no headroom cloud account. History queries select only item type and timestamp metadata — never prompt bodies. Adapters open agent databases read-only. The tray/desktop display reads local metadata and the ledger; the dashboard binds to `127.0.0.1`. A file untouched since before the seven-day window is skipped without being read, which is what keeps a 159 MB history directory from being rescanned on every refresh.

Run the regression tests:

```text
python -m unittest discover -s skills/headroom/tests -p "test_*.py" -v
node skills/headroom/tests/test_dashboard_mood.js
```

## Installing the hooks

On Windows, install the user hook with `powershell -ExecutionPolicy Bypass -File hooks/install_windows.ps1` from this skill directory. Existing `hooks.json` is never overwritten without `-Force`; merge manually if it already exists. On macOS/Linux, run `sh hooks/install.sh` instead: it merges the two hooks into an existing `hooks.json` (backing it up), uses `python3` rather than PowerShell, and `--uninstall` removes only headroom's entries.

The installer currently wires up **Codex** only. The other agents are read-only: they contribute to the cap and the per-agent breakdown, but their turns are not charged. Their hooks can be added by hand — the hook normalizes each agent's payload (`prompt`/`user_prompt`, `session_id`/`sessionId`, `turn_id`/`promptId`) and reads `HEADROOM_AGENT` when it is set, so a hook definition that exports that variable works without further changes.

Review and trust the two definitions in Codex `/hooks`, then start a new session. Plugin installation or implicit skill selection alone does not activate lifecycle hooks. `SessionStart` defaults to the tray display on Windows and macOS, and the loopback dashboard elsewhere. `HEADROOM_DISPLAY=web|desktop|both|off` selects the display; `off` does not disable charging. Quitting the display leaves charging enabled; it can be manually started or reopened by the next session. No login startup is installed. `UserPromptSubmit` scores asynchronously and never blocks a response. Use temporary ledgers for tests and set `HEADROOM_DISABLE_DASHBOARD=1` to suppress all lifecycle displays.
