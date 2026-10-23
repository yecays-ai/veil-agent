'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const read = file => fs.readFile(path.join(__dirname, '..', file), 'utf8');

test('light and dark theme switch stays visible in the chat title bar and remembers the choice', async () => {
  const [html, css, js] = await Promise.all([read('chat.html'), read('chat.css'), read('chat.js')]);
  const header = html.match(/<header class="topbar">([\s\S]*?)<\/header>/)?.[1] || '';
  const tools = html.match(/<section id="tool-panel"[\s\S]*?<\/section>/)?.[0] || '';
  assert.match(header, /id="theme"[^>]*>浅色<\/button>/);
  assert.doesNotMatch(tools, /id="theme"/);
  assert.match(css, /:root\[data-theme="light"\][^{]*\{[^}]*background:#f5f1ec/);
  assert.match(css, /\.top-actions #theme\{/);
  assert.match(js, /localStorage\.getItem\('veil-theme'\)\|\|'light'/);
  assert.match(js, /localStorage\.setItem\('veil-theme',t\)/);
});
