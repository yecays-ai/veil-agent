'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const read = file => fs.readFile(path.join(__dirname, '..', file), 'utf8');

test('sticker grid keeps its image rows visible inside the scrolling panel', async () => {
  const [css, js] = await Promise.all([read('desktop-visual-system.css'), read('chat.js')]);
  assert.match(css, /\.composer \.sticker-grid\{[^}]*flex:0 0 auto[^}]*overflow:visible/);
  assert.match(css, /\.composer \.sticker-grid\{[^}]*gap:10px/);
  assert.match(css, /\.composer-tray[^}]*max-height:min\(40vh,320px\)[^}]*overflow-y:auto/);
  assert.match(css, /\.composer \.sticker-cell img\{flex:none;width:56px;height:56px/);
  assert.match(js, /const label=document\.createElement\('span'\);label\.textContent=s\.name;\s*tile\.append\(img,label\)/);
});

test('chat shell exposes appearance CSS, custom background, global and per-page opacity controls', async () => {
  const [html, css, js, main, preload] = await Promise.all([read('chat.html'), read('chat.css'), read('chat.js'), read('main.cjs'), read('desktop-preload.cjs')]);
  assert.deepEqual([...html.matchAll(/class="nav-item[^\"]*" data-page="([^"]+)"/g)].map(match => match[1]),
    ['chat', 'activity', 'context', 'memory', 'life', 'mcp', 'more']);
  assert.match(html, /自主活动：<b id="header-autonomy-status">/);
  assert.match(html, /id="header-next-wake"/);
  assert.match(html, /id="service-status" hidden/);
  assert.match(html, /id="window-opacity" type="range"/);
  for (const page of ['chat', 'activity', 'context', 'memory', 'mcp', 'more']) assert.match(html, new RegExp(`id="${page}-opacity" type="range"`));
  assert.match(html, /id="bubble-opacity" type="range"/);
  assert.match(html, /id="background-opacity" type="range"/);
  assert.match(html, /id="custom-css"/);
  assert.match(html, /id="choose-background"/);
  assert.match(html, /id="activity-history"/);
  assert.match(html, /data-page-view="life"/);
  assert.match(js, /api\('\/api\/life'/);
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
  assert.match(html, /data-memory-group="recent"/);
  assert.match(main, /\/api\/memory\/panel/);
  assert.match(main, /'\/api\/memory\/edit', '\/api\/memory\/delete'/);
  assert.match(main, /\/api\/memory\/catalog/);
  assert.match(main, /\/api\/memory\/read/);
  assert.match(web, /panel_entries/);
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
  for (const view of ['chat', 'activity', 'context', 'memory', 'more']) assert.match(main, new RegExp(`['"]${view}['"]`));
  assert.match(main, /const viewURL = view => `\$\{chatURL\}\?view=\$\{view\}&detached=1`/);
  assert.match(main, /settings\.detachedBounds/);
  assert.match(main, /settings\.detachedViews/);
  assert.match(main, /current\.showInactive\(\)/);
  assert.match(preload, /detachView:.*view:detach/);
  assert.match(preload, /mergeView:.*view:merge/);
  assert.doesNotMatch(main, /activity\.html/);
});

test('desktop redesign keeps real pages in one shell with a collapsible draggable sidebar and live rail', async () => {
  const [html, css, js, visual] = await Promise.all([read('chat.html'), read('desktop-redesign.css'), read('chat.js'), read('desktop-visual-system.css')]);
  assert.match(html, /href="desktop-redesign\.css"/);
  assert.match(html, /href="desktop-visual-system\.css"/);
  assert.match(html, /id="sidebar-toggle"/);
  assert.match(html, /id="rail-toggle"/);
  assert.match(html, /id="desktop-rail"/);
  assert.match(html, /id="rail-approval"/);
  assert.match(html, /<h1>Peter<\/h1>/);
  assert.match(html, /data-page="chat"[^>]*><span data-lucide="message-square"><\/span><span class="nav-label">聊天<\/span>/);
  assert.match(html, /data-memory-group="system">系统和能力<\/button><button id="memory-reload"/);
  assert.doesNotMatch(html, /和 Peter 聊聊/);
  assert.match(css, /grid-template-columns:clamp\(176px,17vw,232px\) minmax\(0,1fr\) clamp\(250px,21vw,304px\)/);
  assert.match(css, /body\.detached-window \.topbar,body\.detached-window \.navigation,body\.detached-window \.desktop-rail/);
  assert.match(visual, /--veil-canvas:#fbf8f3/);
  assert.match(visual, /--veil-accent:#596f5b/);
  assert.match(visual, /body:not\(\.detached-window\)\{[^}]*background:rgb\(var\(--veil-canvas-rgb\)\/var\(--window-alpha\)\)/);
  assert.match(visual, /body:not\(\.detached-window\) \.topbar\{[^}]*background:rgb\(var\(--surface-rgb\)\/var\(--window-alpha\)\)/);
  assert.match(visual, /body:not\(\.detached-window\) \.page\{[^}]*background:rgb\(var\(--veil-canvas-rgb\)\/var\(--view-alpha,var\(--window-alpha\)\)\)/);
  assert.match(visual, /\.desktop-rail\{[^}]*background:rgb\(var\(--veil-rail-rgb\)\/var\(--window-alpha\)\)/);
  assert.match(visual, /body\.rail-visible:not\(\.detached-window\) \.desktop-rail\{display:flex!important\}/);
  assert.match(visual, /body:not\(\.detached-window\) \.navigation\{position:static;inset:auto;transform:none;grid-column:1;grid-row:1\/4/);
  assert.match(visual, /body\.sidebar-collapsed \.navigation \.nav-item>\[data-lucide\]\{width:100%;height:100%;display:grid;place-items:center\}/);
  assert.doesNotMatch(visual, /#life-view\{padding-left:/);
  assert.match(visual, /\.life-filter\{gap:3px;padding:0;border:0/);
  assert.match(visual, /grid-template-columns:208px minmax\(0,1fr\);grid-template-rows:64px/);
  assert.match(visual, /\.desktop-rail\{position:static;grid-column:3;grid-row:1\/4/);
  assert.match(css, /body\.detached-window\[data-current-page="chat"\] \.chat-input-shell\{grid-column:2;position:relative;display:flex/);
  assert.match(css, /body:not\(\.detached-window\) \.chat-input-shell\{grid-column:2;position:relative;display:flex/);
  assert.match(css, /grid-template-columns:36px minmax\(0,1fr\) 38px/);
  assert.match(css, /body\.detached-window\[data-current-page="chat"\] #voice-hold\{position:absolute;right:2px;bottom:1px;width:32px;height:32px;min-width:32px\}/);
  assert.match(css, /body:not\(\.detached-window\) #voice-hold\{position:absolute;right:2px;bottom:1px;width:32px;height:32px;min-width:32px\}/);
  assert.match(css, /#send\{grid-column:3;width:38px;height:38px/);
  assert.match(css, /\.voice-tools\{display:none;position:absolute;z-index:12/);
  assert.match(css, /#request-reply\{display:none;position:absolute;z-index:12;left:136px/);
  assert.match(css, /\.veil-select-menu\.open\{display:block\}/);
  assert.match(js, /function enhanceVeilSelects\(root=document\)/);
  assert.match(js, /select\.dispatchEvent\(new Event\('change',\{bubbles:true\}\)\)/);
  assert.equal([...html.matchAll(/<select\b/g)].length, 8, 'all static dropdowns are accounted for');
  assert.match(js, /id="lf-kind"[\s\S]*?id="lf-owner"[\s\S]*?id="lf-repeat"/);
  assert.match(js, /x\.date===key\|\|x\.kind==='note'&&x\.repeat==='daily'\)\)\.length/);
  assert.match(visual, /\.life-records\.life-overview\{display:grid;grid-template-columns:minmax\(0,1fr\)[^}]*width:100%;max-width:none/);
  assert.match(css, /\.life-detail\{[^}]*background:rgb\(var\(--veil-canvas-rgb\)\/var\(--view-alpha,var\(--window-alpha\)\)\)!important/);
  assert.match(css, /#life-view \.life-sticky\{padding:18px 0 8px;[^}]*font:15px\/1\.5/);
  assert.match(await read('screen-pop.css'), /border-radius: 12px; overflow: hidden/);
  assert.doesNotMatch(css, /Segoe Print/);
  assert.match(js, /veil-sidebar-collapsed/);
  assert.match(js, /veil-rail-visible/);
  assert.match(js, /railToggle\.onclick/);
  assert.match(js, /railToggle\.firstElementChild\.dataset\.lucide/);
  assert.match(js, /function setComposerPanel\(panel\)/);
  assert.match(js, /composerTray\.hidden=panel!=='plus';stickerPanel\.hidden=panel!=='stickers'/);
  assert.match(js, /new MutationObserver\(syncDesktopRail\)/);
  assert.match(js, /const outside=event\.clientX<0\|\|event\.clientY<0\|\|event\.clientX>innerWidth\|\|event\.clientY>innerHeight/);
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

test('screen approval remains a compact fixed-size window with scrollable long text', async () => {
  const [main, css] = await Promise.all([read('main.cjs'), read('screen-pop.css')]);
  assert.match(main, /const width = Math\.min\(520, area\.width\), height = Math\.min\(270, area\.height\)/);
  assert.match(css, /\.popup-content p\s*\{[^}]*max-height:\s*100%;[^}]*overflow:\s*auto/);
  assert.match(css, /\.popup-actions\s*\{\s*height:\s*52px/);
});

test('life overview uses a balanced month grid, round record markers, and a selected-day agenda', async () => {
  const [html, js, css] = await Promise.all([read('chat.html'), read('chat.js'), read('desktop-visual-system.css')]);
  assert.match(html, /data-life-kind="all" class="active">总览/);
  assert.match(js, /className='calendar-today'.*?lifeDay=lifeToday\(\)/);
  assert.match(js, /className='life-agenda'/);
  assert.match(js, /calendar-day-number/);
  assert.match(js, /calendar-day-marker/);
  assert.match(js, /agenda-owner/);
  assert.match(css, /life-records\.life-overview\{display:grid;grid-template-columns:minmax\(0,1fr\)[^}]*width:100%;max-width:none[^}]*border:0;[^}]*background:transparent/);
  assert.match(css, /@container \(min-width:800px\)\{\.life-records\.life-overview\{grid-template-columns:minmax\(360px,460px\) minmax\(0,1fr\);gap:26px\}\}/);
  assert.match(css, /\.life-agenda\{[^}]*max-height:[^}]*overflow:auto[^}]*grid-template-columns:minmax\(0,1fr\)/);
  assert.match(css, /@container \(min-width:1120px\)\{\.life-agenda\{grid-template-columns:repeat\(2,minmax\(0,1fr\)\);grid-template-rows:auto 1fr;column-gap:28px\}/);
  assert.match(css, /\.life-agenda-row\{[^}]*border:0;border-radius:9px/);
  assert.match(css, /\.life-calendar-days button\{display:flex;[^}]*height:56px/);
  assert.match(css, /\.calendar-day-number\{[^}]*width:32px;height:32px;[^}]*border-radius:50%/);
  assert.match(css, /\.life-calendar-days button \.calendar-day-marker\{display:block;box-sizing:border-box;width:5px;height:5px;min-height:5px;flex:0 0 5px;padding:0;border-radius:50%/);
  assert.match(css, /\.life-calendar-days button\.has-record::after\{display:none!important;content:none!important\}/);
  assert.match(css, /\.agenda-owner\{flex:none;padding:0 4px;color:var\(--veil-soft\);font-size:11px/);
  assert.match(css, /\.agenda-check\{[^}]*border-radius:50%/);
  assert.match(css, /body:not\(\.detached-window\)\{[^}]*border-radius:18px;overflow:hidden/);
  assert.match(css, /body\.detached-window\{border-radius:18px;overflow:hidden\}/);
  assert.match(css, /life-date\[hidden\]\{display:none!important\}/);
});

test('live context view shows request activity above short-term notes and can be detached read-only', async () => {
  const [html, js, main] = await Promise.all([read('chat.html'), read('chat.js'), read('main.cjs')]);
  assert.match(html, /id="context-view"[\s\S]*?id="context-turns"[\s\S]*?id="context-notes"/);
  assert.match(js, /renderLiveContext\(s\.live_context\)/);
  assert.match(main, /detachedView === 'context' && !body && pathname\?\.startsWith\('\/api\/events\?'\)/);
});

test('settings page has a workbench switch with folder picker, and the chat log shows no scrollbar', async () => {
  const [html, js, main, preload, css] = await Promise.all([read('chat.html'), read('chat.js'), read('main.cjs'), read('desktop-preload.cjs'), read('desktop-visual-system.css')]);
  assert.match(html, /id="workbench-card"[\s\S]*?id="workbench-on"[\s\S]*?id="workbench-off"[\s\S]*?id="workbench-add"/);
  assert.match(js, /api\('\/api\/workbench'/);
  assert.match(js, /window\.veilDesktop\.chooseFolder\(\)/);
  assert.match(preload, /chooseFolder: \(\) => ipcRenderer\.invoke\('chat:choose-folder'\)/);
  assert.match(main, /ipcMain\.handle\('chat:choose-folder'[\s\S]*?openDirectory/);
  assert.match(main, /url\.pathname === '\/api\/workbench'/);
  assert.match(main, /'\/api\/mcp', '\/api\/workbench'/);
  assert.match(css, /#log,\.page\{scrollbar-width:none\}/);
  assert.match(css, /#log::-webkit-scrollbar,\.page::-webkit-scrollbar\{display:none/);
});
