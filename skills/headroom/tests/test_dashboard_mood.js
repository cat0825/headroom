// Exercise the rendered dashboard JavaScript, never a real server or ledger.
const assert = require('node:assert/strict');
const path = require('node:path');
const vm = require('node:vm');
const {spawnSync} = require('node:child_process');

const result = spawnSync(process.env.HEADROOM_TEST_PYTHON || 'python', ['-c',
  'import json,sys; sys.path.insert(0,sys.argv[1]); import headroom_dashboard as d; print(json.dumps({k:d.render_page(k) for k in d.TEXT}))',
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

async function check(lang) {
  const page = pages[lang];
  if (lang === 'en') assert.doesNotMatch(page, /[\u3400-\u9fff]/);
  const elements = Object.fromEntries(['mood-image','mood','value','meta','updated','fill','refresh'].map(id => [id, {
    hidden: true, textContent: '', style: {}, classList: {add() {}, remove() {}},
    getAttribute(name) {return this[name];}, addEventListener(event, callback) {this[event] = callback;},
  }]));
  let payload = {left_percent: 72.4, spent_points: 230.46, cap_points: 835};
  let failure = null;
  let interval;
  const context = vm.createContext({
    document: {getElementById(id) {assert.ok(elements[id], id); return elements[id];}},
    setInterval(callback, delay) {assert.equal(delay, 10000); interval = callback;},
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
  const script = page.match(/<script>([\s\S]*?)<\/script>/)[1];
  vm.runInContext(script, context);
  await new Promise(setImmediate); // Let the initial async refresh finish.
  assert.equal(elements.value.textContent, '72.40%');
  assert.equal(elements.meta.textContent, lang === 'en' ? 'Used 230.46 / 835 points' : '已用 230.46 / 835 点');
  assert.ok(elements.updated.textContent.startsWith(lang === 'en' ? 'Updated: ' : '最近刷新：'));
  assert.equal(typeof elements.refresh.click, 'function');
  assert.equal(typeof interval, 'function');

  for (const [percent, file] of cases) {
    payload = {...payload, left_percent: percent};
    await elements.refresh.click();
    assert.equal(elements['mood-image'].src, '/assets/' + file, `Wrong image at ${percent}%`);
    assert.equal(elements.mood.hidden, false);
    assert.ok(elements['mood-image'].alt.length > 0);
    if (lang === 'en') assert.doesNotMatch(elements['mood-image'].alt, /[\u3400-\u9fff]/);
    assert.equal(elements.fill.style.width, percent + '%');
  }
  for (const mode of ['network', 'http', 'json', 'data']) {
    failure = mode;
    if (mode === 'data') payload = {...payload, left_percent: null};
    await interval();
    assert.equal(elements.value.textContent, lang === 'en' ? 'Unavailable' : '不可用');
    assert.equal(elements.mood.hidden, true);
    assert.equal(elements.fill.style.width, '0%');
    assert.doesNotMatch(elements.meta.textContent, /untranslated/);
    if (lang === 'en') assert.doesNotMatch(elements.meta.textContent, /[\u3400-\u9fff]/);
  }
  failure = null;
  payload = {...payload, left_percent: 100};
  await interval();
  assert.equal(elements.value.textContent, '100.00%');
  assert.equal(elements.mood.hidden, false);
}

(async () => {
  for (const lang of ['en', 'zh']) await check(lang);
  console.log('Passed 2 languages: initial render, 18 mood transitions, 8 error paths, refresh and recovery.');
})().catch(error => {console.error(error); process.exitCode = 1;});
