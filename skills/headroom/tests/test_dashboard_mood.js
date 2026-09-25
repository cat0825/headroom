// Exercise the rendered dashboard JavaScript, never a real server or ledger.
const assert = require('node:assert/strict');
const path = require('node:path');
const vm = require('node:vm');
const {spawnSync} = require('node:child_process');

// macOS and many Linux distributions ship only `python3`.
const python = process.env.HEADROOM_TEST_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const result = spawnSync(python, ['-c',
  'import json,sys; sys.path.insert(0,sys.argv[1]); import headroom_dashboard as d; print(json.dumps({k+str(t):d.render_page(k,desktop=t) for k in d.TEXT for t in (False,True)}))',
  path.join(__dirname, '../scripts')], {encoding: 'utf8', env: {...process.env, PYTHONIOENCODING: 'utf-8'}});
assert.equal(result.status, 0, result.stderr || String(result.error));
const pages = JSON.parse(result.stdout);
const cases = [
  [100, 'brain-full.png'],
  [99.28, 'brain-full.png'],
  [70.01, 'brain-full.png'],
  [70, 'brain-full.png'],
  [69.99, 'brain-declining.webp'],
  [30, 'brain-declining.webp'],
  [29.99, 'brain-low.png'],
  [0, 'brain-low.png'],
  [100, 'brain-full.png'],
];

