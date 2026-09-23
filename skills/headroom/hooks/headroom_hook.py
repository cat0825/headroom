"""Codex lifecycle hook for headroom.

The hook stores no prompt text. It passes prompt text only to the configured
local scorer and records an opaque hash of session_id + turn_id for idempotence.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


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


def scoring_backend() -> str:
    """Read local preferences on each turn so existing sessions can switch."""
    configured = os.environ.get("HEADROOM_BACKEND")
    if configured is None:
        path = codex_home() / "headroom" / "config.json"
        try:
            config = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            config = {}
        if not isinstance(config, dict):
            raise ValueError("Headroom config must be an object")
        configured = config.get("backend", "mock")
    if not isinstance(configured, str) or configured not in {"mock", "laya"}:
        raise ValueError("Headroom backend must be mock or laya")
    return configured


def debug_hook_event(event: dict, chargeable: bool, outcome: str = "received",
                     **details) -> None:
    """Keep only the latest prompt-free diagnostic, never text or identifiers."""
    target = os.environ.get(
        "HEADROOM_DEBUG_PATH", str(state_path(None).with_name("last-hook.json"))
    )
    if not target:
        return
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "outcome": outcome,
        "has_prompt": isinstance(event.get("prompt"), str),
        "prompt_length": len(event["prompt"]) if isinstance(event.get("prompt"), str) else 0,
        "has_session_id": isinstance(event.get("session_id"), str) and bool(event["session_id"]),
        "has_turn_id": isinstance(event.get("turn_id"), str) and bool(event["turn_id"]),
        "chargeable": chargeable,
        **{key: value for key, value in details.items()
           if key in {"returncode", "charged_points", "backend", "provider"}},
    }
    temp_path = None
    try:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".headroom-", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=True)
        os.replace(temp_path, path)
    except OSError:
        return
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError:
                pass


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
    # Lifecycle smoke tests must not leave a production-port server pointing
    # at a temporary ledger that disappears when the test finishes.
    if os.environ.get("HEADROOM_DISABLE_DASHBOARD") == "1":
        return
    port = int(os.environ.get("HEADROOM_DASHBOARD_PORT", "8766"))
    if dashboard_is_up(port):
        return
    dashboard = plugin_root() / "scripts" / "headroom_dashboard.py"
    command = [python_background(), str(dashboard), "--port", str(port),
               "--codex-home", str(codex_home()),
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
    if source is not None and (not isinstance(source, str) or
                              source not in {"interactive", "user", "manual-user"}):
        return False
    if os.environ.get("HEADROOM_HOOK_STRICT") == "1" and source is None:
        return False
    return True


def charge(event: dict) -> None:
    chargeable = is_chargeable(event)
    debug_hook_event(event, chargeable)
    if not chargeable:
        debug_hook_event(event, False, "ineligible")
        return
    try:
        backend = scoring_backend()
    except (OSError, UnicodeError, ValueError, TypeError):
        debug_hook_event(event, True, "invalid_backend_config")
        return
    digest = hashlib.sha256(
        f"{event['session_id']}:{event['turn_id']}".encode("utf-8")
    ).hexdigest()[:40]
    # Keep standard input available; CREATE_NO_WINDOW hides console windows.
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    command = [str(executable), str(scorer_path()), "--codex-home", str(codex_home()),
               "--state-path", str(state_path(event.get("cwd"))), "turn",
               "--event-id", f"hook-{digest}", "--origin", "manual-user",
               "--mode", "normal", "--backend",
               backend]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(command, input=event["prompt"].encode("utf-8"),
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=25, check=False, creationflags=creationflags)
        if result.returncode:
            debug_hook_event(event, True, "scorer_process_failed", returncode=result.returncode)
            return
        output = json.loads(result.stdout.decode("utf-8"))
        action = output.get("action")
        if action not in {"charged", "duplicate", "skipped"}:
            action = "invalid_scorer_result"
        debug_hook_event(event, True, action, charged_points=output.get("charged_points", 0),
                         backend=backend, provider=output.get("provider"))
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        # Fail open: a missing scorer or local model must never block a turn.
        debug_hook_event(event, True, "scorer_failed")
        return


def main() -> int:
    debug_hook_event({}, False, "process_started")
    try:
        # Codex sends UTF-8 JSON regardless of the Windows ANSI code page.
        # json.load(sys.stdin) decodes it as GBK on some Windows installs.
        event = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except (ValueError, UnicodeError, AttributeError, OSError):
        debug_hook_event({}, False, "invalid_input")
        return 0
    if not isinstance(event, dict):
        debug_hook_event({}, False, "invalid_input")
        return 0
    if "--session-start" in sys.argv:
        try:
            start_dashboard(event.get("cwd"))
            debug_hook_event(event, False, "dashboard_ready_or_starting")
        except (OSError, ValueError):
            debug_hook_event(event, False, "dashboard_failed")
    elif "--user-prompt" in sys.argv:
        charge(event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
