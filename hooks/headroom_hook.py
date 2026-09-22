"""Codex lifecycle hook for headroom.

The hook stores no prompt text. It passes prompt text only to the loopback
Laya scorer and records an opaque hash of session_id + turn_id for idempotence.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen


def plugin_root() -> Path:
    configured = os.environ.get("HEADROOM_PLUGIN_ROOT")
    return Path(configured).resolve() if configured else Path(__file__).resolve().parents[1]


def python_background() -> str:
    configured = os.environ.get("HEADROOM_PYTHONW")
    if configured:
        return configured
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return str(pythonw if pythonw.is_file() else sys.executable)


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))


def state_path(_cwd: str | None) -> Path:
    configured = os.environ.get("HEADROOM_STATE_PATH")
    if configured:
        return Path(configured)
    return codex_home() / "headroom" / "ledger.sqlite3"


def scorer_path() -> Path:
    root = plugin_root()
    installed = root / "skills" / "headroom" / "scripts" / "headroom.py"
    return installed if installed.is_file() else root / "scripts" / "headroom.py"


def dashboard_is_up(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def start_dashboard(cwd: str | None) -> None:
    port = int(os.environ.get("HEADROOM_DASHBOARD_PORT", "8766"))
    if dashboard_is_up(port):
        return
    dashboard = plugin_root() / "scripts" / "headroom_dashboard.py"
    command = [python_background(), str(dashboard), "--port", str(port),
               "--state-path", str(state_path(cwd))]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(command, cwd=cwd or None, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=creationflags, close_fds=True)


def is_chargeable(event: dict) -> bool:
    if not isinstance(event.get("prompt"), str) or not event["prompt"].strip():
        return False
    if not isinstance(event.get("session_id"), str) or not event["session_id"]:
        return False
    if not isinstance(event.get("turn_id"), str) or not event["turn_id"]:
        return False
    if event.get("permission_mode") == "plan":
        return False
    # A future Codex event may expose source/origin. If it does, fail closed
    # for known non-interactive sources; absent source is treated as the main
    # UserPromptSubmit event for compatibility with current releases.
    source = event.get("source", event.get("origin"))
    if source is not None and source not in {"interactive", "user", "manual-user"}:
        return False
    if os.environ.get("HEADROOM_HOOK_STRICT") == "1" and source is None:
        return False
    return True


def charge(event: dict) -> None:
    if not is_chargeable(event):
        return
    digest = hashlib.sha256(
        f"{event['session_id']}:{event['turn_id']}".encode("utf-8")
    ).hexdigest()[:40]
    command = [python_background(), str(scorer_path()), "--codex-home", str(codex_home()),
               "--state-path", str(state_path(event.get("cwd"))), "turn",
               "--event-id", f"hook-{digest}", "--origin", "manual-user",
               "--mode", "normal", "--backend",
               os.environ.get("HEADROOM_BACKEND", "laya")]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run(command, input=event["prompt"].encode("utf-8"),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=25, check=False, creationflags=creationflags)
    except (OSError, subprocess.TimeoutExpired):
        # Fail open: a missing scorer or local model must never block a turn.
        return


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeError):
        return 0
    if not isinstance(event, dict):
        return 0
    if "--session-start" in sys.argv:
        start_dashboard(event.get("cwd"))
    elif "--user-prompt" in sys.argv:
        charge(event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
