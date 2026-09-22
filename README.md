# headroom

> I need a reset.

`headroom` is a small, local-only Codex plugin/skill that turns each eligible interactive turn into a playful 0-10 brain-load debit. It is entertainment, not a measurement of intelligence, health, or productivity.

## What it does

- Derives the daily cap from the busiest of the previous seven complete Asia/Shanghai days: `max(userMessage count) * 5`.
- Shows the remaining balance as a percentage.
- Uses a deterministic mock scorer by default, or an explicitly configured loopback Laya scorer.
- Stores only an opaque event ID, date, numeric score, and provider in SQLite. Prompt text is passed to the local scorer but is never written to the ledger.
- Skips Goal/plan mode, scheduled/background work, subagents, continuations, unknown provenance, and scorer failures.
- Uses an idempotent session/turn identifier so retries do not debit twice.

## Local install

Copy this folder into a Codex plugin source directory, or install it through a local marketplace. Install the optional user hooks across platforms:

```bash
# macOS / Linux / Windows
python skills/headroom/hooks/install_hooks.py
```

On Windows, you can also run:

```powershell
powershell -ExecutionPolicy Bypass -File skills/headroom/hooks/install_windows.ps1
```

Review and trust the hook definitions in Codex `/hooks`, then start a new session. `SessionStart` starts the loopback dashboard; `UserPromptSubmit` charges asynchronously and never blocks the response.

The terminal commands are:

```text
python skills/headroom/scripts/headroom.py status
python skills/headroom/scripts/headroom_dashboard.py
```

Open `http://127.0.0.1:8766/` to view the dashboard. Set `HEADROOM_STATE_PATH` to share one ledger across workspaces. The default is `$CODEX_HOME/headroom/ledger.sqlite3`.

## Tests

```text
python skills/headroom/tests/test_headroom.py
```

The repository intentionally contains no Codex history database, ledger, prompt transcript, personal path, API key, or service credential.
