"""Multi-agent lifecycle hook for headroom.

Codex, Claude Code, Gemini, and WorkBuddy all emit a lifecycle hook payload,
but with different field names and different levels of detail. This module
normalizes them into one canonical event, then delegates scoring to
``headroom.py``.

The hook stores no prompt text. It passes prompt text only to the configured
local scorer and records an opaque event id for idempotence.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


#: Field aliases per canonical name. Order matters: first match wins.
FIELD_ALIASES = {
    "prompt": ("prompt", "user_prompt", "userPrompt", "message", "text"),
    "session_id": ("session_id", "sessionId", "conversation_id", "conversationId"),
    "turn_id": ("turn_id", "turnId", "prompt_id", "promptId", "message_id", "messageId"),
    "cwd": ("cwd", "working_directory", "workingDirectory"),
    "permission_mode": ("permission_mode", "permissionMode"),
    "mode": ("mode",),
    "source": ("source", "origin"),
    "transcript": ("transcript_path", "transcriptPath"),
}
#: Sources that identify a direct human turn.
HUMAN_SOURCES = {"interactive", "user", "manual-user"}
#: Non-interactive modes that must never be charged.
NON_INTERACTIVE_MODES = {"goal", "task-automation"}
#: Transcript path markers, used only when HEADROOM_AGENT is not set.
TRANSCRIPT_MARKERS = (
    (".claude/", "claude"),
    (".codebuddy/", "workbuddy"),
    (".workbuddy-ai/", "workbuddy"),
    (".gemini/", "antigravity"),
    (".codex/", "codex"),
)
ENV_MARKERS = (
    ("CLAUDE_CONFIG_DIR", "claude"),
    ("GEMINI_DIR", "antigravity"),
    ("WORKBUDDY_HOME", "workbuddy"),
    ("CODEX_HOME", "codex"),
)

AMBIENT_BROWSER_CONTEXT = re.compile(
    r'\A<in-app-browser-context source="ambient-ui-state">\r?\n'
    r'.*?\r?\n</in-app-browser-context>\r?\n\r?\n## My request:\r?\n',
    re.DOTALL,
)


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


def shared_headroom():
    """The scorer module, for shared path defaults. Never breaks the hook."""
    try:
        scripts = str(scorer_path().parent)
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import headroom  # noqa: PLC0415
        return headroom
    except Exception:
        return None


def state_path(_cwd: str | None) -> Path:
    """One shared ledger across every agent."""
    configured = os.environ.get("HEADROOM_STATE_PATH")
    if configured:
        return Path(configured)
    module = shared_headroom()
    if module is not None:
        return module.default_state_path()
    return codex_home() / "headroom" / "ledger.sqlite3"


def first_string(event: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def scorable_prompt(event: dict) -> str | None:
    """Remove Codex browser state that accompanies, but is not, a user request."""
    prompt = first_string(event, FIELD_ALIASES["prompt"])
    if prompt is None:
        return None
    wrapper = AMBIENT_BROWSER_CONTEXT.match(prompt)
    return prompt[wrapper.end():] if wrapper else prompt


def detect_agent(event: dict) -> str:
    """Which agent fired this hook.

    Hook installers set HEADROOM_AGENT explicitly; the rest is a safety net for
    a hand-written hook definition.
    """
    configured = os.environ.get("HEADROOM_AGENT")
    if configured:
        return configured
    if first_string(event, FIELD_ALIASES["turn_id"]):
        return "codex"
    transcript = first_string(event, FIELD_ALIASES["transcript"])
    if transcript:
        normalized = transcript.replace("\\", "/")
        for marker, name in TRANSCRIPT_MARKERS:
            if marker in normalized:
                return name
    for variable, name in ENV_MARKERS:
        if os.environ.get(variable):
            return name
    return "unknown"


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
    prompt = scorable_prompt(event)
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "outcome": outcome,
        "agent": detect_agent(event),
        "has_prompt": prompt is not None,
        "prompt_length": len(prompt) if prompt else 0,
        "has_session_id": first_string(event, FIELD_ALIASES["session_id"]) is not None,
        "has_turn_id": first_string(event, FIELD_ALIASES["turn_id"]) is not None,
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


def spawn_detached(command: list[str], cwd: str | None) -> None:
    """Start a display that outlives this short-lived hook process.

    Windows hides the console window. On macOS/Linux a new session keeps the
    display out of Codex's process group and terminal (no SIGHUP/SIGINT).
    """
    options = ({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
               if sys.platform == "win32" else {"start_new_session": True})
    subprocess.Popen(command, cwd=cwd or None, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, **options)


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
    spawn_detached(command, cwd)


def start_display(cwd: str | None) -> None:
    # Preserve the existing all-display suppression used by lifecycle tests.
    if os.environ.get("HEADROOM_DISABLE_DASHBOARD") == "1":
        return
    mode = os.environ.get("HEADROOM_DISPLAY", "desktop" if sys.platform == "win32" else "web")
    if mode not in {"desktop", "web", "both", "off"}:
        raise ValueError("HEADROOM_DISPLAY must be desktop, web, both, or off")
    if mode in {"web", "both"}:
        start_dashboard(cwd)
    if mode in {"desktop", "both"} and os.environ.get("HEADROOM_DISABLE_DESKTOP") != "1":
        command = [python_background(), str(scorer_path().with_name("headroom_desktop.py")),
                   "--codex-home", str(codex_home()), "--state-path", str(state_path(cwd))]
        # The desktop process owns a per-ledger lock (a Windows named mutex,
        # or flock elsewhere), so concurrent SessionStart events cannot leave
        # multiple displays on the desktop.
        spawn_detached(command, cwd)


def is_chargeable(event: dict) -> bool:
    prompt = scorable_prompt(event)
    if not prompt or not prompt.strip():
        return False
    if first_string(event, FIELD_ALIASES["session_id"]) is None:
        return False
    # A turn identifier is optional: several agents have no such concept and get
    # a per-session sequence instead. A *present but blank* one is malformed.
    for key in FIELD_ALIASES["turn_id"]:
        if key in event:
            value = event[key]
            if not isinstance(value, str) or not value:
                return False
            break
    if first_string(event, FIELD_ALIASES["permission_mode"]) == "plan":
        return False
    if first_string(event, FIELD_ALIASES["mode"]) in NON_INTERACTIVE_MODES:
        return False
    # A future payload may expose source/origin. If it does, fail closed for
    # known non-interactive sources; absent source is accepted for compatibility.
    source = first_string(event, FIELD_ALIASES["source"])
    if source is not None and source not in HUMAN_SOURCES:
        return False
    if os.environ.get("HEADROOM_HOOK_STRICT") == "1" and source is None:
        return False
    return True


def turn_identity(agent: str, event: dict) -> list[str]:
    """The scorer arguments that make this turn idempotent.

    Prefer the agent's own turn id. Without one, hand headroom an opaque session
    key and let it allocate a durable per-session sequence.
    """
    session_id = first_string(event, FIELD_ALIASES["session_id"]) or ""
    turn_id = first_string(event, FIELD_ALIASES["turn_id"])
    if turn_id:
        digest = hashlib.sha256(f"{agent}:{session_id}:{turn_id}".encode("utf-8")).hexdigest()
        return ["--event-id", f"hook-{digest[:40]}"]
    key = hashlib.sha256(f"{agent}:{session_id}".encode("utf-8")).hexdigest()[:40]
    return ["--session-key", key]


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
    agent = detect_agent(event)
    prompt = scorable_prompt(event) or ""
    # Keep standard input available; CREATE_NO_WINDOW hides console windows.
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    command = [str(executable), str(scorer_path()), "--codex-home", str(codex_home()),
               "--state-path", str(state_path(event.get("cwd"))), "turn",
               *turn_identity(agent, event), "--origin", "manual-user",
               "--mode", "normal", "--backend", backend, "--agent", agent]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(command, input=prompt.encode("utf-8"),
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
            start_display(event.get("cwd"))
            debug_hook_event(event, False, "display_ready_or_starting")
        except (OSError, ValueError):
            debug_hook_event(event, False, "display_failed")
    elif "--user-prompt" in sys.argv:
        charge(event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
