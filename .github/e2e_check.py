"""TEMPORARY CI helper: exercise install_hooks.py and the real hook commands."""
import json, os, signal, socket, sqlite3, subprocess, sys, tempfile, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INSTALLER = REPO / "skills/headroom/hooks/install_hooks.py"
sys.path.insert(0, str(REPO / "skills/headroom/scripts"))

root = Path(tempfile.mkdtemp())
home = root / "home dir"
codex = home / ".codex"
codex.mkdir(parents=True)
with sqlite3.connect(codex / "thread_history_1.sqlite") as conn:
    conn.execute("CREATE TABLE thread_items (item_type TEXT, created_at_ms INTEGER)")
env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "CODEX_HOME": str(codex)}
env.pop("HEADROOM_STATE_PATH", None)


def run(*args, **kw):
    print("+", args, flush=True)
    return subprocess.run(list(args), env=kw.pop("env", env), check=True, timeout=120, **kw)


def hook_command(hooks, event):
    entry = hooks["hooks"][event][0]["hooks"][0]
    if sys.platform == "win32":
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", entry["commandWindows"]]
    return ["/bin/sh", "-c", entry["command"]]


def port_up(port=8766):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


mode = sys.argv[1] if len(sys.argv) > 1 else "--charge"
if mode == "--charge":
    run(sys.executable, str(INSTALLER), "--dry-run")
    assert not (codex / "hooks.json").exists()
    run(sys.executable, str(INSTALLER))
    first = (codex / "hooks.json").read_bytes()
    run(sys.executable, str(INSTALLER))
    assert (codex / "hooks.json").read_bytes() == first, "installer is not idempotent"
    hooks = json.loads(first)
    print(json.dumps(hooks, indent=2))
    event = {"prompt": "hello from CI? 你好 🙂", "session_id": "s1", "turn_id": "t1", "cwd": str(root)}
    for _ in range(2):  # the retry must not charge twice
        run(*hook_command(hooks, "UserPromptSubmit"), input=json.dumps(event).encode("utf-8"),
            env={**env, "HEADROOM_DISABLE_DASHBOARD": "1"})
    print(json.loads((codex / "headroom" / "last-hook.json").read_text()))
    with sqlite3.connect(codex / "headroom" / "ledger.sqlite3") as conn:
        rows = conn.execute("SELECT points, provider FROM debits").fetchall()
    assert len(rows) == 1 and rows[0][1] == "jev-mock-local", rows
    run(*hook_command(hooks, "SessionStart"), input=b'{"cwd": "."}',
        env={**env, "HEADROOM_DISABLE_DASHBOARD": "1"})
    run(sys.executable, str(INSTALLER), "--uninstall")
    assert not (codex / "hooks.json").exists()
    assert (codex / "headroom" / "ledger.sqlite3").exists()
    print("charge e2e OK:", rows)
else:
    # macOS: SessionStart with --display desktop must leave a detached display running.
    if mode == "--desktop-fallback":
        # Diagnose the fallback directly first, with stderr visible.
        direct = subprocess.Popen([sys.executable, str(REPO / "skills/headroom/scripts/headroom_desktop.py"),
                                   "--codex-home", str(codex), "--state-path", str(root / "direct.sqlite3")],
                                  env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline and not port_up() and direct.poll() is None:
            time.sleep(0.5)
        up = port_up()
        direct.kill()
        print("direct run: port up =", up, "exit =", direct.poll(), "after %.1fs" % (time.monotonic() - deadline + 40))
        print(direct.stdout.read().decode(errors="replace"))
        assert up, "direct fallback did not start the dashboard"
        time.sleep(1)
    run(sys.executable, str(INSTALLER), "--display", "desktop")
    hooks = json.loads((codex / "hooks.json").read_text())
    launch_env = {k: v for k, v in env.items() if k != "HEADROOM_DISABLE_DASHBOARD"}
    started = time.monotonic()
    run(*hook_command(hooks, "SessionStart"), input=b'{"cwd": "."}', env=launch_env)
    print("hook returned in %.2fs" % (time.monotonic() - started))
    import headroom_desktop
    ledger = codex / "headroom" / "ledger.sqlite3"
    lock = headroom_desktop.InstanceLock(ledger)
    try:
        if mode == "--desktop":
            deadline = time.monotonic() + 20
            held = False
            while time.monotonic() < deadline and not held:
                time.sleep(1)
                held = not lock.acquire()
                if not held:
                    lock.close()
            ps = subprocess.run(["ps", "-axo", "pid,command"], capture_output=True, text=True).stdout
            print("\n".join(line for line in ps.splitlines() if "headroom_" in line))
            assert held, "desktop display did not start or exited"
            time.sleep(5)
            ps = subprocess.run(["pgrep", "-f", "headroom_desktop.py"], capture_output=True, text=True).stdout
            assert ps.strip(), "desktop display crashed after starting"
            assert not port_up(), "desktop mode must not need the web dashboard"
            print("menu bar display running:", ps.split())
        else:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline and not port_up():
                time.sleep(1)
            assert port_up(), "fallback web dashboard did not start"
            import urllib.request
            with urllib.request.urlopen("http://127.0.0.1:8766/api/status", timeout=5) as r:
                print("fallback dashboard status:", r.read().decode())
    finally:
        subprocess.run(["pkill", "-f", "headroom_desktop.py"])
        subprocess.run(["pkill", "-f", "headroom_dashboard.py"])
        lock.close()
    print("desktop e2e OK", mode)
