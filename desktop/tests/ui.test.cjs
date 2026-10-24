'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const read = file => fs.readFile(path.join(__dirname, '..', file), 'utf8');

test('chat shell exposes appearance CSS, custom background, global and per-page opacity controls', async () => {
  const [html, css, js, main, preload] = await Promise.all([read('chat.html'), read('chat.css'), read('chat.js'), read('main.cjs'), read('desktop-preload.cjs')]);
  assert.deepEqual([...html.matchAll(/class="nav-item[^\"]*" data-page="([^"]+)"/g)].map(match => match[1]),
    ['chat', 'activity', 'memory', 'more']);
  assert.match(html, /自主活动：<b id="header-autonomy-status">/);
  assert.match(html, /id="header-next-wake"/);
  assert.match(html, /id="service-status" hidden/);
  assert.match(html, /id="window-opacity" type="range"/);
  for (const page of ['chat', 'activity', 'memory', 'more']) assert.match(html, new RegExp(`id="${page}-opacity" type="range"`));
  assert.match(html, /id="bubble-opacity" type="range"/);
  assert.match(html, /id="background-opacity" type="range"/);
  assert.match(html, /id="custom-css"/);
  assert.match(html, /id="choose-background"/);
  assert.match(html, /id="activity-history"/);
  assert.match(html, /id="memory-list"/);
  assert.match(css, /background:transparent/);
  assert.match(css, /--window-alpha:\.88/);
  assert.match(css, /--bubble-alpha:\.9/);
  assert.match(css, /:root\[data-theme="light"\][^}]*background:transparent/);
  assert.match(css, /:root\[data-theme="light"\][^}]*--panel-2:rgb\(var\(--surface-2-rgb\)\/var\(--window-alpha\)\)/);
  assert.match(css, /#window-background/);
  assert.match(js, /localStorage\.setItem\(APPEARANCE_KEY/);
  assert.match(js, /setProperty\('--window-alpha'/);
  assert.match(js, /setProperty\('--bubble-alpha'/);
  assert.ok(js.includes('@import\\b|url\\s*\\('));
  assert.match(js, /pageOpacity\[page\]=appearance\.windowOpacity/);
  assert.match(main, /chat:choose-background/);
  assert.match(main, /8 \* 1024 \* 1024/);
  assert.match(preload, /chooseBackground:.*chat:choose-background/);
  assert.match(html, /style-src 'self' 'nonce-veil-custom-css'/);
});

test('daily memory view reads Ombre catalog and exact records without the legacy /memory command', async () => {
  const [html, main, web, native] = await Promise.all([
    read('chat.html'), read('main.cjs'), fs.readFile(path.join(__dirname, '..', '..', 'web_agent.py'), 'utf8'),
    fs.readFile(path.join(__dirname, '..', '..', 'memory_native.py'), 'utf8')
  ]);
  assert.match(html, /来自 Ombre Brain 的真实记忆/);
  assert.match(main, /\/api\/memory\/catalog/);
  assert.match(main, /\/api\/memory\/read/);
  assert.match(web, /memory_catalog/);
  assert.match(web, /memory_read/);
  assert.match(native, /def _visible_records\(/);
  assert.doesNotMatch(html, /data-cmd="\/memory"/);
  assert.match(html, /高级管理 · Ombre Dashboard/);
});

test('pet provides four continuous corner resize handles and preserves its square bounds', async () => {
  const [html, css, main] = await Promise.all([read('pet.html'), read('pet.css'), read('main.cjs')]);
  assert.equal([...html.matchAll(/class="resize (?:nw|ne|sw|se)"/g)].length, 4);
  assert.match(css, /\.resize\.nw/);
  assert.match(css, /\.resize\.se/);
  assert.match(main, /width: size, height: size/);
  assert.match(main, /petResize = \{ cursor:/);
});

test('every existing tab detaches into the shared chat view and can merge without a second renderer implementation', async () => {
  const [html, css, js, main, preload] = await Promise.all([
    read('chat.html'), read('chat.css'), read('chat.js'), read('main.cjs'), read('desktop-preload.cjs')
  ]);
  assert.match(html, /id="activity-tab"/);
  assert.match(html, /data-activity-filter="memory"/);
  assert.match(html, /data-activity-filter="long_task"/);
  assert.match(html, /id="merge-activity"/);
  assert.match(css, /body\.activity-window/);
  assert.match(js, /detachedView=pageQuery\.get\('detached'\)==='1'\?pageQuery\.get\('view'\):null/);
  assert.match(js, /setPointerCapture/);
  for (const view of ['chat', 'activity', 'memory', 'more']) assert.match(main, new RegExp(`['"]${view}['"]`));
  assert.match(main, /const viewURL = view => `\$\{chatURL\}\?view=\$\{view\}&detached=1`/);
  assert.match(main, /settings\.detachedBounds/);
  assert.match(main, /settings\.detachedViews/);
  assert.match(main, /current\.showInactive\(\)/);
  assert.match(preload, /detachView:.*view:detach/);
  assert.match(preload, /mergeView:.*view:merge/);
  assert.doesNotMatch(main, /activity\.html/);
});

test('activity polling preserves expanded rows and restored detached windows remain usable', async () => {
  const [js, main] = await Promise.all([read('chat.js'), read('main.cjs')]);
  assert.match(js, /const activityRowNodes=new Map\(\)/);
  assert.match(js, /activityRowNodes\.get\(key\)/);
  assert.match(js, /list\.insertBefore\(row\.details,cursor\)/);
  assert.doesNotMatch(js, /const list=\$\('activity-timeline'\);list\.replaceChildren\(\)/);
  assert.match(main, /async function showActivity\(\)[\s\S]*?await toggleChat\(\)[\s\S]*?detachedWindows\.get\('activity'\)[\s\S]*?restored\.focus\(\)/);
  assert.match(main, /async function showAppearance\(\)[\s\S]*?await toggleChat\(\)[\s\S]*?detachedWindows\.get\('more'\)[\s\S]*?restored\.focus\(\)/);
  assert.match(main, /async function reconnectChat\(event\)[\s\S]*?fromChat\(event\) \|\| detachedViewFromEvent\(event\) === 'chat'/);
});
