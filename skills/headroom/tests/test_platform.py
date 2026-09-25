"""Cross-platform installer, hook-launch, and macOS display logic.

Every test uses a temporary HOME/CODEX_HOME and fake native objects, so the
suite runs on Windows, macOS, and Linux without touching real Codex config.
"""

import contextlib
import importlib.util
import io
import json
import os
import queue
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))
sys.path.insert(0, str(SKILL / "hooks"))
import headroom_desktop as desktop  # noqa: E402
import headroom_hook as hook  # noqa: E402
import headroom_tray as tray  # noqa: E402
import install_hooks as installer  # noqa: E402

FOREIGN = {"type": "command", "command": "echo keep-me", "timeout": 5}


class InstallerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name) / "home with space"
        self.codex = self.home / ".codex"
        self.target = self.codex / "hooks.json"
        env = patch.dict(os.environ, {"HOME": str(self.home), "USERPROFILE": str(self.home),
                                      "CODEX_HOME": str(self.codex)})
        env.start()
        self.addCleanup(env.stop)

    def run_installer(self, *argv):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = installer.main(list(argv))
        return code, output.getvalue()

    def backups(self):
        return sorted(self.codex.glob("hooks.json.bak-*"))

    def test_posix_commands_are_quoted_python3_without_powershell(self):
        root = Path("/Users/me/My Repos/head'room/skills/headroom")
        hooks = installer.build_hooks(root, "/opt/homebrew/bin/python3", windows=False)
        self.assertEqual(set(hooks), {"SessionStart", "UserPromptSubmit"})
        for event, flag in (("SessionStart", "--session-start"), ("UserPromptSubmit", "--user-prompt")):
            entry = hooks[event][0]["hooks"][0]
            self.assertNotIn("commandWindows", entry)
            self.assertNotIn("&", entry["command"])
            self.assertNotIn("powershell", entry["command"].lower())
            self.assertEqual(shlex.split(entry["command"]),
                             ["/opt/homebrew/bin/python3", str(root / "hooks" / "headroom_hook.py"), flag])
            self.assertTrue(entry["async"])
        self.assertEqual(hooks["SessionStart"][0]["matcher"], "startup|resume|clear|compact")
        self.assertNotIn("matcher", hooks["UserPromptSubmit"][0])
        shown = installer.build_hooks(root, "python3", display="desktop", windows=False)
        self.assertTrue(shown["SessionStart"][0]["hooks"][0]["command"].startswith(
            "HEADROOM_DISPLAY=desktop python3 "))
        self.assertNotIn("HEADROOM_DISPLAY", shown["UserPromptSubmit"][0]["hooks"][0]["command"])

    def test_windows_entries_match_the_powershell_installer_template(self):
        root, python = Path("C:/Users/me/headroom/skills/headroom"), "C:/Python312/python.exe"
        template = (SKILL / "hooks" / "hooks.json.template").read_text(encoding="utf-8")
        expected = json.loads(template.replace("__PLUGIN_ROOT__", str(root).replace("\\", "/"))
                              .replace("__PYTHON__", python))["hooks"]
        self.assertEqual(installer.build_hooks(root, python, windows=True), expected)

    def test_fresh_install_is_idempotent_and_creates_data_dir(self):
        code, output = self.run_installer()
        self.assertEqual(code, 0, output)
        data = json.loads(self.target.read_text(encoding="utf-8"))
        self.assertEqual(data["description"], installer.DESCRIPTION)
        entry = data["hooks"]["UserPromptSubmit"][0]["hooks"][0]
        if sys.platform == "win32":
            self.assertIn(installer.default_python().replace("\\", "/"), entry["commandWindows"])
        else:
            self.assertEqual(shlex.split(entry["command"])[0], installer.default_python())
        self.assertTrue((self.codex / "headroom").is_dir())
        before = self.target.read_bytes()
        code, output = self.run_installer()
        self.assertEqual(code, 0)
        self.assertIn("already up to date", output)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(self.backups(), [])

    def test_merge_keeps_other_hooks_backs_up_and_replaces_stale_entries(self):
        self.codex.mkdir(parents=True)
        stale = {"type": "command", "command": "python3 /old/place/hooks/headroom_hook.py --user-prompt"}
        original = {"hooks": {"UserPromptSubmit": [{"hooks": [FOREIGN]}, {"hooks": [stale]}],
                              "Stop": [{"hooks": [FOREIGN]}]}, "custom": 1}
        self.target.write_text(json.dumps(original), encoding="utf-8")
        code, output = self.run_installer("--python", sys.executable)
        self.assertEqual(code, 0, output)
        data = json.loads(self.target.read_text(encoding="utf-8"))
        self.assertEqual(data["custom"], 1)
        self.assertNotIn("description", data)  # Never relabel a user's own file.
        self.assertEqual(data["hooks"]["Stop"], [{"hooks": [FOREIGN]}])
        prompt = data["hooks"]["UserPromptSubmit"]
        self.assertEqual(prompt[0], {"hooks": [FOREIGN]})
        self.assertEqual(len(prompt), 2)
        self.assertNotIn("/old/place", json.dumps(prompt))
        self.assertEqual(len(data["hooks"]["SessionStart"]), 1)
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(json.loads(self.backups()[0].read_text(encoding="utf-8")), original)

        code, output = self.run_installer("--uninstall")
        self.assertEqual(code, 0, output)
        remaining = json.loads(self.target.read_text(encoding="utf-8"))
        self.assertEqual(remaining, {"hooks": {"UserPromptSubmit": [{"hooks": [FOREIGN]}],
                                               "Stop": [{"hooks": [FOREIGN]}]}, "custom": 1})
        self.assertTrue((self.codex / "headroom").is_dir())  # Data is never deleted.

    def test_uninstall_removes_a_headroom_only_file_and_is_repeatable(self):
        self.assertEqual(self.run_installer()[0], 0)
        (self.codex / "headroom" / "ledger.sqlite3").write_bytes(b"keep")
        code, output = self.run_installer("--uninstall")
        self.assertEqual(code, 0, output)
        self.assertFalse(self.target.exists())
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual((self.codex / "headroom" / "ledger.sqlite3").read_bytes(), b"keep")
        code, output = self.run_installer("--uninstall")
        self.assertEqual(code, 0)
        self.assertIn("No headroom hooks", output)

    def test_dry_run_and_invalid_config_never_write(self):
        code, output = self.run_installer("--dry-run")
        self.assertEqual(code, 0, output)
        self.assertIn("headroom_hook.py", output)
        self.assertFalse(self.codex.exists())
        self.codex.mkdir(parents=True)
        self.target.write_text("{not json", encoding="utf-8")
        code, output = self.run_installer()
        self.assertEqual(code, 1)
        self.assertIn("error", output)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "{not json")
        self.assertEqual(self.backups(), [])

    def test_rejects_python_older_than_3_10(self):
        with patch.object(installer, "python_version", return_value=(3, 9)):
            code, output = self.run_installer("--python", "/usr/bin/python3")
        self.assertEqual(code, 1)
        self.assertIn("3.10+", output)
        self.assertFalse(self.target.exists())

    @unittest.skipIf(sys.platform == "win32", "symlinks need extra privileges on Windows")
    def test_optional_skill_link_is_created_and_removed(self):
        code, output = self.run_installer("--link-skill")
        self.assertEqual(code, 0, output)
        link = self.home / ".agents" / "skills" / "headroom"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), SKILL.resolve())
        self.assertEqual(self.run_installer("--link-skill")[0], 0)
        self.assertEqual(self.run_installer("--uninstall")[0], 0)
        self.assertFalse(link.exists() or link.is_symlink())

    @unittest.skipIf(sys.platform == "win32" or not shutil.which("sh"), "POSIX shell wrapper")
    def test_install_sh_wrapper_installs_into_temporary_home(self):
        env = {**os.environ, "HOME": str(self.home), "PYTHON": sys.executable}
        env.pop("CODEX_HOME")
        result = subprocess.run(["sh", str(SKILL / "hooks" / "install.sh"), "--display", "desktop"],
                                capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(self.target.read_text(encoding="utf-8"))
        start = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertTrue(start.startswith("HEADROOM_DISPLAY=desktop "))
        self.assertEqual(shlex.split(start)[1], sys.executable)


class HookLaunchTests(unittest.TestCase):
    def test_display_processes_are_detached_per_platform(self):
        with patch.object(hook.subprocess, "Popen") as spawn:
            hook.spawn_detached(["python3", "display.py"], None)
        options = spawn.call_args.kwargs
        if sys.platform == "win32":
            self.assertIn("creationflags", options)
            self.assertNotIn("start_new_session", options)
        else:
            self.assertTrue(options["start_new_session"])
            self.assertNotIn("creationflags", options)
        self.assertIs(options["stdin"], subprocess.DEVNULL)

    def test_default_display_follows_the_platform(self):
        env = {"HEADROOM_DISABLE_DASHBOARD": "0", "HEADROOM_DISABLE_DESKTOP": "0"}
        with patch.dict(os.environ, env), patch.object(hook, "start_dashboard") as web, \
                patch.object(hook, "spawn_detached") as spawn:
            os.environ.pop("HEADROOM_DISPLAY", None)
            hook.start_display(None)
        if sys.platform in ("win32", "darwin"):
            # Both platforms have a working tray; macOS runs pystray's status
            # item on Tk's main-thread Aqua loop. A missing Tk or tray package
            # falls back to the dashboard from inside headroom_desktop.
            spawn.assert_called_once()
            web.assert_not_called()
        else:
            web.assert_called_once()
            spawn.assert_not_called()


class CliTests(unittest.TestCase):
    def test_status_cli_honors_codex_home_like_the_hook_and_dashboard(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            with contextlib.closing(sqlite3.connect(home / "thread_history_1.sqlite")) as conn:
                conn.execute("CREATE TABLE thread_items (item_type TEXT, created_at_ms INTEGER)")
                conn.commit()
            env = {**os.environ, "CODEX_HOME": str(home), "HOME": str(home / "elsewhere")}
            env.pop("HEADROOM_STATE_PATH", None)
            result = subprocess.run([sys.executable, str(SKILL / "scripts" / "headroom.py"), "status"],
                                    capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["left_percent"], 100)
            self.assertFalse((home / "headroom" / "ledger.sqlite3").exists())


class DesktopPlatformTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_tray_card_opens_above_bottom_taskbar_and_below_top_menu_bar(self):
        area = (0, 25, 1440, 900)
        self.assertEqual(desktop.tray_card_top(880, area), 880 - desktop.CARD_H - 12)
        self.assertEqual(desktop.tray_card_top(12, area), 24)
        x, y = desktop.clamp_position(1200 - desktop.CARD_W, desktop.tray_card_top(12, area),
                                      desktop.CARD_W, desktop.CARD_H, area)
        self.assertGreaterEqual(y, area[1])
        self.assertLessEqual(y + desktop.CARD_H, area[3])

    @unittest.skipIf(sys.platform == "win32", "POSIX flock lock")
    def test_posix_instance_lock_is_per_ledger_and_released(self):
        first = desktop.InstanceLock(self.root / "a.sqlite3")
        second = desktop.InstanceLock(self.root / "a.sqlite3")
        other = desktop.InstanceLock(self.root / "b.sqlite3")
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            self.assertTrue(other.acquire())
            first.close()
            self.assertTrue(second.acquire())
        finally:
            for lock in (first, second, other):
                lock.close()

    def test_fallback_starts_web_dashboard_or_reuses_running_one(self):
        args = SimpleNamespace(codex_home=self.root, state_path=self.root / "ledger.sqlite3",
                               settings_path=self.root / "desktop.json", lang="en", show=False)
        stderr = io.StringIO()
        with patch.object(desktop.os, "execv") as execv, patch("socket.create_connection",
                                                                side_effect=OSError), \
                contextlib.redirect_stderr(stderr):
            desktop.fall_back_to_web(args, "no tray here")
        command = execv.call_args.args[1]
        self.assertTrue(command[1].endswith("headroom_dashboard.py"))
        self.assertEqual(command[command.index("--state-path") + 1], str(args.state_path))
        self.assertEqual(command[command.index("--lang") + 1], "en")
        self.assertIn("no tray here", stderr.getvalue())
        self.assertIn("http://127.0.0.1:8766/?lang=en", stderr.getvalue())
        with patch.object(desktop.os, "execv") as execv, patch("socket.create_connection"), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(desktop.fall_back_to_web(args, "no tray here"), 0)
        execv.assert_not_called()

    def test_windows_only_webview_renderer_is_not_the_default_elsewhere(self):
        with patch.dict(os.environ, {"CODEX_HOME": str(self.root)}):
            args = desktop.parse_args([])
        self.assertEqual(args.renderer, "webview" if sys.platform == "win32" else "tk")


class FakeDetachedIcon:
    def __init__(self, name, icon, title, menu):
        self.name, self.icon, self.title, self.menu = name, icon, title, menu
        self.visible = False
        self.detached = 0

    def run(self, setup=None):
        raise AssertionError("macOS must not start a separate AppKit loop")

    def run_detached(self, setup=None):
        self.detached += 1

    def stop(self):
        raise AssertionError("macOS must not stop Tk's NSApplication")

    def update_menu(self):
        pass


def tray_dependencies_importable():
    if not (importlib.util.find_spec("pystray") and importlib.util.find_spec("PIL")):
        return False
    try:
        import pystray  # noqa: F401  (backend selection needs a desktop session)
    except Exception:
        return False
    return True


@unittest.skipUnless(tray_dependencies_importable(), "optional tray dependencies")
class MacMenuBarTests(unittest.TestCase):
    def test_macos_status_item_runs_detached_on_the_calling_thread(self):
        view = desktop.presentation({"left_percent": 80, "spent_points": 2, "cap_points": 10}, "en")
        events = queue.Queue()
        with patch("pystray.Icon", FakeDetachedIcon), patch.object(tray.sys, "platform", "darwin"), \
                patch.object(tray, "hide_dock_icon") as hide_dock:
            icon = tray.TrayIcon(events, desktop.TEXT["en"], view)
            icon.start()
            self.assertIsNone(icon.thread)
            self.assertEqual(icon.icon.detached, 1)
            self.assertTrue(icon.icon.visible)
            hide_dock.assert_called_once()
            list(icon.icon.menu)[0](icon.icon)
            self.assertEqual(events.get_nowait(), "toggle")
            icon.close()
            self.assertFalse(icon.icon.visible)


if __name__ == "__main__":
    unittest.main(verbosity=2)
