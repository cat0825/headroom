#!/usr/bin/env python3
"""Cross-platform, non-destructive installer for headroom's Codex hooks.

Merges headroom's SessionStart and UserPromptSubmit hooks into
``$CODEX_HOME/hooks.json`` (default ``~/.codex/hooks.json``) while keeping
every other hook. Re-running is idempotent; changed files are backed up first.
Use ``--uninstall`` to remove only headroom's entries. Runtime data (ledger,
preferences) stays in ``$CODEX_HOME/headroom`` and is never deleted.

The installer itself runs on Python 3.8+, so it can explain an interpreter
that is too old (e.g. macOS's /usr/bin/python3) instead of crashing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HOOK_SCRIPT = "headroom_hook.py"
DESCRIPTION = "headroom: I need a reset."
DISPLAYS = ("desktop", "web", "both", "off")
MIN_PYTHON = (3, 10)
HOOK_EVENTS = {"SessionStart": ("--session-start", "Starting headroom dashboard"),
               "UserPromptSubmit": ("--user-prompt", "Charging headroom")}
PYTHON_NAME = re.compile(r"pythonw?(?:\d+(?:\.\d+)*)?(?:\.exe)?$", re.IGNORECASE)


def skill_root() -> Path:
    configured = os.environ.get("HEADROOM_PLUGIN_ROOT")
    return Path(configured).resolve() if configured else Path(__file__).resolve().parents[1]


def default_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()


def default_python() -> str:
    """The interpreter running the installer; never pythonw for hooks."""
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    return str(executable)


def python_version(python: str) -> tuple[int, int] | None:
    if python == sys.executable:
        return sys.version_info[:2]
    try:
        result = subprocess.run([python, "-c", "import sys; print(*sys.version_info[:2])"],
                                capture_output=True, text=True, timeout=20, check=True)
        major, minor = result.stdout.split()
        return int(major), int(minor)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def posix_command(python: str, hook: str, flag: str, display: str | None = None) -> str:
    """A /bin/sh command line: no PowerShell, safe for paths with spaces."""
    prefix = f"HEADROOM_DISPLAY={display} " if display else ""
    return f"{prefix}{shlex.quote(python)} {shlex.quote(hook)} {flag}"


def windows_command(python: str, hook: str, flag: str) -> str:
    """The same PowerShell form install_windows.ps1 writes."""
    return f'& "{python}" "{hook}" {flag}'.replace("\\", "/")


def build_hooks(root: Path, python: str, display: str | None = None,
                windows: bool | None = None) -> dict:
    """Return headroom's ``hooks`` mapping, mirroring hooks.json.template."""
    windows = sys.platform == "win32" if windows is None else windows
    hook = str(root / "hooks" / HOOK_SCRIPT)
    if windows:
        hook = hook.replace("\\", "/")
    events = {
        "SessionStart": ("--session-start", 3, "Starting headroom dashboard",
                         "startup|resume|clear|compact"),
        "UserPromptSubmit": ("--user-prompt", 30, "Charging headroom", None),
    }
    hooks = {}
    for event, (flag, timeout, status, matcher) in events.items():
        if windows:  # Byte-for-byte what install_windows.ps1 writes from the template.
            entry = {"type": "command", "command": f'python3 "{hook}" {flag}',
                     "commandWindows": windows_command(python, hook, flag)}
        else:  # Only SessionStart reads HEADROOM_DISPLAY; keep the charging hook plain.
            entry = {"type": "command", "command": posix_command(
                python, hook, flag, display if flag == "--session-start" else None)}
        entry.update({"timeout": timeout, "async": True, "statusMessage": status})
        block = {"matcher": matcher} if matcher else {}
        block["hooks"] = [entry]
        hooks[event] = [block]
    return hooks


def is_headroom_hook(hook: object, event: str) -> bool:
    """Recognize installed commands without matching incidental mentions."""
    if not isinstance(hook, dict) or hook.get("type") != "command" or event not in HOOK_EVENTS:
        return False
    flag, status = HOOK_EVENTS[event]
    for key in ("command", "commandWindows", "command_windows"):
        command = hook.get(key)
        if not isinstance(command, str):
            continue
        try:
            parts = shlex.split(command)
        except ValueError:
            continue
        if parts and parts[0].startswith("HEADROOM_DISPLAY="):
            parts = parts[1:]
        if parts and parts[0] == "&":  # PowerShell's call operator.
            parts = parts[1:]
        if len(parts) != 3:
            continue
        python, script, argument = parts
        script = script.replace("\\", "/")
        python_name = python.replace("\\", "/").rsplit("/", 1)[-1]
        if (argument == flag and script.endswith("/hooks/" + HOOK_SCRIPT)
                and (PYTHON_NAME.fullmatch(python_name)
                     or hook.get("statusMessage") == status)):
            return True
    return False


