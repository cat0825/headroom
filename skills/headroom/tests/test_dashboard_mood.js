// Test the actual dashboard function without touching the ledger or a server.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../scripts/headroom_dashboard.py'), 'utf8');
const match = source.match(/function updateMood\(percent\)\{[\s\S]*?\n\}/);
assert.ok(match, 'Dashboard must define updateMood');
const picture = {getAttribute(name) { return this[name]; }};
const mood = {hidden: true};
const context = vm.createContext({document: {getElementById(id) {
  if (id === 'mood-image') return picture;
  if (id === 'mood') return mood;
  throw new Error(`Unexpected element: ${id}`);
}}});
vm.runInContext(match[0], context);

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
for (const [percent, file] of cases) {
  context.percent = percent;
  vm.runInContext('updateMood(percent)', context);
  assert.equal(picture.src, '/assets/' + file, `Wrong image at ${percent}%`);
  assert.equal(mood.hidden, false);
  assert.ok(picture.alt.length > 0);
}
console.log(`Passed ${cases.length} dashboard mood threshold/transition cases.`);