async function check(lang, storedMute = 'false', brokenStorage = false, desktop = false) {
  const page = pages[lang + (desktop ? 'True' : 'False')];
  if (lang === 'en') assert.doesNotMatch(page, /[\u3400-\u9fff]/);
  const elements = Object.fromEntries(['collapse','language','sound-toggle','sound-icon','sound-label','mood-image','mood-video','mood-feedback','mood','value','meta','updated','fill','refresh'].map(id => [id, {
    hidden: true, textContent: '', style: {}, classList: {add() {}, remove() {}},
    getAttribute(name) {return this[name];}, addEventListener(event, callback) {
      const previous=this[event];
      this[event]=previous?((...args)=>{const result=previous(...args);return callback(...args)??result;}):callback;
    },
    removeAttribute(name) {delete this[name];}, pause() {this.paused=true;}, load() {},
    setAttribute(name,value) {this[name]=value;},
    async play() {this.paused=false;},
  }]));
  let payload = {left_percent: 72.4, spent_points: 230.46, cap_points: 835};
  let failure = null;
  let interval;
  let timerId=0;
  const timers=new Map();
  const frames=new Map();
  let frameId=0, audioLevel=200, sourceCount=0, clickTime=0, randomValue=0;
  const motion={matches:false};
  let gain;
  const windowEvents={}, documentEvents={}, bridgeCalls=[];
  class FakeAudioContext {
    sampleRate=48000;
    destination={};
    async resume() {}
    createGain() {gain={gain:{value:1},connect(){}};return gain;}
    createMediaElementSource() {sourceCount++;return {connect() {}};}
    createAnalyser() {return {frequencyBinCount:256,getByteFrequencyData(data) {data.fill(audioLevel);}};}
  }
  function animate(now) {
    const pending=[...frames.values()];frames.clear();
    for(const callback of pending)callback(now);
  }
  const context = vm.createContext({
    Math:Object.assign(Object.create(Math),{random:()=>randomValue}),
    performance: {now(){return clickTime;}},
    document: {getElementById(id) {assert.ok(elements[id], id); return elements[id];},
      addEventListener(event,callback){documentEvents[event]=callback;}},
    setInterval(callback, delay) {assert.equal(delay, 10000); interval = callback;},
    setTimeout(callback) {timers.set(++timerId,callback);return timerId;},
    clearTimeout(id) {timers.delete(id);},
    requestAnimationFrame(callback) {frames.set(++frameId,callback);return frameId;},
    cancelAnimationFrame(id) {frames.delete(id);},
    window: {matchMedia() {return motion;},AudioContext:FakeAudioContext,
      addEventListener(event,callback){windowEvents[event]=callback;},
      pywebview:{api:{
        async settings(){return {muted:!brokenStorage&&storedMute==='true'};},
        collapse(){bridgeCalls.push(['collapse']);},
        language(lang){bridgeCalls.push(['language',lang]);},
        sound(muted){bridgeCalls.push(['sound',muted]);},
      }}},
    localStorage: {
      getItem(key){assert.equal(key,'headroom-muted');if(brokenStorage)throw Error('blocked');return storedMute;},
      setItem(key,value){assert.equal(key,'headroom-muted');if(brokenStorage)throw Error('blocked');storedMute=value;},
    },
    async fetch(url, options) {
      assert.equal(url, '/api/status?lang=' + lang);
      assert.equal(options.cache, 'no-store');
      assert.equal(options.method, undefined); // GET only, no charging route.
      if (failure === 'network') throw new TypeError('untranslated browser error');
      return {ok: failure !== 'http', async json() {
        if (failure === 'json') throw new SyntaxError('untranslated parse error');
        return payload;
      }};
    },
  });
  for(const script of page.matchAll(/<script>([\s\S]*?)<\/script>/g))vm.runInContext(script[1],context);
  if(desktop)windowEvents.pywebviewready();
  await new Promise(setImmediate); // Let the initial async refresh finish.
  assert.equal(elements.value.textContent, '72.40%');
  assert.equal(elements.meta.textContent, lang === 'en' ? 'Used 230.46 / 835 points' : '已用 230.46 / 835 点');
  assert.ok(elements.updated.textContent.startsWith(lang === 'en' ? 'Updated: ' : '最近刷新：'));
  assert.equal(typeof elements.refresh.click, 'function');
  assert.equal(typeof interval, 'function');
  const sound=elements['sound-toggle'];
  assert.equal(elements['mood-video'].muted,!brokenStorage&&storedMute==='true');
  if(elements['mood-video'].muted)sound.click();
  sound.click();assert.equal(elements['mood-video'].muted,true);
  assert.equal(sound['aria-pressed'],'true');
  assert.equal(elements['sound-label'].textContent,lang==='en'?'Muted':'已静音');
  sound.click();assert.equal(elements['mood-video'].muted,false);
  if(!brokenStorage)assert.equal(storedMute,'false');

  for (const [percent, file] of cases) {
    payload = {...payload, left_percent: percent};
    await elements.refresh.click();
    assert.equal(elements['mood-image'].src, '/assets/' + file, `Wrong image at ${percent}%`);
    assert.equal(elements.mood.hidden, false);
    assert.ok(elements['mood-image'].alt.length > 0);
    if (lang === 'en') assert.doesNotMatch(elements['mood-image'].alt, /[\u3400-\u9fff]/);
    assert.equal(elements.fill.style.width, percent + '%');
  }
  const video=elements['mood-video'], picture=elements['mood-image'];
  async function finishHop() {
    const pending=[...timers.values()];timers.clear();
    for(const callback of pending)await callback();
  }
  // Each click starts exactly one clip, even while a clip is already playing.
  const playlist=[
    'dog-hey.mp4','dog-bark-1.mp4','dog-bark-2.mp4','dog-bark-3.mp4','dog-bark-4.mp4','dog-bark-5.mp4',
    'dog-call.mp4','dog-dadada.m4a','dog-industry-baby.m4a',
  ];
  const draws=[0,0,0,.15,.3,.45,.6,.75,.9,0];
  for(const [step,file] of [...playlist,playlist[0]].entries()) {
    const audioOnly=file.endsWith('.m4a'), previousImage=picture.src;
    randomValue=draws[step];clickTime+=step===9?3500:500;
    elements.mood.click();
    assert.equal(video.hidden,true);assert.equal(picture.hidden,false);
    await finishHop();
    assert.equal(video.src,'/assets/'+(audioOnly?'audio/':'videos/')+file);
    assert.equal(video.hidden,audioOnly);assert.equal(video.paused,false);
    assert.equal(picture.hidden,!audioOnly);assert.equal(picture.src,previousImage);
    await interval();assert.equal(video.hidden,audioOnly);
    if(audioOnly){
      const currentSource=video.src;
      sound.click();assert.equal(gain.gain.value,0);assert.equal(video.paused,false);
      assert.equal(video.muted,false); // The pre-mute analyser still receives sound.
      audioLevel=220;animate(10);animate(80);
      const strong=picture.style.transform;
      assert.match(strong,/translateY\(-[\d.]+px\) rotate\(-?[\d.]+deg\) scale\(1\.[\d]+\)/);
      sound.click();assert.equal(gain.gain.value,1);assert.equal(video.src,currentSource);
      audioLevel=0;
      for(let time=180;time<3000;time+=100)animate(time);
      assert.notEqual(picture.style.transform,strong);
      assert.ok(Math.abs(Number(picture.style.transform.match(/rotate\(([-\d.]+)/)[1]))<1);
      motion.matches=true;animate(3100);
      assert.equal(picture.style.transform,'');assert.equal(frames.size,0);
      motion.matches=false;
      video.onended();assert.equal(picture.hidden,false);assert.equal(video.paused,true);
      assert.equal(timers.size,0);
      assert.equal(picture.style.transform,'');assert.equal(picture.style.filter,'');
      assert.equal(frames.size,0);
    }else{
      assert.equal(frames.size,0);assert.equal(picture.style.transform,'');
    }
  }
  assert.equal(sourceCount,1); // Reuse the media audio graph across tracks.
  video.onended();
  assert.equal(video.hidden,true);assert.equal(picture.hidden,false);
  assert.equal(timers.size,0); // Ending never automatically starts another clip.
  elements.mood.click();elements.mood.click();await finishHop();
  assert.equal(video.src,'/assets/videos/dog-bark-2.mp4');
  const staleEnd=video.onended, staleError=video.onerror;
  elements.mood.click();await finishHop();staleEnd();staleError();
  assert.equal(video.src,'/assets/videos/dog-bark-1.mp4');
  assert.equal(video.hidden,false);
  elements.mood.keydown({key:'Escape'});assert.equal(video.hidden,true);
  const originalPlay=video.play;
  video.play=async()=>{throw Error('blocked');};
  elements.mood.click();await finishHop();
  assert.equal(video.hidden,true);assert.equal(elements['mood-feedback'].hidden,false);
  video.play=originalPlay;elements.mood.click();await finishHop();
  assert.equal(video.src,'/assets/videos/dog-bark-2.mp4');
  video.onerror();assert.equal(video.hidden,true);
  elements.mood.click();await finishHop();
  for (const mode of ['network', 'http', 'json', 'data']) {
    failure = mode;
    if (mode === 'data') payload = {...payload, left_percent: null};
    await interval();
    assert.equal(elements.value.textContent, lang === 'en' ? 'Unavailable' : '不可用');
    assert.equal(elements.mood.hidden, true);
    assert.equal(video.hidden,true);assert.equal(video.paused,true);
    assert.equal(elements.fill.style.width, '0%');
    assert.doesNotMatch(elements.meta.textContent, /untranslated/);
    if (lang === 'en') assert.doesNotMatch(elements.meta.textContent, /[\u3400-\u9fff]/);
  }
  failure = null;
  payload = {...payload, left_percent: 100};
  await interval();
  assert.equal(elements.value.textContent, '100.00%');
  assert.equal(elements.mood.hidden, false);
  // The window is measured from the preceding image click, not playback end.
  for(const [gap,file] of [[4000,'dog-hey.mp4'],[3499,'dog-bark-1.mp4'],
                          [3499,'dog-bark-2.mp4'],[3500,'dog-hey.mp4'],
                          [3501,'dog-hey.mp4'],[100,'dog-bark-1.mp4']]){
    clickTime+=gap;elements.mood.click();await finishHop();
    assert.equal(video.src,'/assets/videos/'+file,`Click gap ${gap}`);
  }
  clickTime+=3500;sound.click();await interval();
  elements.mood.click();await finishHop();
  assert.equal(video.src,'/assets/videos/dog-hey.mp4'); // Other controls never extend the streak.
  randomValue=.999;
  elements.mood.click();await finishHop();
  assert.equal(video.src,'/assets/audio/dog-industry-baby.m4a'); // Can jump straight to the last clip.
  elements.mood.click();await finishHop();
  assert.equal(video.src,'/assets/audio/dog-dadada.m4a'); // Never immediately repeat or select Hey Dog.
  clickTime+=3500;elements.mood.click();await finishHop();
  assert.equal(video.src,'/assets/videos/dog-hey.mp4');
  if(desktop){
    assert.ok(bridgeCalls.some(call=>call[0]==='sound'&&call[1]===true));
    elements.collapse.click();assert.equal(video.paused,true);
    assert.deepEqual(bridgeCalls.at(-1),['collapse']);
    elements.language.click();assert.deepEqual(bridgeCalls.at(-1),['language',lang==='en'?'zh':'en']);
    elements.mood.click();await finishHop();
    documentEvents.keydown({key:'Escape'});assert.equal(video.paused,true);
    assert.deepEqual(bridgeCalls.at(-1),['collapse']);
  }
}

(async () => {
  for (const lang of ['en', 'zh']) await check(lang);
  await check('zh','true');
  await check('en','false',true);
  for(const lang of ['en','zh'])await check(lang,'true',false,true);
  console.log('Passed: random selection, no consecutive repeats, 3.5-second reset, media playback, audio-reactive motion, mute and recovery in both languages.');
})().catch(error => {console.error(error); process.exitCode = 1;});
