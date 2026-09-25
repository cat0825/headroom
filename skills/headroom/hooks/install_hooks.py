"""Install headroom's lifecycle hooks into every supported agent's config.

Preview is the default; nothing is written without ``--apply``.

```text
python hooks/install_hooks.py                 # show what would change
python hooks/install_hooks.py --apply         # merge into every detected agent
python hooks/install_hooks.py --apply --agents claude,gemini
```

Existing hook entries are preserved. A headroom entry is identified by
``headroom_hook.py`` appearing in its command, so re-running updates in place
instead of stacking duplicates. Every modified file is backed up first.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HOOK_SCRIPT = PLUGIN_ROOT / "hooks" / "headroom_hook.py"
MARKER = "headroom_hook.py"

#: Inner hook shape per agent: which optional keys the schema accepts.
SHAPES = {
    "codex": {"matcher": True, "windows": True, "async": True, "status": True,
              "description": True},
    "claude": {"matcher": True, "windows": False, "async": False, "status": False,
               "description": False},
    "gemini": {"matcher": False, "windows": False, "async": False, "status": False,
               "description": False},
}

#: Event -> (matcher, timeout seconds). None matcher means the key is omitted.
EVENTS = {
    "codex": {"SessionStart": ("startup|resume|clear|compact", 3),
              "UserPromptSubmit": (None, 30)},
    "claude": {"SessionStart": ("startup|resume|clear|compact", 3),
               "UserPromptSubmit": (None, 30)},
    "gemini": {"SessionStart": (None, 3), "BeforeAgent": (None, 30)},
}
#: Gemini's SessionStart has no matcher; this keeps the two lists aligned.
START_EVENT = {"codex": "SessionStart", "claude": "SessionStart", "gemini": "SessionStart"}
PROMPT_EVENT = {"codex": "UserPromptSubmit", "claude": "UserPromptSubmit",
                "gemini": "BeforeAgent"}
#: Event used for the flag passed to the hook script.
START_FLAG = "--session-start"
PROMPT_FLAG = "--user-prompt"


def config_path(agent: str) -> Path:
    if agent == "codex":
        return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "hooks.json"
    if agent == "claude":
        return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "settings.json"
    if agent == "gemini":
        return Path(os.environ.get("GEMINI_DIR") or Path.home() / ".gemini") / "settings.json"
    raise ValueError(f"Unsupported agent: {agent}")


def python_command() -> str:
    """A POSIX-safe absolute interpreter path, quoted."""
    executable = shutil.which("python3") or shutil.which("python") or sys.executable
    return f'"{executable}"'


def hook_command(agent: str, flag: str) -> str:
    # HEADROOM_AGENT is set explicitly: the payload sniffing is only a fallback.
    return (f'HEADROOM_AGENT={agent} {python_command()} "{HOOK_SCRIPT}" {flag}')


def windows_command(agent: str, flag: str) -> str:
    executable = shutil.which("python") or sys.executable
    return (f"$env:HEADROOM_AGENT='{agent}'; & \"{executable.replace(chr(92), '/')}\" "
            f"\"{str(HOOK_SCRIPT).replace(chr(92), '/')}\" {flag}")


def build_entry(agent: str, event: str, flag: str) -> dict:
    shape = SHAPES[agent]
    matcher, timeout = EVENTS[agent][event]
    inner = {"type": "command", "command": hook_command(agent, flag), "timeout": timeout}
    if shape["windows"]:
        inner["commandWindows"] = windows_command(agent, flag)
    if shape["async"]:
        inner["async"] = True
    if shape["status"]:
        inner["statusMessage"] = ("Starting headroom display" if flag == START_FLAG
                                  else "Charging headroom")
    entry: dict = {}
    if shape["matcher"] and matcher is not None:
        entry["matcher"] = matcher
    entry["hooks"] = [inner]
    return entry


def is_headroom_entry(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    for inner in entry.get("hooks") or []:
        if isinstance(inner, dict) and MARKER in str(inner.get("command", "")):
            return True
    return False


def plan_agent(agent: str) -> dict:
    """Describe the merge without touching disk."""
    path = config_path(agent)
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(document, dict):
            raise ValueError("config root must be an object")
    except FileNotFoundError:
        document = {}
    except ValueError as exc:
        return {"agent": agent, "path": path, "ok": False,
                "reason": f"refusing to rewrite an unparseable config: {exc}"}
    hooks = document.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return {"agent": agent, "path": path, "ok": False,
                "reason": "'hooks' is not an object"}
    changes = []
    for event, flag in ((START_EVENT[agent], START_FLAG), (PROMPT_EVENT[agent], PROMPT_FLAG)):
        entries = hooks.setdefault(event, [])
        if not isinstance(entries, list):
            return {"agent": agent, "path": path, "ok": False,
                    "reason": f"'{event}' is not a list"}
        replacement = build_entry(agent, event, flag)
        for index, entry in enumerate(entries):
            if is_headroom_entry(entry):
                entries[index] = replacement
                changes.append((event, "updated"))
                break
        else:
            entries.append(replacement)
            changes.append((event, "added"))
    if SHAPES[agent]["description"]:
        document.setdefault("description", "headroom: I need a reset.")
    return {"agent": agent, "path": path, "ok": True, "changes": changes, "document": document}


def write_config(path: Path, document: dict, backup: bool = True) -> Path | None:
    """Atomic write, preserving the original file mode."""
    saved = None
    if path.exists() and backup:
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        saved = path.with_name(f"{path.name}.bak-headroom-{stamp}")
        shutil.copy2(path, saved)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else None
    handle, temporary = tempfile.mkstemp(prefix=".headroom-hooks-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    if mode is not None:
        os.chmod(path, mode)
    return saved


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="actually write the configs; without it this is a preview")
    parser.add_argument("--agents", default="codex,claude,gemini",
                        help="comma list; default codex,claude,gemini")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args(argv)
    if not HOOK_SCRIPT.is_file():
        parser.error(f"hook script not found: {HOOK_SCRIPT}")
    selected = [name.strip() for name in args.agents.split(",") if name.strip()]
    unknown = [name for name in selected if name not in SHAPES]
    if unknown:
        parser.error(f"unsupported agents: {', '.join(unknown)}")
    for agent in selected:
        result = plan_agent(agent)
        if not result["ok"]:
            print(f"[skip] {agent}: {result['reason']}")
            continue
        state = "would write" if not args.apply else "writing"
        print(f"[{state}] {agent} -> {result['path']}")
        for event, action in result["changes"]:
            print(f"         {action}: {event}")
        if args.apply:
            saved = write_config(result["path"], result["document"],
                                 backup=not args.no_backup)
            if saved:
                print(f"         backup: {saved}")
    if not args.apply:
        print("\nPreview only. Re-run with --apply to write.")
        print("Trust the new definitions in each agent before they take effect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
