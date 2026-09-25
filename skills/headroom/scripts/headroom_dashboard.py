"""Read-only loopback browser dashboard for headroom."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import headroom  # noqa: E402

ASSET_DIR = Path(__file__).resolve().parents[1] / "assets"
IMAGE_TYPES = {
    "/assets/brain-full.png": "image/png",
    "/assets/brain-declining.webp": "image/webp",
    "/assets/brain-low.png": "image/png",
}
VIDEO_FILES = (
    "dog-hey.mp4",
    *(f"dog-bark-{i}.mp4" for i in range(1, 6)),
    "dog-call.mp4",
)
VIDEO_PATHS = {f"/assets/videos/{name}": ASSET_DIR / "videos" / name for name in VIDEO_FILES}
MEDIA_PATHS = {
    **VIDEO_PATHS,
    **{f"/assets/audio/{name}.m4a": ASSET_DIR / "audio" / f"{name}.m4a"
       for name in ("dog-dadada", "dog-industry-baby")},
}

TEXT = {
    "zh": {
        "locale": "zh-CN", "heading": "脑力剩余", "loading": "读取中...",
        "refresh": "刷新", "spent": "已用", "points": "点", "updated": "最近刷新：",
        "unavailable": "不可用", "read_failed": "读取失败，请稍后刷新",
        "invalid_data": "脑力数据不可用",
        "status_error": "无法读取本机 AI 历史或 headroom 账本",
        "sources": "来源",
        "from_counts": "（按对话条数估算）",
        "no_agents": "未发现可读取的 agent 历史",
        "mood_full": "脑力充足：戴耳机的狗狗", "mood_low": "脑力低于30%：咆哮的狗狗",
        "mood_declining": "脑力下降中：流泪的猫猫",
        "play_next": "点击播放下一段大狗叫（有声音）；Esc 停止",
        "video_failed": "片段暂时无法播放，请再点一次重试。",
        "sound_on": "声音开", "sound_off": "已静音", "mute": "静音",
    },
    "en": {
        "locale": "en-US", "heading": "Headroom", "loading": "Loading...",
        "refresh": "Refresh", "spent": "Used", "points": "points", "updated": "Updated: ",
        "unavailable": "Unavailable", "read_failed": "Unable to load. Please refresh.",
        "invalid_data": "Headroom data is unavailable",
        "status_error": "Unable to read local AI history or the headroom ledger",
        "sources": "Sources",
        "from_counts": " (estimated from message count)",
        "no_agents": "No readable agent history found",
        "mood_full": "Plenty of headroom: dog wearing headphones",
        "mood_low": "Below 30%: barking dog",
        "mood_declining": "Headroom running low: crying cat",
        "play_next": "Click to play the next dog clip with sound; Esc to stop",
        "video_failed": "Unable to play this clip. Click again to retry.",
        "sound_on": "Sound on", "sound_off": "Muted", "mute": "Mute",
    },
}

PAGE = """<!doctype html>
<html lang="__LOCALE__"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>headroom</title><style>
:root{font-family:system-ui,"Microsoft YaHei",sans-serif;color:#14213d;background:#f5f7fb}
body{margin:0;min-height:100vh;display:grid;place-items:center;padding:20px;box-sizing:border-box}
.card{width:min(420px,100%);box-sizing:border-box;background:white;border-radius:20px;padding:28px;box-shadow:0 14px 40px #17213d18}
.heading-row{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:22px;min-height:64px}
.mood{flex-shrink:0}.mood img,.mood video{display:block;width:64px;height:64px;object-fit:contain;border-radius:10px;background:#f5f7fb}
.mood [hidden]{display:none}
button.mood{width:64px;height:64px;padding:0;margin:0;background:transparent;border-radius:10px}
button.mood:hover{background:transparent}button.mood:focus-visible{outline:3px solid #2563eb;outline-offset:4px}
.mood.jumping{animation:mood-hop .24s ease-out}
.mood.dancing img{will-change:transform,filter;transform-origin:50% 75%}
@keyframes mood-hop{0%,100%{transform:translateY(0)}45%{transform:translateY(-18px)}}
@media(prefers-reduced-motion:reduce){.mood.jumping{animation:none}.mood.dancing img{transform:none!important;filter:none!important}}
h1{font-size:23px;margin:0}.value{font-size:56px;font-weight:750;line-height:1;color:#2563eb}
.sub{color:#61708c;font-size:13px;margin-top:6px}.track{height:12px;background:#e8edf6;border-radius:99px;margin:24px 0 16px;overflow:hidden}
.fill{height:100%;width:0;background:linear-gradient(90deg,#58c7ab,#2563eb);border-radius:99px;transition:width .35s}
.meta{font-size:15px;line-height:1.8}.small{font-size:12px;color:#61708c;margin-top:14px}
.sources{font-size:12px;color:#61708c;margin-top:10px;line-height:1.7;word-break:break-word}
.sources b{color:#14213d;font-weight:600}
.sources .dead{color:#b42318}
button{width:100%;border:0;border-radius:10px;background:#14213d;color:#fff;font:inherit;padding:12px;margin-top:24px;cursor:pointer}
button:hover{background:#263b61}.error{color:#b42318}
.card{position:relative}
.controls{margin-top:24px}.controls button{margin:0;min-width:0}
.controls #sound-toggle{position:absolute;right:8px;bottom:2px;width:24px;height:24px;display:grid;place-items:center;padding:4px;background:transparent;color:#8b95a5;opacity:.6;border-radius:6px}
.controls #sound-toggle:hover,.controls #sound-toggle:focus-visible{background:#f3f5f8;color:#52627b;opacity:1}
#sound-toggle svg{width:16px;height:16px;display:block}.sound-cross{display:none}
#sound-toggle[aria-pressed="true"] .sound-waves{display:none}#sound-toggle[aria-pressed="true"] .sound-cross{display:block}
.controls button:focus-visible{outline:3px solid #2563eb;outline-offset:3px}
</style></head><body><main class="card">
<div class="heading-row"><h1>__HEADING__</h1>
<button id="mood" class="mood" type="button" hidden aria-label="__PLAY_NEXT__" title="__PLAY_NEXT__"><img id="mood-image" alt="" width="64" height="64"><video id="mood-video" width="64" height="64" playsinline preload="none" hidden></video></button></div>
<div id="mood-feedback" class="small error" role="status" hidden></div>
<div id="value" class="value">--</div><div class="sub">left</div>
<div class="track"><div id="fill" class="fill"></div></div>
<div id="meta" class="meta">__LOADING__</div><div id="updated" class="small"></div>
<div id="sources" class="sources" hidden></div>
<div class="controls"><button id="refresh">__REFRESH__</button><button id="sound-toggle" type="button" aria-label="__MUTE__" aria-pressed="false" title="__SOUND_ON__"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 5 6 9H3v6h3l5 4Z"/><path class="sound-waves" d="M15 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/><path class="sound-cross" d="m16 9 5 6m0-6-5 6"/></svg><span id="sound-label" hidden>__SOUND_ON__</span></button></div>
</main><script>
const TEXT=__TEXT__;
const LANGUAGE=__LANGUAGE__;
const clips=__CLIPS__;
const mood=document.getElementById('mood'), picture=document.getElementById('mood-image');
const video=document.getElementById('mood-video'), feedback=document.getElementById('mood-feedback');
let lastClipIndex=0, retryClip=null, playbackId=0, hopTimer, lastClipClick=null;
let audioContext, audioGain, analyser, spectrum, danceFrame;
let muted=false;
try{muted=localStorage.getItem('headroom-muted')==='true';}catch(error){}
function updateSound(){
 // Mute only the speaker output so the analyser can keep the picture dancing.
 video.muted=audioGain?false:muted;
 if(audioGain)audioGain.gain.value=muted?0:1;
 document.getElementById('sound-toggle').setAttribute('aria-pressed',String(muted));
 document.getElementById('sound-toggle').title=muted?TEXT.sound_off:TEXT.sound_on;
 document.getElementById('sound-label').textContent=muted?TEXT.sound_off:TEXT.sound_on;
}
document.getElementById('sound-toggle').addEventListener('click',()=>{
 muted=!muted;updateSound();
 try{localStorage.setItem('headroom-muted',String(muted));}catch(error){}
});
updateSound();
const reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)');
function prepareAudio(){
 const AudioEngine=window.AudioContext||window.webkitAudioContext;
 if(!audioContext&&AudioEngine&&!reducedMotion.matches){
  try{
   audioContext=new AudioEngine();
   analyser=audioContext.createAnalyser();
   analyser.fftSize=512;analyser.smoothingTimeConstant=.55;
   audioGain=audioContext.createGain();
   audioGain.gain.value=muted?0:1;
   audioGain.connect(audioContext.destination);
   const source=audioContext.createMediaElementSource(video);
   source.connect(audioGain);
   source.connect(analyser);
   spectrum=new Uint8Array(analyser.frequencyBinCount);
   updateSound();
  }catch(error){analyser=null;}
 }
 // Resume inside the click gesture, including later video clips using this graph.
 return audioContext?audioContext.resume().then(()=>true,()=>false):Promise.resolve(false);
}
function stopDance(){
 cancelAnimationFrame(danceFrame);
 picture.style.transform='';picture.style.filter='';
 mood.classList.remove('dancing');
}
function startDance(id){
 if(!analyser||reducedMotion.matches)return;
 mood.classList.add('dancing');
 let average=0, pulse=0, direction=1, lastBeat=-1000, previous=0;
 function frame(now){
  if(id!==playbackId)return;
  if(video.paused||video.ended||reducedMotion.matches){stopDance();return;}
  analyser.getByteFrequencyData(spectrum);
  const bassEnd=Math.max(2,Math.min(spectrum.length,Math.ceil(450*analyser.fftSize/audioContext.sampleRate)));
  let bass=0, loudness=0;
  for(let i=1;i<bassEnd;i++)bass+=spectrum[i];
  for(let i=0;i<64;i++)loudness+=spectrum[i];
  const energy=.7*bass/((bassEnd-1)*255)+.3*loudness/(64*255);
  const dt=previous?Math.min(now-previous,100):16;previous=now;
  if(energy>.12&&energy>average*1.12&&now-lastBeat>180){
   pulse=1;direction*=-1;lastBeat=now;
  }
  average+=(energy-average)*(1-Math.exp(-dt/240));
  pulse*=Math.exp(-dt/190);
  const bounce=Math.max(0,Math.sin((now-lastBeat)/115))*pulse;
  const jump=36*bounce+energy*7;
  const rotation=direction*(pulse*27+Math.sin(now/115)*energy*12);
  const scale=1+pulse*.48+Math.sin((now-lastBeat)/95)*energy*.16;
  picture.style.transform=`translateY(${-jump.toFixed(2)}px) rotate(${rotation.toFixed(2)}deg) scale(${scale.toFixed(3)})`;
  picture.style.filter=`drop-shadow(0 ${4+pulse*8}px ${3+pulse*7}px rgba(37,99,235,${(.12+pulse*.25).toFixed(2)}))`;
  danceFrame=requestAnimationFrame(frame);
 }
 danceFrame=requestAnimationFrame(frame);
}
function stopClip(){
 playbackId++;
 stopDance();
 clearTimeout(hopTimer);
 video.onended=null;video.onerror=null;
 video.pause();video.removeAttribute('src');video.load();
 video.hidden=true;picture.hidden=false;mood.classList.remove('jumping');
}
function playNextClip(){
 if(mood.hidden)return;
 const now=performance.now();
 const continuing=lastClipClick!==null&&now-lastClipClick<3500;
 const choices=clips.map((_,i)=>i).filter(i=>i!==0&&i!==lastClipIndex);
 const index=continuing?(retryClip??choices[Math.floor(Math.random()*choices.length)]):0;
 lastClipIndex=index;retryClip=null;
 lastClipClick=now;
 stopClip();feedback.hidden=true;
 const id=playbackId;
 const audioReady=(clips[index].audioOnly||audioContext)?prepareAudio():Promise.resolve(false);
 // Restart the hop even if another click interrupts the previous animation.
 void mood.offsetWidth;
 mood.classList.add('jumping');
 hopTimer=setTimeout(async()=>{
  if(id!==playbackId)return;
  mood.classList.remove('jumping');
  const clip=clips[index];
  video.src=clip.src;picture.hidden=!clip.audioOnly;video.hidden=clip.audioOnly;
  const failed=()=>{
   if(id!==playbackId)return;
   stopClip();retryClip=index;feedback.textContent=TEXT.video_failed;feedback.hidden=false;
  };
  video.onended=()=>{if(id===playbackId)stopClip();};
  video.onerror=failed;
  try{
   const ready=await audioReady;
   if(id!==playbackId)return;
   await video.play();
   if(id===playbackId&&clip.audioOnly&&ready)startDance(id);
  }catch(error){failed();}
 },reducedMotion.matches?0:240);
}
mood.addEventListener('click',playNextClip);
mood.addEventListener('keydown',event=>{if(event.key==='Escape')stopClip();});
function updateMood(percent){
 const picture=document.getElementById('mood-image');
 const [file,description]=percent>=70
  ? ['brain-full.png',TEXT.mood_full]
  : percent<30
   ? ['brain-low.png',TEXT.mood_low]
   : ['brain-declining.webp',TEXT.mood_declining];
 const source='/assets/'+file;
 if(picture.getAttribute('src')!==source)picture.src=source;
 picture.alt=description;
 document.getElementById('mood').hidden=false;
}
function escapeHtml(value){
 return String(value).replace(/[&<>"']/g,character=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
}
function updateSources(data){
 // Per-agent totals come from the same window that produced the cap.
 const box=document.getElementById('sources');
 const per=data.per_agent_counts||{};
 const agents=data.agents||[];
 const parts=Object.keys(per)
  .map(name=>({name,total:Object.values(per[name]).reduce((sum,value)=>sum+value,0)}))
  .filter(item=>item.total>0)
  .sort((left,right)=>right.total-left.total)
  .map(item=>'<b>'+escapeHtml(item.name)+'</b> '+item.total);
 for(const agent of agents){
  if(agent&&agent.available===false){parts.push('<span class="dead">'+escapeHtml(agent.name)+' ✕</span>');}
 }
 if(!parts.length){box.hidden=true;return;}
 box.innerHTML=TEXT.sources+' · '+parts.join(' · ');
 box.hidden=false;
}
async function refresh(){
 const value=document.getElementById('value'), meta=document.getElementById('meta');
 let errorText=TEXT.read_failed;
 try{const response=await fetch('/api/status?lang='+LANGUAGE,{cache:'no-store'});
  if(!response.ok){errorText=TEXT.status_error;throw Error();}
  const data=await response.json();
  const percent=data.left_percent;
  if(![percent,data.spent_points,data.cap_points].every(v=>typeof v==='number'&&Number.isFinite(v))
     ||percent<0||percent>100||data.spent_points<0||data.cap_points<=0){errorText=TEXT.invalid_data;throw Error();}
  value.textContent=percent.toFixed(2)+'%';
  document.getElementById('fill').style.width=Math.max(0,Math.min(100,percent))+'%';
  updateMood(percent);
  meta.textContent=TEXT.spent+' '+data.spent_points.toFixed(2)+' / '+data.cap_points+' '+TEXT.points
   +(data.spent_origin==='counts'?TEXT.from_counts:'');
  document.getElementById('updated').textContent=TEXT.updated+new Date().toLocaleTimeString(TEXT.locale);
  meta.classList.remove('error');
  updateSources(data);
 }catch(error){stopClip();feedback.hidden=true;value.textContent=TEXT.unavailable;meta.textContent=errorText;meta.classList.add('error');document.getElementById('mood').hidden=true;document.getElementById('fill').style.width='0%';document.getElementById('sources').hidden=true}
}
document.getElementById('refresh').addEventListener('click',refresh);
refresh();setInterval(refresh,10000);
</script></body></html>"""


TRAY_STYLE = """<style>
html,body{width:100%;height:100%;min-height:0;overflow:hidden;background:white}
body{display:block;padding:0}.card{width:100%;height:100%;padding:42px 22px 20px;box-shadow:none;border-radius:12px}
.heading-row{min-height:56px;margin-bottom:12px}h1{font-size:20px}
button.mood,.mood img,.mood video{width:56px;height:56px}
.value{font-size:46px}.track{height:10px;margin:16px 0 10px}.meta{font-size:14px}
.small{margin-top:8px}.controls{margin-top:14px}.controls #refresh{padding:10px}
.tray-toolbar{position:absolute;right:8px;top:5px;display:flex;gap:4px;z-index:2}
.tray-toolbar button{width:30px;height:28px;margin:0;padding:0;background:white;color:#61708c;font-size:13px}
.tray-toolbar #collapse{font-size:22px}.tray-toolbar button:hover{background:#f3f5f8}
#mood-feedback{position:absolute;bottom:49px;left:22px;right:22px;background:white;font-size:11px}
</style>"""

TRAY_SCRIPT = """<script>
async function trayReady(){
 const settings=await window.pywebview.api.settings();
 muted=settings.muted;updateSound();
}
window.addEventListener('pywebviewready',()=>{trayReady().catch(()=>{});});
document.getElementById('collapse').addEventListener('click',()=>{stopClip();window.pywebview.api.collapse();});
document.getElementById('language').addEventListener('click',()=>{stopClip();window.pywebview.api.language(LANGUAGE==='en'?'zh':'en');});
document.getElementById('sound-toggle').addEventListener('click',()=>{window.pywebview.api.sound(muted);});
document.addEventListener('keydown',event=>{if(event.key==='Escape'){stopClip();window.pywebview.api.collapse();}});
</script>"""


def render_page(lang: str, *, desktop: bool = False) -> str:
    # Only fixed, allowlisted translations are interpolated, never URL input.
    labels = TEXT[lang]
    page = PAGE
    for key in ("locale", "heading", "loading", "refresh", "play_next", "mute", "sound_on"):
        page = page.replace(f"__{key.upper()}__", labels[key])
    page = page.replace("__TEXT__", json.dumps(labels, ensure_ascii=False)).replace(
        "__LANGUAGE__", json.dumps(lang)).replace("__CLIPS__", json.dumps([
            {"src": route, "audioOnly": path.suffix == ".m4a"}
            for route, path in MEDIA_PATHS.items()
        ]))
    if desktop:
        collapse = "收起" if lang == "zh" else "Collapse"
        switch = "EN" if lang == "zh" else "ZH"
        switch_label = "切换语言" if lang == "zh" else "Switch language"
        controls = (f'<div class="tray-toolbar"><button id="language" aria-label="{switch_label}">{switch}</button>'
                    f'<button id="collapse" aria-label="{collapse}" title="{collapse}">−</button></div>')
        page = page.replace('</head>', TRAY_STYLE + '</head>').replace(
            '<main class="card">', '<main class="card">' + controls).replace('</body>', TRAY_SCRIPT + '</body>')
    return page


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--lang", choices=TEXT, default=os.environ.get("HEADROOM_LANG", "zh"),
                        help="dashboard language; default: HEADROOM_LANG or zh")
    parser.add_argument("--codex-home", type=Path,
                        default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))),
                        help="Codex home; overrides the codex adapter's default root")
    parser.add_argument("--agent-home", action="append", metavar="NAME=PATH",
                        help="override one agent's root; repeatable")
    parser.add_argument("--agents", default=os.environ.get("HEADROOM_AGENTS", "auto"),
                        help="'auto' to discover every installed agent, or a comma list")
    parser.add_argument("--state-path", type=Path, default=headroom.default_state_path())
    args = parser.parse_args(argv)
    if args.lang not in TEXT:
        parser.error("HEADROOM_LANG must be zh or en")
    return args


def create_handler(codex_home: Path, state_path: Path, lang: str = "zh", *,
                   desktop: bool = False, adapters: list | None = None):
    if lang not in TEXT:
        raise ValueError("Dashboard language must be zh or en")
    # Default to the single Codex root so a bare (home, ledger) call stays
    # hermetic; the CLI passes an explicitly discovered adapter list.
    selected = list(adapters) if adapters else [headroom.CodexAdapter(root=codex_home)]

    class Handler(BaseHTTPRequestHandler):
        def serve_media(self, path: str, head_only: bool = False) -> None:
            # Explicit allowlist, never resolve a filesystem path from URL input.
            try:
                source = MEDIA_PATHS[path].open("rb")
            except (KeyError, OSError):
                self.send_error(404)
                return
            with source:
                size = os.fstat(source.fileno()).st_size
                start, end, status = 0, size - 1, 200
                requested = self.headers.get("Range")
                if requested:
                    match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
                    try:
                        if not match or not any(match.groups()):
                            raise ValueError()
                        first, last = match.groups()
                        if first:
                            start = int(first)
                            end = min(int(last), size - 1) if last else size - 1
                        else:
                            length = int(last)
                            if length <= 0:
                                raise ValueError()
                            start = max(0, size - length)
                        if start > end or start >= size:
                            raise ValueError()
                    except ValueError:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Content-Length", "0")
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        return
                    status = 206
                self.send_response(status)
                self.send_header("Content-Type", "audio/mp4" if MEDIA_PATHS[path].suffix == ".m4a" else "video/mp4")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Cache-Control", "no-store")
                if status == 206:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                if head_only:
                    return
                source.seek(start)
                remaining = end - start + 1
                try:
                    while remaining:
                        chunk = source.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass  # Switching clips can cancel an in-flight media request.

        def do_HEAD(self) -> None:
            self.serve_media(urlsplit(self.path).path, head_only=True)

        def do_GET(self) -> None:
            url = urlsplit(self.path)
            if url.path in MEDIA_PATHS:
                self.serve_media(url.path)
                return
            requested = parse_qs(url.query).get("lang", [lang])[-1]
            language = requested if requested in TEXT else lang
            if url.path == "/":
                body = render_page(language, desktop=desktop).encode("utf-8")
                content_type, status = "text/html; charset=utf-8", 200
            elif url.path in IMAGE_TYPES:
                try:
                    body = (ASSET_DIR / url.path.rsplit("/", 1)[1]).read_bytes()
                    content_type, status = IMAGE_TYPES[url.path], 200
                except OSError:
                    body, status, content_type = b"Not found", 404, "text/plain; charset=utf-8"
            elif url.path == "/api/status":
                try:
                    today = datetime.now(headroom.SHANGHAI).date()
                    result = headroom.status(
                        headroom.baseline_strict(selected, today), today, state_path)
                    body = json.dumps(result, ensure_ascii=False).encode("utf-8")
                    status = 200
                except Exception:
                    body = json.dumps({"error": TEXT[language]["status_error"]}, ensure_ascii=False).encode("utf-8")
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
    try:
        adapters = headroom.select_adapters(args.agents, args.agent_home, args.codex_home)
    except (headroom.UnknownAgentError, ValueError) as exc:
        raise SystemExit(str(exc))
    handler = create_handler(args.codex_home, args.state_path, args.lang, adapters=adapters)
    with ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
        print(f"headroom dashboard: http://127.0.0.1:{args.port}/?lang={args.lang}", flush=True)
        print(f"headroom agents: {', '.join(a.name for a in adapters) or 'none'}", flush=True)
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
