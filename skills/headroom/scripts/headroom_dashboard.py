"""Read-only loopback browser dashboard for headroom."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import headroom  # noqa: E402

PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>headroom</title><style>
:root{font-family:system-ui,"Microsoft YaHei",sans-serif;color:#14213d;background:#f5f7fb}
body{margin:0;min-height:100vh;display:grid;place-items:center;padding:20px;box-sizing:border-box}
.card{width:min(420px,100%);background:white;border-radius:20px;padding:28px;box-shadow:0 14px 40px #17213d18}
h1{font-size:23px;margin:8px 0 22px}.value{font-size:56px;font-weight:750;line-height:1;color:#2563eb}
.sub{color:#61708c;font-size:13px;margin-top:6px}.track{height:12px;background:#e8edf6;border-radius:99px;margin:24px 0 16px;overflow:hidden}
.fill{height:100%;width:0;background:linear-gradient(90deg,#58c7ab,#2563eb);border-radius:99px;transition:width .35s}
.meta{font-size:15px;line-height:1.8}.small{font-size:12px;color:#61708c;margin-top:14px}
button{width:100%;border:0;border-radius:10px;background:#14213d;color:#fff;font:inherit;padding:12px;margin-top:24px;cursor:pointer}
button:hover{background:#263b61}.error{color:#b42318}
</style></head><body><main class="card">
<h1>脑力剩余</h1>
<div id="value" class="value">--</div><div class="sub">left</div>
<div class="track"><div id="fill" class="fill"></div></div>
<div id="meta" class="meta">读取中...</div><div id="updated" class="small"></div>
<button id="refresh">刷新</button>
</main><script>
async function refresh(){
 const value=document.getElementById('value'), meta=document.getElementById('meta');
 try{const response=await fetch('/api/status',{cache:'no-store'}); const data=await response.json();
  if(!response.ok)throw Error(data.error||'读取失败');
  value.textContent=Number(data.left_percent).toFixed(2)+'%';
  document.getElementById('fill').style.width=Math.max(0,Math.min(100,data.left_percent))+'%';
  meta.textContent='已用 '+Number(data.spent_points).toFixed(2)+' / '+data.cap_points+' 点';
  document.getElementById('updated').textContent='最近刷新：'+new Date().toLocaleTimeString();
  meta.classList.remove('error');
 }catch(error){value.textContent='不可用';meta.textContent=error.message;meta.classList.add('error')}
}
document.getElementById('refresh').addEventListener('click',refresh);
refresh();setInterval(refresh,10000);
</script></body></html>"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--state-path", type=Path, default=Path(os.environ.get(
        "HEADROOM_STATE_PATH", str(Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "headroom" / "ledger.sqlite3"),
    )))
    return parser.parse_args()


def create_handler(codex_home: Path, state_path: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/":
                body = PAGE.encode("utf-8")
                content_type, status = "text/html; charset=utf-8", 200
            elif self.path == "/api/status":
                try:
                    today = datetime.now(headroom.SHANGHAI).date()
                    base = headroom.baseline(codex_home / "thread_history_1.sqlite", today)
                    result = headroom.status(base, today, state_path)
                    body = json.dumps(result, ensure_ascii=False).encode("utf-8")
                    status = 200
                except Exception:
                    body = json.dumps({"error": "无法读取本机 Codex 历史或 headroom 账本"}, ensure_ascii=False).encode("utf-8")
                    status = 503
                content_type = "application/json; charset=utf-8"
            else:
                body, status, content_type = b"Not found", 404, "text/plain; charset=utf-8"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args) -> None:
            pass

    return Handler


def main() -> int:
    args = parse_args()
    if not 1024 <= args.port <= 65535:
        raise SystemExit("--port must be between 1024 and 65535")
    with ThreadingHTTPServer(("127.0.0.1", args.port), create_handler(args.codex_home, args.state_path)) as server:
        print(f"headroom dashboard: http://127.0.0.1:{args.port}/", flush=True)
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
