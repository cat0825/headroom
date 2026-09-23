<div align="center">

# headroom

**I need a reset.**

Your AI has a usage limit. So do you.

**English** · [简体中文](README.zh-CN.md)

A local-first **Codex plugin and skill** that gives your brain a daily budget.<br>
Send a message. Spend a few imaginary brain points.

[Quick start](#quick-start) · [Taskbar tray](#taskbar-tray-windows) · [English UI](#dashboard-language) · [Jev & Laya](#jev-laya-and-mock-scoring) · [Privacy](#privacy)

</div>

| 100%–70% left | Below 70%–30% left | Below 30% left |
| :---: | :---: | :---: |
| <img src="skills/headroom/assets/brain-full.png" width="110" alt="Dog wearing headphones"> | <img src="skills/headroom/assets/brain-declining.webp" width="110" alt="Crying cat"> | <img src="skills/headroom/assets/brain-low.png" width="110" alt="Barking dog"> |
| One more prompt. | That was not a quick question. | I need a reset. |

**For fun, not science.** This is a playful brain-load / cognitive-load estimate, not a measurement of intelligence, health, fatigue, or productivity. Zero percent never stops you from chatting.

## What happens when you chat?

- **A daily limit based on you.** Take the busiest day in the previous seven complete days, count its user messages, and multiply by two. No history within that window? Start with two points.
- **A tiny debit per eligible prompt.** Each direct interactive turn gets a 0–10 score. Retries of the same session/turn ID only charge once.
- **One balance across sessions.** A shared local ledger powers the percentage-left meter, progress bar, and meme mood. It also shows `Used 12.50 / 334 points`, without a separate remaining-points number.
- **Refresh without spending.** The dashboard refreshes every 10 seconds. Refreshing or switching its language never charges. A new chat is not required for each debit.
- **Keep it in your taskbar.** A small H tray icon opens a usage card on click. No browser tab or floating ball needed; it reads the local ledger directly.

The day boundary is **Asia/Shanghai (UTC+8)**. The seven-day cap counts **all** recorded `userMessage` items, including automated ones; eligibility filtering applies to debits, not the cap.

## Quick start

In the web dashboard, click the mood image to make it hop and play one local dog clip with sound. The first click plays Hey Dog. Consecutive image clicks less than 3.5 seconds apart randomly select one of the other eight clips (dog barks 1–5, Call, Dadada, or Industry Baby), without immediately repeating the current clip. A gap of 3.5 seconds or more restarts at Hey Dog. Dadada and Industry Baby play audio only while the current mood image bounces, rotates and scales to the live audio energy and beat peaks; reduced-motion settings disable these effects; the other seven clips show video. Playback ends on the current mood image without automatically starting another clip; clicking during playback stops it and applies the same click-interval rule. Press Esc while the image button is focused to stop. Refreshing the balance does not interrupt playback, and playing clips never spends points. Use the small speaker icon in the bottom-right corner of the card to change sound immediately without restarting playback. The browser remembers this setting; muting does not stop the audio-reactive animation.

The current automatic setup targets **Codex on Windows with PowerShell**. You need Python 3.10+ and a local Codex history index (`thread_history_1.sqlite`). Mock scoring and the dashboard use only Python's standard library.

```powershell
git clone https://github.com/llm-learner/headroom.git
cd headroom
python skills/headroom/scripts/headroom.py status
python -m pip install -r skills/headroom/requirements-desktop.txt
pythonw skills/headroom/scripts/headroom_desktop.py --lang en
```

A small H icon appears in the Windows taskbar notification area, possibly under the hidden-icons arrow. Click it to open the usage card, or hover for percent left. This first step is read-only; it does not enable automatic debits. Use `python` instead of `pythonw` if you need to see startup errors in a terminal.

### Enable automatic debits

From the repository root, review and run:

```powershell
powershell -ExecutionPolicy Bypass -File skills/headroom/hooks/install_windows.ps1
```

Review and trust headroom's **SessionStart** and **UserPromptSubmit** definitions in Codex `/hooks`. After the initial hook setup, start a new session to verify activation. Subsequent messages in that session can charge normally; you do **not** need to keep making new sessions.

`SessionStart` launches the tray display on Windows (the web dashboard on other platforms). `UserPromptSubmit` scores after submission, asynchronously—not after the assistant finishes. Allow scoring to finish and the display to refresh. The installer refuses to overwrite an existing `hooks.json`; merge the two definitions with your other hooks instead of blindly forcing an overwrite.

To expose `$headroom` as a Codex skill, install the repository through your local plugin marketplace, or copy `skills/headroom` into your Codex skills directory. Installing a skill/plugin alone does not activate or trust lifecycle hooks. Keep the hook's source directory in place.

<a id="desktop-orb-windows"></a>

## Taskbar tray (Windows)

- **Click** the H tray icon to expand/collapse. The `−` button or Escape also collapses the card. Hover over the icon to see percent left.
- The card opens near the tray, inside the monitor's work area. Windows may initially place the icon under **Show hidden icons**; drag it onto the visible tray if desired.
- **Right-click → Exit headroom** closes the display, not scoring. A later `SessionStart` can open it again.
- The card shows the exact **percent left**, spent/cap points, the current meme, and a Refresh button.
- Use the card's **EN / ZH** button to switch languages live. Launch options: `--lang en` or `--lang zh`. Priority: CLI → `HEADROOM_LANG` → saved language → Chinese. Exit the existing display before changing startup options.
- One display per shared ledger and Windows login session—even with multiple Codex sessions. Refresh runs every ten seconds, off the UI thread, and never calls a scorer.

Tray mode needs Python with **Tk**, **pystray**, and **Pillow** (see `requirements-desktop.txt`). It does not need the web dashboard or any listening port. Only orb position/language are saved in `desktop.json` beside the ledger. It starts with a trusted Codex `SessionStart`, **not at Windows login**.

Prefer the old draggable orb? Exit the tray display and launch with `--mode orb`. `--mode tray` is the default; `HEADROOM_DESKTOP_MODE=tray|orb` sets the startup default. Orb mode works without pystray; Pillow is optional there, with text faces for unsupported meme formats.

To choose what future sessions launch, set `HEADROOM_DISPLAY` in the hook's environment to `desktop` (Windows default), `web`, `both`, or `off`. This changes display startup only, not scoring. The browser UI remains available manually:

```text
python skills/headroom/scripts/headroom_dashboard.py --lang en
```

Open [the English web dashboard](http://127.0.0.1:8766/?lang=en). If it is already running, open the URL instead of starting another server on the same port.

## Dashboard language

| Option | English | Chinese |
| --- | --- | --- |
| Switch the running page immediately | [`?lang=en`](http://127.0.0.1:8766/?lang=en) | [`?lang=zh`](http://127.0.0.1:8766/?lang=zh) |
| Default when starting the server | `--lang en` | `--lang zh` |
| Default from the server environment | `HEADROOM_LANG=en` | `HEADROOM_LANG=zh` |

Priority: **URL parameter → CLI parameter → environment → Chinese**. Unknown URL values fall back to the server default. English covers labels, loading/error states, time formatting, and image descriptions. Language only affects presentation, not scoring, the ledger, or the UTC+8 reset time. To change an already-running dashboard, use the URL parameter; startup options take effect on the next server start.

## Jev, Laya, and mock scoring

| Backend | Status | Where the prompt goes |
| --- | --- | --- |
| **Mock** (`mock`) | Included; default, deterministic fake score | In-process on your machine; no network |
| **Laya** (`laya`) | Included adapter for a separately deployed local Laya service | Fixed loopback endpoint: `http://127.0.0.1:8765/predict` |
| **Jev API** | **Not implemented**; a future integration direction | No Jev/cloud request exists in this version |

The original prototype imagined a Jev-style scoring API, so the mock provider is named `jev-mock-local`. **It is not an actual Jev call**, and headroom is not an official Jev or Laya product.

### Use your local Laya deployment

Run a compatible Laya service separately, then put this in `$CODEX_HOME/headroom/config.json` (default: `~/.codex/headroom/config.json`):

```json
{"backend": "laya"}
```

The hook rereads this file on **every turn**, including existing sessions. Set `mock` to switch back. `HEADROOM_BACKEND` overrides this file when set. Invalid configuration or an unavailable Laya service **skips charging**, with no silent fallback to fake scores. This config does not launch/download a model or change the CLI's explicit `--backend` option.

The adapter posts JSON with the current prompt in `state.body` and a `questions.brain_load` scoring request. It expects a finite **0–10** number at `answers.brain_load.score`, with `type: "score"`. Model weights and the model server are **not bundled**. The client bypasses system/HTTP proxies for this loopback request.

## Privacy

**Local scoring. Local ledger. No headroom cloud account.** The shipped backends add no cloud scoring calls or telemetry.

| Data | What headroom does |
| --- | --- |
| Past Codex conversations | Reads message-type/timestamp metadata to count turns, not historical prompt bodies |
| Current eligible prompt | Passes it in memory to mock scoring or the configured local Laya service |
| Debit ledger | Stores an opaque event ID, local date, numeric score, and provider—not prompt text |
| Hook diagnostics | Stores only the latest outcome, time, input-presence/length metadata, and scoring metadata—not prompt text or raw session/turn IDs |
| Tray / desktop display | Reads local history metadata and the ledger directly; saves only orb position/language, and makes no network requests |
| Dashboard | Binds to `127.0.0.1`; images are bundled locally, with no CDN or analytics |

The default shared ledger is `$CODEX_HOME/headroom/ledger.sqlite3` (otherwise `~/.codex/headroom/ledger.sqlite3`). `HEADROOM_STATE_PATH` can override it. Do not publish your runtime state, diagnostics, Codex history, or credentials.

These guarantees describe **headroom**, not Codex's own data processing. A separately deployed model server may have its own logs or network behavior; configure it accordingly. Any future cloud Jev integration would need explicit opt-in and a separate disclosure of what leaves the device.

## Which turns count?

The intent is to charge direct human conversation, not unattended work. Known non-interactive sources and plan-mode hooks are rejected. The manual scoring CLI also rejects non-normal modes and unconfirmed origins.

**Automatic provenance filtering is best-effort.** Current hook payloads do not always identify Goal/automation/subagent provenance reliably. Missing source metadata is accepted for compatibility; `HEADROOM_HOOK_STRICT=1` rejects it, but may also skip ordinary conversation. Do not treat these exclusions as audit-grade guarantees.

<details>
<summary>Troubleshooting and development</summary>

- No debit? Check `/hooks` trust, the selected backend, and local Laya availability. `last-hook.json` beside the ledger records the latest outcome. Set `HEADROOM_DEBUG_PATH` to another path, or an empty string to disable it.
- On Windows, a quoted executable in a PowerShell hook needs `&`. Hook input is decoded as UTF-8, including Chinese and emoji on GBK systems.
- Changing the hook command requires a new review of that definition. Do not run manual `turn` scoring for a turn already covered by the hook—it can create a second event ID.
- Changing language, opening the dashboard, and its Refresh button are read-only. Sending a chat message asking for your balance is still a user prompt and may be charged by the hook.

Run the regression tests with temporary histories and ledgers:

```text
python -m unittest discover -s skills/headroom/tests -p "test_*.py" -v
node skills/headroom/tests/test_dashboard_mood.js
```

The JavaScript test needs Node.js and Python (`HEADROOM_TEST_PYTHON` can select the Python executable). Lifecycle smoke tests must set `HEADROOM_DISABLE_DASHBOARD=1` when using a temporary ledger. HTTP tests use ephemeral loopback ports, never the live dashboard port.

</details>

## Contributors

- [TOGET-H](https://github.com/TOGET-H)
- [cat0825](https://github.com/cat0825)
- [fuxiuht](https://github.com/fuxiuht)
