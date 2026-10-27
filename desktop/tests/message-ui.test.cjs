'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'chat.js'), 'utf8');
function functionSource(start, end) {
  return source.slice(source.indexOf(start), source.indexOf(end));
}
const sandbox = {};
vm.runInNewContext(functionSource('function esc(s)', 'function formatVoiceTime(') +
  functionSource('function memoryDisplay(raw)', 'async function showMessageMemory(') +
  '\nthis.render=render; this.memoryDisplay=memoryDisplay;', sandbox);

test('quote displays the referenced text as a bubble and keeps the answer separate', () => {
  const html = sandbox.render('> Peter：\n> First line\n> Second <line>\n\nmy reply');
  assert.match(html, /chat-quote-preview/);
  assert.match(html, /First line\nSecond &lt;line&gt;/);
  assert.match(html, /<\/span><\/span>my reply$/);
});

test('memory cards use content, creation time and at most three tags, not raw metadata', () => {
  const result = sandbox.memoryDisplay('---\ncreated: 2026-10-26T12:30:00+08:00\ntags:\n- one\n- two\n- three\n- four\nimportance: 5\n---\nFictional memory body');
  assert.equal(result.body, 'Fictional memory body');
  assert.equal(result.tags.join(','), 'one,two,three');
  assert.ok(result.date);
  assert.equal(sandbox.memoryDisplay('Plain memory').body, 'Plain memory');
});