def load_config(target: Path) -> dict:
    if not target.exists():
        return {}
    data = json.loads(target.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise ValueError(f"{target} must contain a JSON object with a 'hooks' object")
    return data


def without_headroom(data: dict) -> tuple[dict, int]:
    result = {key: value for key, value in data.items() if key != "hooks"}
    hooks, removed = {}, 0
    for event, blocks in data.get("hooks", {}).items():
        if not isinstance(blocks, list):
            hooks[event] = blocks
            continue
        kept = []
        for block in blocks:
            if not isinstance(block, dict) or not isinstance(block.get("hooks"), list):
                kept.append(block)
                continue
            commands = [hook for hook in block["hooks"] if not is_headroom_hook(hook, event)]
            removed_here = len(block["hooks"]) - len(commands)
            removed += removed_here
            if not removed_here:
                kept.append(block)
            elif commands:
                kept.append({**block, "hooks": commands})
        if kept:
            hooks[event] = kept
    result["hooks"] = hooks
    return result, removed


def merged(data: dict, headroom_hooks: dict) -> dict:
    result, _removed = without_headroom(data)
    if not data:  # A new file mirrors hooks.json.template; never relabel a user's file.
        result = {"description": DESCRIPTION, **result}
    for event, blocks in headroom_hooks.items():
        result["hooks"].setdefault(event, []).extend(blocks)
    return result


def backup(target: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = target.with_name(f"{target.name}.bak-{stamp}")
    counter = 1
    while path.exists():
        path = target.with_name(f"{target.name}.bak-{stamp}-{counter}")
        counter += 1
    path.write_bytes(target.read_bytes())
    return path


def write_json(target: Path, data: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".hooks-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def skill_link_path() -> Path:
    # Codex discovers user skills in ~/.agents/skills and follows symlinks.
    return Path.home() / ".agents" / "skills" / "headroom"


def link_skill(root: Path, dry_run: bool) -> str:
    link = skill_link_path()
    if link.is_symlink() and link.resolve() == root.resolve():
        return f"Skill link already present: {link}"
    if link.exists() or link.is_symlink():
        return f"Skipped skill link: {link} already exists and is not a link to {root}"
    if not dry_run:
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(root, target_is_directory=True)
    return f"{'Would link' if dry_run else 'Linked'} skill: {link} -> {root}"


def unlink_skill(root: Path, dry_run: bool) -> str | None:
    link = skill_link_path()
    if link.is_symlink() and link.resolve() == root.resolve():
        if not dry_run:
            link.unlink()
        return f"{'Would remove' if dry_run else 'Removed'} skill link: {link}"
    return None


def install(args) -> int:
    root = skill_root()
    target = args.codex_home / "hooks.json"
    if not (root / "hooks" / HOOK_SCRIPT).is_file():
        print(f"error: {root / 'hooks' / HOOK_SCRIPT} not found", file=sys.stderr)
        return 1
    version = python_version(args.python)
    if version is None or version < MIN_PYTHON:
        found = "not runnable" if version is None else "Python %d.%d" % version
        print(f"error: hooks need Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+, but {args.python} is "
              f"{found}. Install a newer Python (python.org or Homebrew) and re-run with "
              "--python /path/to/python3.", file=sys.stderr)
        return 1
    current = load_config(target)
    updated = merged(current, build_hooks(root, args.python, args.display))
    changed = updated != current
    messages = []
    if args.dry_run:
        print(json.dumps(updated, indent=2, ensure_ascii=False))
        messages.append(f"[dry run] Would {'update' if changed else 'leave unchanged'} {target}")
    elif changed:
        if target.exists():
            messages.append(f"Backed up {target} to {backup(target)}")
        write_json(target, updated)
        messages.append(f"Installed headroom hooks in {target}")
    else:
        messages.append(f"headroom hooks already up to date in {target}")
    data_dir = args.codex_home / "headroom"
    if not args.dry_run:
        data_dir.mkdir(parents=True, exist_ok=True)
    messages.append(f"Data directory: {data_dir}")
    if args.link_skill:
        messages.append(link_skill(root, args.dry_run))
    print("\n".join(messages))
    if changed and not args.dry_run:
        print("Next: run /hooks in Codex, review and trust headroom's SessionStart and "
              "UserPromptSubmit hooks, then start a new session.")
    return 0


def uninstall(args) -> int:
    target = args.codex_home / "hooks.json"
    messages = []
    current = load_config(target)
    updated, removed = without_headroom(current)
    if not removed:
        messages.append(f"No headroom hooks found in {target}")
    elif args.dry_run:
        messages.append(f"[dry run] Would remove {removed} headroom hook(s) from {target}")
    else:
        messages.append(f"Backed up {target} to {backup(target)}")
        if not updated["hooks"] and set(updated) <= {"hooks", "description"} \
                and updated.get("description", DESCRIPTION) == DESCRIPTION:
            target.unlink()
            messages.append(f"Removed {target} (it only contained headroom hooks)")
        else:
            write_json(target, updated)
            messages.append(f"Removed {removed} headroom hook(s) from {target}")
    unlinked = unlink_skill(skill_root(), args.dry_run)
    if unlinked:
        messages.append(unlinked)
    messages.append(f"Kept your data in {args.codex_home / 'headroom'}; delete it manually if desired.")
    print("\n".join(messages))
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--codex-home", type=Path, default=default_codex_home(),
                        help="Codex home (default: $CODEX_HOME or ~/.codex)")
    parser.add_argument("--python", default=default_python(),
                        help="interpreter the hooks run with (default: this Python, "
                             "so pip-installed desktop extras are found)")
    parser.add_argument("--display", choices=DISPLAYS,
                        help="what SessionStart opens on macOS/Linux; default: the web dashboard "
                             "(desktop = menu bar card)")
    parser.add_argument("--link-skill", action="store_true",
                        help="also symlink the skill into ~/.agents/skills/headroom")
    parser.add_argument("--uninstall", action="store_true", help="remove headroom's hooks")
    parser.add_argument("--dry-run", action="store_true", help="show changes without writing")
    args = parser.parse_args(argv)
    if args.display and sys.platform == "win32":
        parser.error("--display applies to macOS/Linux; on Windows set HEADROOM_DISPLAY instead")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        return uninstall(args) if args.uninstall else install(args)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
