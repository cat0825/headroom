#!/usr/bin/env python3
"""Cross-platform, non-destructive hook installer for headroom.

Safely merges headroom hooks into Codex hooks.json without overwriting
existing user hooks, preserving all existing configurations.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
from pathlib import Path


def get_default_codex_home() -> Path:
    env_home = os.environ.get("CODEX_HOME")
    if env_home:
        return Path(env_home).resolve()
    return (Path.home() / ".codex").resolve()


def get_plugin_root() -> Path:
    configured = os.environ.get("HEADROOM_PLUGIN_ROOT")
    if configured:
        return Path(configured).resolve()
    # hooks/ -> headroom (skill root)
    return Path(__file__).resolve().parents[1]


def build_headroom_hooks(plugin_root: Path) -> dict[str, list[dict]]:
    root_str = str(plugin_root).replace("\\", "/")
    hook_script = f"{root_str}/hooks/headroom_hook.py"
    
    python_cmd = "python3"
    python_win = "\"__PYTHON__\""

    return {
        "SessionStart": [
            {
                "matcher": "startup|resume|clear|compact",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{python_cmd} \"{hook_script}\" --session-start",
                        "commandWindows": f"{python_win} \"{hook_script}\" --session-start",
                        "timeout": 3,
                        "async": True,
                        "statusMessage": "Starting headroom dashboard",
                    }
                ],
            }
        ],
        "UserPromptSubmit": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{python_cmd} \"{hook_script}\" --user-prompt",
                        "commandWindows": f"{python_win} \"{hook_script}\" --user-prompt",
                        "timeout": 30,
                        "async": True,
                        "statusMessage": "Charging headroom",
                    }
                ],
            }
        ],
    }


def is_headroom_hook_block(block: dict) -> bool:
    content = json.dumps(block, ensure_ascii=False)
    return "headroom_hook.py" in content


def install(codex_home: Path, dry_run: bool = False) -> int:
    codex_home.mkdir(parents=True, exist_ok=True)
    target = codex_home / "hooks.json"
    
    data: dict = {"hooks": {}}
    if target.is_file():
        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            print(f"Error reading existing {target}: {exc}", file=sys.stderr)
            return 1

    hooks_dict = data.setdefault("hooks", {})
    new_hooks = build_headroom_hooks(get_plugin_root())

    added_count = 0
    for event_name, hook_blocks in new_hooks.items():
        existing_list = hooks_dict.setdefault(event_name, [])
        for block in hook_blocks:
            if not any(is_headroom_hook_block(item) for item in existing_list):
                existing_list.append(block)
                added_count += 1

    if dry_run:
        print(f"[Dry Run] Would merge {added_count} new hook blocks into {target}")
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    if target.is_file() and added_count > 0:
        timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        backup = codex_home / f"hooks.json.bak-{timestamp}"
        shutil.copy2(target, backup)
        print(f"Backed up existing config to: {backup}")

    tmp_target = codex_home / f"hooks.json.tmp-{os.getpid()}"
    with open(tmp_target, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    tmp_target.replace(target)
    print(f"Successfully installed headroom hooks into {target} (added {added_count} blocks).")
    return 0


def uninstall(codex_home: Path, dry_run: bool = False) -> int:
    target = codex_home / "hooks.json"
    if not target.is_file():
        print(f"Target {target} does not exist. Nothing to uninstall.")
        return 0

    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"Error reading {target}: {exc}", file=sys.stderr)
        return 1

    hooks_dict = data.get("hooks", {})
    removed_count = 0
    for event_name in list(hooks_dict.keys()):
        original_len = len(hooks_dict[event_name])
        hooks_dict[event_name] = [
            item for item in hooks_dict[event_name] if not is_headroom_hook_block(item)
        ]
        removed_count += (original_len - len(hooks_dict[event_name]))
        if not hooks_dict[event_name]:
            del hooks_dict[event_name]

    if dry_run:
        print(f"[Dry Run] Would remove {removed_count} hook blocks from {target}")
        return 0

    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    backup = codex_home / f"hooks.json.bak-{timestamp}"
    shutil.copy2(target, backup)
    print(f"Backed up existing config to: {backup}")

    tmp_target = codex_home / f"hooks.json.tmp-{os.getpid()}"
    with open(tmp_target, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    tmp_target.replace(target)
    print(f"Successfully uninstalled headroom hooks from {target} (removed {removed_count} blocks).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=get_default_codex_home(),
        help="Path to Codex home directory (defaults to $CODEX_HOME or ~/.codex)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without modifying files",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove headroom hooks from hooks.json",
    )
    args = parser.parse_args()

    if args.uninstall:
        return uninstall(args.codex_home, dry_run=args.dry_run)
    return install(args.codex_home, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
