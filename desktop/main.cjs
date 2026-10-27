'use strict';
const { app, BrowserWindow, ipcMain, dialog, screen, shell } = require('electron');
const path = require('node:path');
const fs = require('node:fs/promises');
const { randomUUID } = require('node:crypto');
const { pathToFileURL } = require('node:url');
const { connect, validRoot, readEndpoint } = require('./backend.cjs');

let pet, chat, menu, connecting = false, quitting = false, drag, petResize, agentEndpoint, saveTimer, saveChain = Promise.resolve();
const detachedWindows = new Map(), mergingViews = new Set();
const screenPopWindows = new Map(), screenPopInFlight = new Set();
let screenPopPollTimer = null, screenPopPolling = false, quitFlushStarted = false;
const VIEW_NAMES = new Set(['chat', 'activity', 'context', 'memory', 'more']);
const settings = { size: 320, theme: 'light' };
const petURL = pathToFileURL(path.join(__dirname, 'pet.html')).href;
const chatURL = pathToFileURL(path.join(__dirname, 'chat.html')).href;
const screenPopURL = pathToFileURL(path.join(__dirname, 'screen-pop.html')).href;
const viewURL = view => `${chatURL}?view=${view}&detached=1`;
const menuURL = pathToFileURL(path.join(__dirname, 'menu.html')).href;
function canRequestChatMedia(contents, permission, details, currentChat = chat) {
  const audioOnly = Array.isArray(details?.mediaTypes) && details.mediaTypes.length === 1 && details.mediaTypes[0] === 'audio';
  const detachedChat = detachedWindows.get('chat');
  const allowedWindow = (currentChat && !currentChat.isDestroyed() && contents === currentChat.webContents && details?.requestingUrl === chatURL) ||
    (detachedChat && !detachedChat.isDestroyed() && contents === detachedChat.webContents && details?.requestingUrl === viewURL('chat'));
  return Boolean(allowedWindow && permission === 'media' && audioOnly);
}
function canCheckChatMedia(contents, permission, details, currentChat = chat) {
  const detachedChat = detachedWindows.get('chat');
  const allowedWindow = (currentChat && !currentChat.isDestroyed() && contents === currentChat.webContents && details?.requestingUrl === chatURL) ||
    (detachedChat && !detachedChat.isDestroyed() && contents === detachedChat.webContents && details?.requestingUrl === viewURL('chat'));
  return Boolean(allowedWindow && permission === 'media' && details?.mediaType === 'audio' && details?.isMainFrame === true);
}
const runtimeDataPath = app.isPackaged
  ? path.join(app.getPath('appData'), 'VEIL Desktop')
  : path.join(__dirname, '.runtime-data');
app.setPath('userData', runtimeDataPath);
app.setPath('sessionData', path.join(runtimeDataPath, 'session-data'));
const settingsPath = () => path.join(app.getPath('userData'), 'desktop.json');

async function save() {
  const write = saveChain.then(async () => {
    if (pet && !pet.isDestroyed()) Object.assign(settings, pet.getBounds());
    await fs.mkdir(app.getPath('userData'), { recursive: true });
    await fs.writeFile(settingsPath(), JSON.stringify(settings), 'utf8');
  });
  saveChain = write.catch(() => {});
  return write;
}
function saveSoon() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => save().catch(() => {}), 180);
}
function safeContents(win, allowed) {
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.webContents.on('will-navigate', (event, url) => { if (!allowed(url)) event.preventDefault(); });
  win.webContents.on('will-redirect', (event, url) => { if (!allowed(url)) event.preventDefault(); });
  win.webContents.on('will-attach-webview', event => event.preventDefault());
}
function workAreaAt(x, y) { return screen.getDisplayNearestPoint({ x, y }).workArea; }
function clampPosition(x, y, width, height, area) {
  return { x: Math.round(Math.max(area.x, Math.min(x, area.x + area.width - width))),
    y: Math.round(Math.max(area.y, Math.min(y, area.y + area.height - height))) };
}
function visibleBounds(size, position = settings) {
  const area = workAreaAt(Number.isFinite(position.x) ? position.x : 0, Number.isFinite(position.y) ? position.y : 0);
  const x = position.x ?? area.x + area.width - size - 24;
  const y = position.y ?? area.y + area.height - size - 24;
  return { ...clampPosition(x, y, size, size, area), width: size, height: size };
}
function positionChat() {
  if (!pet || pet.isDestroyed() || !chat || chat.isDestroyed()) return;
  const p = pet.getBounds(); const c = chat.getBounds(); const area = workAreaAt(p.x, p.y);
  const right = p.x + p.width + 12;
  const left = p.x - c.width - 12;
  const x = right + c.width <= area.x + area.width ? right : Math.max(area.x, left);
  const y = Math.max(area.y, Math.min(p.y + p.height / 2 - c.height / 2, area.y + area.height - c.height));
  chat.setPosition(...Object.values(clampPosition(x, y, c.width, c.height, area)));
}
function detachedViewNames() {
  return [...detachedWindows].filter(([, win]) => win && !win.isDestroyed()).map(([view]) => view);
}
function notifyDetachedViews() {
  const views = detachedViewNames();
  for (const win of [chat, ...detachedWindows.values()]) if (win && !win.isDestroyed() && !win.webContents.isLoading())
    win.webContents.send('view:states', views);
}
function restoredWindowBounds(saved, fallback, minimum) {
  const restored = saved && ['x', 'y', 'width', 'height'].every(key => Number.isFinite(saved[key]));
  const bounds = restored ? { ...saved, width: Math.max(minimum.width, Math.round(saved.width)),
    height: Math.max(minimum.height, Math.round(saved.height)) } : { ...fallback };
  const area = workAreaAt(bounds.x || 0, bounds.y || 0);
  bounds.width = Math.min(bounds.width, area.width); bounds.height = Math.min(bounds.height, area.height);
  return { restored, bounds: { ...clampPosition(bounds.x ?? area.x, bounds.y ?? area.y, bounds.width, bounds.height, area),
    width: bounds.width, height: bounds.height } };
}
async function createDetachedWindow(view, point) {
  if (!VIEW_NAMES.has(view)) return false;
  const existing = detachedWindows.get(view);
  if (existing && !existing.isDestroyed()) { existing.showInactive(); return true; }
  if (!agentEndpoint) return false;
  const fallbackSize = view === 'chat' ? { width: 520, height: 680 } : { width: 430, height: 680 };
  const fallback = { x: Number.isFinite(point?.x) ? Math.round(point.x - 170) : 80,
    y: Number.isFinite(point?.y) ? Math.round(point.y - 35) : 80, ...fallbackSize };
  const saved = settings.detachedBounds?.[view];
  const restored = restoredWindowBounds(saved, fallback, { width: 340, height: 420 });
  const current = new BrowserWindow({ ...restored.bounds, minWidth: 340, minHeight: 420, frame: false,
    transparent: true, backgroundColor: '#00000000', resizable: true, hasShadow: true,
    alwaysOnTop: true, skipTaskbar: true, title: `Peter · ${view}`, show: false,
    webPreferences: { preload: path.join(__dirname, 'desktop-preload.cjs'), contextIsolation: true,
      nodeIntegration: false, sandbox: true } });
  detachedWindows.set(view, current); safeContents(current, url => url === viewURL(view));
  const rememberBounds = () => { settings.detachedBounds ||= {}; settings.detachedBounds[view] = current.getBounds(); saveSoon(); };
  current.on('move', rememberBounds); current.on('resize', rememberBounds);
  current.on('closed', () => {
    if (detachedWindows.get(view) === current) detachedWindows.delete(view);
    if (!quitting && !mergingViews.has(view)) {
      settings.detachedViews = (settings.detachedViews || []).filter(item => item !== view); saveSoon(); notifyDetachedViews();
    }
  });
  await current.loadURL(viewURL(view)); settings.detachedViews = [...new Set([...(settings.detachedViews || []), view])];
  rememberBounds(); current.showInactive(); notifyDetachedViews(); return true;
}
async function mergeDetachedWindow(view) {
  if (!VIEW_NAMES.has(view)) return false;
  settings.detachedViews = (settings.detachedViews || []).filter(item => item !== view); mergingViews.add(view);
  try { const win = detachedWindows.get(view); if (win && !win.isDestroyed()) win.destroy(); }
  finally { mergingViews.delete(view); await save().catch(() => {}); }
  notifyDetachedViews();
  if (chat && !chat.isDestroyed()) {
    chat.show(); chat.webContents.send(view === 'activity' ? 'desktop:show-activity' : view === 'more' ? 'desktop:show-settings' : 'desktop:show-view', view);
  }
  return true;
}
function createPet(size = settings.size) {
  size = Number.isFinite(size) ? Math.max(160, Math.min(720, Math.round(size))) : 320;
  const old = pet; const previous = old && !old.isDestroyed() ? old.getBounds() : settings;
  const bounds = visibleBounds(size, previous);
  settings.size = size;
  pet = new BrowserWindow({ ...bounds, frame: false, transparent: true, backgroundColor: '#00000000', resizable: false,
    maximizable: false, minimizable: false, hasShadow: false, alwaysOnTop: true,
    skipTaskbar: true, show: false, webPreferences: { preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true, nodeIntegration: false, sandbox: true } });
  const current = pet; safeContents(current, url => url === petURL);
  current.once('ready-to-show', () => {
    current.showInactive(); if (old && !old.isDestroyed()) old.destroy();
    if (!old && chat && !chat.isDestroyed()) positionChat(); save().catch(() => {});
  });
  current.on('move', saveSoon);
  current.on('resize', () => {
    const [width] = current.getSize(); settings.size = width; saveSoon();
  });
  current.loadURL(petURL); current.on('closed', () => { if (pet === current) pet = null; });
}
async function selectRoot() {
  const choice = await dialog.showOpenDialog({ title: '选择已有的 VEIL Agent 文件夹（里面有 start-web.bat）', properties: ['openDirectory'] });
  if (choice.canceled) return null;
  if (!await validRoot(choice.filePaths[0])) throw new Error('此文件夹尚未安装 Agent。请先在原 Agent 文件夹运行 setup.bat。');
  settings.root = choice.filePaths[0]; await save().catch(() => {}); return settings.root;
}
async function toggleChat() {
  if (chat && !chat.isDestroyed()) { if (chat.isVisible()) chat.hide(); else { chat.show(); chat.focus(); } return; }
  if (connecting) return; connecting = true;
  try {
    let root = settings.root;
    const adjacent = app.isPackaged
      ? path.resolve(path.dirname(process.execPath), '..', '..', '..', 'veil-agent')
      : path.resolve(__dirname, '..', 'veil-agent');
    if (!root && await validRoot(adjacent)) { root = adjacent; settings.root = root; await save().catch(() => {}); }
    if (!root) root = await selectRoot(); if (!root) return;
    agentEndpoint = await connect(root); if (quitting) return;
    const restored = settings.chatBounds && ['x', 'y', 'width', 'height'].every(key => Number.isFinite(settings.chatBounds[key]));
    const chatBounds = restored ? { ...settings.chatBounds,
      width: Math.max(430, Math.round(settings.chatBounds.width)), height: Math.max(480, Math.round(settings.chatBounds.height)) }
      : { x: 0, y: 0, width: 560, height: 700 };
    chat = new BrowserWindow({ ...chatBounds, minWidth: 430, minHeight: 480,
      frame: false, transparent: true, backgroundColor: '#00000000', resizable: true,
      hasShadow: true, alwaysOnTop: true, skipTaskbar: true,
      title: 'VEIL · Peter', show: false,
      webPreferences: { preload: path.join(__dirname, 'desktop-preload.cjs'),
        contextIsolation: true, nodeIntegration: false, sandbox: true } });
    const current = chat; safeContents(current, url => url === chatURL);
    current.on('close', event => { if (!quitting) { event.preventDefault(); current.hide(); } });
    current.on('closed', () => { if (chat === current) chat = null; });
    await current.loadURL(chatURL);
    if (restored) {
      const area = workAreaAt(chatBounds.x, chatBounds.y);
      chatBounds.width = Math.min(chatBounds.width, area.width);
      chatBounds.height = Math.min(chatBounds.height, area.height);
      const position = clampPosition(chatBounds.x, chatBounds.y, chatBounds.width, chatBounds.height, area);
      current.setBounds({ ...position, width: chatBounds.width, height: chatBounds.height });
    } else positionChat();
    settings.chatBounds = current.getBounds(); saveSoon();
    current.on('move', () => { settings.chatBounds = current.getBounds(); saveSoon(); });
    current.on('resize', () => { settings.chatBounds = current.getBounds(); saveSoon(); });
    if (!quitting) { current.show(); current.focus(); }
    notifyDetachedViews();
    for (const view of (Array.isArray(settings.detachedViews) ? settings.detachedViews : []).filter(view => VIEW_NAMES.has(view)))
      await createDetachedWindow(view);
  } catch (error) {
    if (chat && !chat.isDestroyed()) chat.destroy();
    dialog.showErrorBox('VEIL · 暂时无法聊天', error.message);
  } finally { connecting = false; }
}
async function showActivity() {
  const detached = detachedWindows.get('activity');
  if (detached && !detached.isDestroyed()) { detached.show(); detached.focus(); return; }
  if (!chat || chat.isDestroyed()) await toggleChat();
  const restored = detachedWindows.get('activity');
  if (restored && !restored.isDestroyed()) { restored.show(); restored.focus(); return; }
  if (chat && !chat.isDestroyed() && !chat.isVisible()) { chat.show(); chat.focus(); }
  if (chat && !chat.isDestroyed()) chat.webContents.send('desktop:show-activity');
}
async function showAppearance() {
  const detached = detachedWindows.get('more');
  if (detached && !detached.isDestroyed()) { detached.show(); detached.focus(); return; }
  if (!chat || chat.isDestroyed()) await toggleChat();
  const restored = detachedWindows.get('more');
  if (restored && !restored.isDestroyed()) { restored.show(); restored.focus(); return; }
  if (chat && !chat.isDestroyed() && !chat.isVisible()) { chat.show(); chat.focus(); }
  if (chat && !chat.isDestroyed()) chat.webContents.send('desktop:show-settings');
}
async function reconnectChat(event) {
  if (event && !(fromChat(event) || detachedViewFromEvent(event) === 'chat')) return false;
  if (connecting) return false;
  if (chat && !chat.isDestroyed()) chat.destroy();
  chat = null;
  agentEndpoint = null;
  await toggleChat();
  return !!chat && !chat.isDestroyed();
}
function hideMenu() { if (menu && !menu.isDestroyed()) menu.hide(); }
function showMenu() {
  const point = screen.getCursorScreenPoint(); const area = workAreaAt(point.x, point.y);
  const bounds = clampPosition(point.x, point.y, 254, 226, area);
  if (!menu || menu.isDestroyed()) {
    menu = new BrowserWindow({ ...bounds, width: 254, height: 226, frame: false, transparent: true,
      resizable: false, show: false, skipTaskbar: true, alwaysOnTop: true,
      webPreferences: { preload: path.join(__dirname, 'menu-preload.cjs'), contextIsolation: true,
        nodeIntegration: false, sandbox: true } });
    safeContents(menu, url => url === menuURL); menu.on('blur', hideMenu);
    menu.on('closed', () => { menu = null; });
    menu.once('ready-to-show', () => { if (menu && !menu.isDestroyed()) { menu.webContents.send('menu:theme', settings.theme); menu.show(); menu.focus(); } });
    menu.loadURL(menuURL);
  } else { menu.setBounds({ ...bounds, width: 254, height: 226 }); }
  if (!menu.webContents.isLoading()) { menu.webContents.send('menu:theme', settings.theme); menu.show(); menu.focus(); }
}
async function endpointRequest(pathname, method = 'GET', body) {
  if (!agentEndpoint) throw new Error('Agent 尚未连接。');
  const token = new URL(agentEndpoint.url).searchParams.get('t');
  const response = await fetch(`${agentEndpoint.origin}${pathname}`, { method, headers: {
    'X-Token': token, ...(body ? { 'Content-Type': 'application/json' } : {})
  }, body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(5000) });
  const data = await response.json().catch(() => ({})); if (!response.ok) { const error = new Error(data.error || '请求失败'); error.status = response.status; throw error; } return data;
}
function screenPopWindow(bounds, type, name) {
  const win = new BrowserWindow({ ...bounds, frame: false, transparent: true,
    backgroundColor: '#00000000', resizable: false, movable: false,
    minimizable: false, maximizable: false, hasShadow: false,
    alwaysOnTop: true, skipTaskbar: true, focusable: type === 'popup',
    fullscreenable: false, show: false, title: name || 'Peter',
    webPreferences: { preload: path.join(__dirname, 'screen-pop-preload.cjs'),
      contextIsolation: true, nodeIntegration: false, sandbox: true } });
  safeContents(win, url => url === screenPopURL);
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  if (type === 'danmaku') win.setIgnoreMouseEvents(true, { forward: true });
  return win;
}
async function postScreenPopEvent(record, event, item, total) {
  const body = { id: record.id, event };
  if (event === 'shown') Object.assign(body, { style: record.type, total });
  else body.item = item;
  for (let attempt = 0; attempt < 3; attempt++) {
    try { await endpointRequest('/api/screen-pop/event', 'POST', body); return true; }
    catch (error) {
      // The agent no longer knows this pop (for example after a restart): stop retrying.
      if (error?.status === 400 || error?.status === 409) return 'rejected';
      if (attempt < 2) await new Promise(resolve => setTimeout(resolve, 150 * (2 ** attempt)));
    }
  }
  return false;
}
function forgetScreenPop(record) {
  if (screenPopWindows.get(record.id) === record) screenPopWindows.delete(record.id);
}
function suppressAndCloseScreenPop(record) {
  record.suppress = true;
  for (const item of record.windows) if (!item.window.isDestroyed()) item.window.destroy();
  forgetScreenPop(record);
}
function screenPopWindowClosed(record, index) {
  if (record.suppress || !record.acknowledged || record.type !== 'popup' || record.dismissed.has(index)) return;
  if (record.dismissalInFlight.has(index)) return;
  record.dismissalInFlight.add(index);
  void postScreenPopEvent(record, 'dismissed', index).then(ok => {
    if (ok) {
      record.dismissed.add(index);
      if (record.dismissed.size >= record.total) forgetScreenPop(record);
    } else if (!record.suppress && !quitting) {
      setTimeout(() => screenPopWindowClosed(record, index), 1000);
    }
  }).finally(() => record.dismissalInFlight.delete(index));
}
async function prepareScreenPopWindow(record, win, payload, index) {
  const item = { window: win, index, readyResolve: null };
  item.ready = new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('screen-pop renderer did not become ready')), 5000);
    item.readyResolve = () => { clearTimeout(timeout); resolve(); };
    win.once('closed', () => { clearTimeout(timeout); reject(new Error('screen-pop window closed before rendering')); });
  });
  record.windows.push(item);
  win.on('closed', () => screenPopWindowClosed(record, index));
  await win.loadURL(screenPopURL);
  win.webContents.send('screen-pop:payload', { ...payload, type: record.type });
  await item.ready;
}
async function showScreenPop(popId) {
  if (screenPopInFlight.has(popId)) return;
  screenPopInFlight.add(popId);
  let record;
  try {
    const claimed = await endpointRequest('/api/screen-pop/claim', 'POST', { id: popId });
    const command = claimed?.command;
    if (!command || command.id !== popId || typeof command.text !== 'string' ||
        command.text.length < 1 || command.text.length > 500 || Buffer.byteLength(command.text, 'utf8') > 2000 ||
        !['popup', 'danmaku'].includes(command.style) ||
        typeof command.name !== 'string' || command.name.length > 100 ||
        !/^#[0-9a-f]{6}$/i.test(command.color || '') ||
        !['scroll', 'fade', 'bounce'].includes(command.animation) ||
        !['top', 'center', 'bottom'].includes(command.position) ||
        !Number.isInteger(command.duration) || command.duration < 1 || command.duration > 20) return;
    if (command.style === 'popup' && (!Number.isInteger(command.count) || command.count < 1 || command.count > 10)) return;
    if (process.platform !== 'win32') return;
    record = { id: popId, type: command.style, total: command.style === 'popup' ? command.count : 0,
      windows: [], dismissed: new Set(), dismissalInFlight: new Set(),
      finished: new Set(), finishedInFlight: new Set(), acknowledged: false, suppress: false };
    screenPopWindows.set(popId, record);
    if (command.style === 'popup') {
      const area = screen.getPrimaryDisplay().workArea;
      const width = Math.min(520, area.width), height = Math.min(270, area.height);
      const step = 24, firstX = area.x + Math.floor((area.width - width - step * (command.count - 1)) / 2);
      const firstY = area.y + Math.floor((area.height - height - step * (command.count - 1)) / 2);
      const wins = Array.from({ length: command.count }, (_, index) => {
        const x = Math.max(area.x, Math.min(firstX + step * index, area.x + area.width - width));
        const y = Math.max(area.y, Math.min(firstY + step * index, area.y + area.height - height));
        return { index: index + 1, window: screenPopWindow({ x, y, width, height }, 'popup', command.name) };
      });
      await Promise.all(wins.map(item => prepareScreenPopWindow(record, item.window, command, item.index)));
      for (const item of record.windows) item.window.showInactive();
      if (await postScreenPopEvent(record, 'shown', undefined, record.total) !== true) {
        suppressAndCloseScreenPop(record); return;
      }
      record.acknowledged = true;
      for (const item of record.windows) item.window.webContents.send('screen-pop:start');
      return;
    }
    const displays = screen.getAllDisplays();
    if (!displays.length || displays.length > 32) { suppressAndCloseScreenPop(record); return; }
    record.total = displays.length;
    const wins = displays.map((display, index) => ({
      index: index + 1,
      window: screenPopWindow(display.bounds, 'danmaku', command.name),
    }));
    await Promise.all(wins.map(item => prepareScreenPopWindow(record, item.window, command, item.index)));
    for (const item of record.windows) item.window.showInactive();
    if (await postScreenPopEvent(record, 'shown', undefined, record.total) !== true) {
      suppressAndCloseScreenPop(record); return;
    }
    record.acknowledged = true;
    for (const item of record.windows) item.window.webContents.send('screen-pop:start');
  } catch {
    if (record) suppressAndCloseScreenPop(record);
  } finally {
    screenPopInFlight.delete(popId);
  }
}
function screenPopRecordFor(event) {
  for (const record of screenPopWindows.values()) {
    for (const item of record.windows) {
      if (item.window && !item.window.isDestroyed() && item.window.webContents === event.sender &&
          event.senderFrame?.url === screenPopURL) return { record, item };
    }
  }
  return null;
}
ipcMain.on('screen-pop:dismiss', event => {
  const found = screenPopRecordFor(event);
  if (found?.record.type === 'popup' && found.record.acknowledged) found.item.window.close();
});
ipcMain.on('screen-pop:ready', event => {
  const found = screenPopRecordFor(event);
  if (found?.item.readyResolve) {
    const resolve = found.item.readyResolve;
    found.item.readyResolve = null;
    resolve();
  }
});
function recordScreenPopFinished(record, item) {
  if (record.suppress || record.finished.has(item.index) || record.finishedInFlight.has(item.index)) return;
  record.finishedInFlight.add(item.index);
  void postScreenPopEvent(record, 'finished', item.index).then(ok => {
    if (ok) {
      record.finished.add(item.index);
      if (!item.window.isDestroyed()) item.window.close();
      if (record.finished.size >= record.total) forgetScreenPop(record);
    } else if (!record.suppress && !quitting) {
      setTimeout(() => recordScreenPopFinished(record, item), 1000);
    }
  }).finally(() => record.finishedInFlight.delete(item.index));
}
ipcMain.on('screen-pop:finished', event => {
  const found = screenPopRecordFor(event);
  if (!found || found.record.type !== 'danmaku' || !found.record.acknowledged ||
      found.record.finished.has(found.item.index)) return;
  recordScreenPopFinished(found.record, found.item);
});
async function discoverRunningAgentForScreenPop() {
  if (agentEndpoint || quitting) return;
  const adjacent = app.isPackaged
    ? path.resolve(path.dirname(process.execPath), '..', '..', '..', 'veil-agent')
    : path.resolve(__dirname, '..', 'veil-agent');
  const root = settings.root || adjacent;
  if (!await validRoot(root)) return;
  agentEndpoint = await readEndpoint(root, { requireReady: false });
}
async function pollScreenPop() {
  if (screenPopPolling || quitting) return;
  screenPopPolling = true;
  try {
    if (!agentEndpoint) await discoverRunningAgentForScreenPop();
    if (!agentEndpoint) return;
    const result = await endpointRequest('/api/screen-pop/next');
    for (const command of Array.isArray(result.commands) ? result.commands : []) {
      if (command && typeof command.id === 'string') void showScreenPop(command.id);
    }
  } catch {
    // Leave agentEndpoint alone: the chat window shares it, and one failed poll must not disconnect chat.
  } finally { screenPopPolling = false; }
}
function startScreenPopPolling() {
  if (screenPopPollTimer) clearInterval(screenPopPollTimer);
  screenPopPollTimer = setInterval(() => { void pollScreenPop(); }, 500);
  void pollScreenPop();
}
async function flushScreenPopWindows() {
  const records = [...screenPopWindows.values()];
  await Promise.all(records.map(async record => {
    if (record.type === 'popup' && record.acknowledged) {
      await Promise.all(record.windows.filter(item => !record.dismissed.has(item.index)).map(async item => {
        if (await postScreenPopEvent(record, 'dismissed', item.index)) record.dismissed.add(item.index);
      }));
    }
    suppressAndCloseScreenPop(record);
  }));
}
const fromPet = event => pet && !pet.isDestroyed() && event.sender === pet.webContents && event.senderFrame?.url === petURL;
const fromChat = event => chat && !chat.isDestroyed() && event.sender === chat.webContents && event.senderFrame?.url === chatURL;
function detachedViewFromEvent(event) {
  for (const [view, win] of detachedWindows) if (win && !win.isDestroyed() && event.sender === win.webContents && event.senderFrame?.url === viewURL(view)) return view;
  return null;
}
const fromChatPage = event => fromChat(event) || detachedViewFromEvent(event) !== null;
function allowedChatRequest(pathname, method) {
  if (method === 'GET') {
    let url;
    try { url = new URL(pathname, 'http://127.0.0.1'); } catch { return false; }
    const keys = [...url.searchParams.keys()];
    if (url.pathname === '/api/events') return keys.length === 1 && keys[0] === 'after' && /^\d+$/.test(url.searchParams.get('after'));
    if (url.pathname === '/api/memory/panel') return keys.length === 0;
    if (url.pathname === '/api/memory/catalog')
      return keys.every(key => key === 'after') && keys.length <= 1 && (!keys.length || /^[A-Za-z0-9_-]{1,100}$/.test(url.searchParams.get('after')));
    if (url.pathname === '/api/memory/read') {
      const id = url.searchParams.get('id'), offset = url.searchParams.get('offset'), version = url.searchParams.get('version');
      return keys.includes('id') && keys.length === new Set(keys).size && keys.every(key => ['id', 'offset', 'version'].includes(key)) &&
        /^[A-Za-z0-9_-]{1,100}$/.test(id || '') && (offset === null || /^\d{1,8}$/.test(offset)) &&
        (version === null || /^[a-f0-9]{64}$/.test(version));
    }
    return false;
  }
  return method === 'POST' && ['/api/send', '/api/media', '/api/voice', '/api/voice/retry',
    '/api/transcribe', '/api/approve', '/api/activity', '/api/glance', '/api/codex-watch', '/api/quiet-hours', '/api/exit',
    '/api/message/action', '/api/memory/edit', '/api/memory/delete', '/api/models', '/api/models/use', '/api/stop'].includes(pathname);
}
ipcMain.handle('chat:request', async (event, pathname, body, timeoutMs) => {
  const detachedView = detachedViewFromEvent(event);
  const detachedAllowed = !detachedView || detachedView === 'chat' ||
    (detachedView === 'activity' && ((!body && pathname?.startsWith('/api/events?')) || (body && ['/api/activity', '/api/glance', '/api/codex-watch', '/api/quiet-hours'].includes(pathname)))) ||
    (detachedView === 'context' && !body && pathname?.startsWith('/api/events?')) ||
    (detachedView === 'memory' && ((!body && (pathname?.startsWith('/api/events?') || pathname?.startsWith('/api/memory/'))) ||
      (body && ['/api/memory/edit', '/api/memory/delete'].includes(pathname)))) ||
    (detachedView === 'more' && ((!body && pathname?.startsWith('/api/events?')) || (body && ['/api/exit', '/api/models', '/api/models/use'].includes(pathname))));
  if (!fromChatPage(event) || typeof pathname !== 'string' || !allowedChatRequest(pathname, body ? 'POST' : 'GET') || !detachedAllowed)
    return { ok: false, status: 403, data: { error: '请求未允许。' } };
  if (body && (typeof body !== 'object' || Array.isArray(body)))
    return { ok: false, status: 400, data: { error: '无效请求。' } };
  try {
    const token = new URL(agentEndpoint.url).searchParams.get('t');
    const response = await fetch(`${agentEndpoint.origin}${pathname}`, {
      method: body ? 'POST' : 'GET', redirect: 'error',
      headers: { 'X-Token': token, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(Math.max(1000, Math.min(120000, Number(timeoutMs) || 30000)))
    });
    const data = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, data };
  } catch (error) { return { ok: false, status: 0, data: { error: error.message || 'Agent 连接中断。' } }; }
});
ipcMain.handle('chat:media-url', (event, filename) => {
  if (!(fromChat(event) || detachedViewFromEvent(event) === 'chat') || !agentEndpoint) throw new Error('请求未允许。');
  if (typeof filename !== 'string' || filename.length > 180 || !/^[A-Za-z0-9_.-]+$/.test(filename) || filename.includes('..'))
    throw new Error('无效的媒体文件名。');
  const token = new URL(agentEndpoint.url).searchParams.get('t');
  return `${agentEndpoint.origin}/media/${encodeURIComponent(filename)}?t=${encodeURIComponent(token)}`;
});
ipcMain.handle('chat:attachment-text', async (event, id) => {
  if (!(fromChat(event) || detachedViewFromEvent(event) === 'chat') || !/^[0-9a-f]{32}$/.test(id || ''))
    throw new Error('请求未允许。');
  const result = await endpointRequest(`/api/attachment-text?id=${encodeURIComponent(id)}`);
  if (typeof result.text !== 'string') throw new Error('无法读取这个文本附件。');
  return result.text;
});
ipcMain.handle('chat:open-attachment', async (event, id) => {
  if (!(fromChat(event) || detachedViewFromEvent(event) === 'chat') || !/^[0-9a-f]{32}$/.test(id || ''))
    throw new Error('请求未允许。');
  const result = await endpointRequest(`/api/attachment?id=${encodeURIComponent(id)}`);
  const attachment = result.attachment;
  if (!attachment || attachment.id !== id || attachment.kind !== 'attachment' ||
      attachment.view !== 'html' || attachment.mime !== 'text/html' ||
      typeof attachment.filename !== 'string' || !/^[0-9a-f]{32}\.[a-z0-9]{1,12}$/.test(attachment.filename) ||
      typeof attachment.name !== 'string' || attachment.name.length > 120 ||
      path.basename(attachment.name) !== attachment.name || attachment.name.includes('\\') ||
      attachment.name.includes('\0') || /[\x00-\x1f\x7f]/.test(attachment.name) ||
      !Number.isSafeInteger(attachment.size) || attachment.size < 1 || attachment.size > 5 * 1024 * 1024)
    throw new Error('附件信息无效。');
  const extension = path.extname(attachment.filename).toLowerCase();
  if (!['.html', '.htm'].includes(extension) || attachment.filename !== `${id}${extension}` ||
      path.extname(attachment.name).toLowerCase() !== extension)
    throw new Error('附件文件名无效。');
  const token = new URL(agentEndpoint.url).searchParams.get('t');
  const mediaUrl = `${agentEndpoint.origin}/media/${encodeURIComponent(id)}?t=${encodeURIComponent(token)}`;
  const response = await fetch(mediaUrl, { redirect: 'error', signal: AbortSignal.timeout(30000) });
  if (!response.ok) throw new Error('附件读取失败。');
  if (!response.body || typeof response.body[Symbol.asyncIterator] !== 'function')
    throw new Error('附件读取失败。');
  const chunks = [];
  let byteCount = 0;
  for await (const chunk of response.body) {
    const bytes = Buffer.from(chunk);
    byteCount += bytes.length;
    if (byteCount > 5 * 1024 * 1024) throw new Error('附件大小超过限制。');
    chunks.push(bytes);
  }
  if (byteCount !== attachment.size)
    throw new Error('附件大小与记录不一致。');
  const bytes = Buffer.concat(chunks, byteCount);
  const attachmentRoot = path.join(app.getPath('userData'), 'attachments');
  await fs.mkdir(attachmentRoot, { recursive: true });
  const rootStat = await fs.lstat(attachmentRoot);
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) throw new Error('附件缓存目录不可用。');
  const attachmentDir = path.join(attachmentRoot, id);
  await fs.mkdir(attachmentDir, { recursive: true });
  const dirStat = await fs.lstat(attachmentDir);
  if (!dirStat.isDirectory() || dirStat.isSymbolicLink()) throw new Error('附件缓存目录不可用。');
  const destination = path.join(attachmentDir, attachment.name);
  const temporary = path.join(attachmentDir, `.${id}.${randomUUID()}.tmp`);
  try {
    await fs.writeFile(temporary, bytes, { flag: 'wx', mode: 0o600 });
    try {
      const existing = await fs.lstat(destination);
      if (!existing.isFile() || existing.isSymbolicLink()) throw new Error('附件缓存文件不可用。');
      await fs.rm(destination);
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
    await fs.rename(temporary, destination);
    const saved = await fs.stat(destination);
    if (!saved.isFile() || saved.size !== bytes.length) throw new Error('附件缓存校验失败。');
    const openError = await shell.openPath(destination);
    if (openError) throw new Error(openError);
    return true;
  } finally {
    await fs.rm(temporary, { force: true }).catch(() => {});
  }
});
const backgroundMime = { '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp' };
function backgroundFile() {
  const ext = typeof settings.backgroundExt === 'string' ? settings.backgroundExt.toLowerCase() : '';
  return backgroundMime[ext] ? { ext, mime: backgroundMime[ext], path: path.join(app.getPath('userData'), `appearance-background${ext}`) } : null;
}
ipcMain.handle('chat:choose-background', async event => {
  if (!(fromChat(event) || detachedViewFromEvent(event) === 'more')) return null;
  const choice = await dialog.showOpenDialog(chat, { title: '选择 VEIL 背景图片', properties: ['openFile'], filters: [{ name: '图片', extensions: ['png', 'jpg', 'jpeg', 'webp'] }] });
  if (choice.canceled || !choice.filePaths[0]) return null;
  const source = choice.filePaths[0], ext = path.extname(source).toLowerCase(), mime = backgroundMime[ext];
  if (!mime) throw new Error('请选用 PNG、JPG 或 WebP 图片。');
  const info = await fs.stat(source);
  if (!info.isFile() || info.size > 8 * 1024 * 1024) throw new Error('图片必须小于 8 MB。');
  const destination = path.join(app.getPath('userData'), `appearance-background${ext}`);
  await fs.mkdir(app.getPath('userData'), { recursive: true });
  await fs.copyFile(source, destination);
  settings.backgroundExt = ext; await save();
  return { name: path.basename(source), dataUrl: `data:${mime};base64,${(await fs.readFile(destination)).toString('base64')}` };
});
ipcMain.handle('chat:get-background', async event => {
  if (!fromChatPage(event)) return null;
  const stored = backgroundFile(); if (!stored) return null;
  try { return `data:${stored.mime};base64,${(await fs.readFile(stored.path)).toString('base64')}`; }
  catch { return null; }
});
ipcMain.handle('chat:clear-background', async event => {
  if (!(fromChat(event) || detachedViewFromEvent(event) === 'more')) return false;
  for (const ext of Object.keys(backgroundMime)) {
    await fs.rm(path.join(app.getPath('userData'), `appearance-background${ext}`), { force: true });
  }
  settings.backgroundExt = null; await save(); return true;
});
ipcMain.handle('chat:pet-size', event => fromChatPage(event) && pet && !pet.isDestroyed() ? pet.getBounds().width : settings.size);
ipcMain.handle('chat:save-theme', (event, theme) => {
  if (!fromChatPage(event) || !['light', 'dark'].includes(theme)) return false;
  settings.theme = theme; saveSoon();
  if (menu && !menu.isDestroyed() && !menu.webContents.isLoading()) menu.webContents.send('menu:theme', theme);
  return true;
});
ipcMain.handle('chat:set-pet-size', (event, value) => {
  if (!(fromChat(event) || detachedViewFromEvent(event) === 'more') || !Number.isFinite(value)) return false;
  createPet(Math.max(160, Math.min(720, Math.round(value))));
  saveSoon();
  return true;
});
ipcMain.handle('chat:open-memory', async event => {
  if (!(fromChat(event) || ['memory', 'more'].includes(detachedViewFromEvent(event)))) return false;
  const state = await endpointRequest('/api/events?after=0'); if (!state.memory_panel_url) return false;
  const url = new URL(state.memory_panel_url);
  if (!['127.0.0.1', 'localhost'].includes(url.hostname)) return false;
  await shell.openExternal(url.href); return true;
});
ipcMain.handle('view:detach', async (event, view, point) => {
  if (!fromChat(event) || !VIEW_NAMES.has(view) || !point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) return false;
  return createDetachedWindow(view, point);
});
ipcMain.handle('view:merge', (event, view) => detachedViewFromEvent(event) === view ? mergeDetachedWindow(view) : false);
ipcMain.handle('view:focus', (event, view) => {
  const win = detachedWindows.get(view);
  if (!fromChat(event) || !VIEW_NAMES.has(view) || !win || win.isDestroyed()) return false;
  win.show(); win.focus(); return true;
});
ipcMain.handle('view:states', event => fromChatPage(event) ? detachedViewNames() : []);
ipcMain.on('desktop:collapse', () => { if (chat) chat.hide(); });
ipcMain.handle('chat:reconnect', event => reconnectChat(event));
ipcMain.on('desktop:expand', event => {
  if (!fromChat(event) || !chat || chat.isDestroyed()) return;
  const bounds = chat.getBounds();
  const area = workAreaAt(bounds.x + Math.floor(bounds.width / 2), bounds.y + Math.floor(bounds.height / 2));
  chat.setBounds({ x: area.x, y: area.y, width: area.width, height: area.height });
  settings.chatBounds = chat.getBounds(); saveSoon();
});
ipcMain.on('desktop:show-activity', () => { showActivity(); });
ipcMain.on('pet:toggle', event => { if (fromPet(event)) toggleChat(); });
ipcMain.on('pet:menu', event => { if (fromPet(event)) showMenu(); });
ipcMain.on('pet:drag-start', event => { if (fromPet(event)) drag = { cursor: screen.getCursorScreenPoint(), bounds: pet.getBounds() }; });
ipcMain.on('pet:drag-move', event => {
  if (!fromPet(event) || !drag) return;
  const cursor = screen.getCursorScreenPoint(); pet.setPosition(drag.bounds.x + cursor.x - drag.cursor.x, drag.bounds.y + cursor.y - drag.cursor.y);
});
ipcMain.on('pet:drag-end', event => { if (fromPet(event)) { drag = null; save().catch(() => {}); } });
ipcMain.on('pet:resize-start', (event, corner) => {
  if (fromPet(event) && ['nw', 'ne', 'sw', 'se'].includes(corner)) {
    petResize = { cursor: screen.getCursorScreenPoint(), bounds: pet.getBounds(), corner };
  }
});
ipcMain.on('pet:resize-move', event => {
  if (!fromPet(event) || !petResize) return;
  const point = screen.getCursorScreenPoint(), { bounds, corner, cursor } = petResize;
  const dx = point.x - cursor.x, dy = point.y - cursor.y;
  const widthDelta = corner.includes('e') ? dx : -dx;
  const heightDelta = corner.includes('s') ? dy : -dy;
  const size = Math.max(160, Math.min(720, Math.round(bounds.width + Math.max(widthDelta, heightDelta))));
  const x = corner.includes('w') ? bounds.x + bounds.width - size : bounds.x;
  const y = corner.includes('n') ? bounds.y + bounds.height - size : bounds.y;
  const area = workAreaAt(point.x, point.y);
  pet.setBounds({ ...clampPosition(x, y, size, size, area), width: size, height: size });
  settings.size = size;
});
ipcMain.on('pet:resize-end', event => { if (fromPet(event)) { petResize = null; save().catch(() => {}); } });
ipcMain.on('pet:hit', (event, opaque) => { if (fromPet(event) && !drag) pet.setIgnoreMouseEvents(!opaque, { forward: true }); });
ipcMain.on('menu:action', async (event, action, value) => {
  if (!menu || event.sender !== menu.webContents) return;
  hideMenu();
  if (action === 'chat') toggleChat();
  else if (action === 'appearance') showAppearance();
  else if (action === 'size' && Number.isFinite(value)) { createPet(Math.max(160, Math.min(720, Math.round(value)))); save().catch(() => {}); }
  else if (action === 'activity') showActivity();
  else if (action === 'quit-agent') {
    if (quitting) return;
    quitting = true;
    try {
      await flushScreenPopWindows();
      if (!agentEndpoint) {
        const root = settings.root || (app.isPackaged
          ? path.resolve(path.dirname(process.execPath), '..', '..', '..')
          : path.resolve(__dirname, '..'));
        agentEndpoint = await readEndpoint(root);
      }
      if (agentEndpoint) await endpointRequest('/api/exit', 'POST', {});
    } catch (error) {
      // The Agent may already have stopped, or its exit response may be lost.
      // Never trap the user in the desktop shell when they explicitly chose exit.
      console.warn('VEIL Agent exit request failed; closing desktop shell:', error?.name || 'Error', error?.message || 'unknown error');
    } finally {
      app.quit();
    }
  }
});

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (pet) pet.showInactive(); });
  app.whenReady().then(async () => {
    try { Object.assign(settings, JSON.parse(await fs.readFile(settingsPath(), 'utf8'))); }
    catch {
      if (app.isPackaged) {
        try {
          const legacy = path.join(path.dirname(process.execPath), 'data', 'desktop.json');
          Object.assign(settings, JSON.parse(await fs.readFile(legacy, 'utf8')));
          await save().catch(() => {});
        } catch {}
      }
    }
    if (settings.activityDetached === true) {
      settings.detachedViews = [...new Set([...(Array.isArray(settings.detachedViews) ? settings.detachedViews : []), 'activity'])];
      settings.detachedBounds ||= {};
      if (settings.activityBounds && !settings.detachedBounds.activity) settings.detachedBounds.activity = settings.activityBounds;
    }
    if (!backgroundFile()) settings.backgroundExt = null;
    const { session } = require('electron');
    session.defaultSession.setPermissionRequestHandler((contents, permission, callback, details) =>
      callback(canRequestChatMedia(contents, permission, details)));
    session.defaultSession.setPermissionCheckHandler((contents, permission, _origin, details) =>
      canCheckChatMedia(contents, permission, details));
    createPet();
    startScreenPopPolling();
  });
  app.on('before-quit', event => {
    quitting = true;
    if (screenPopPollTimer) clearInterval(screenPopPollTimer);
    if (quitFlushStarted || !screenPopWindows.size) return;
    event.preventDefault();
    quitFlushStarted = true;
    void flushScreenPopWindows().finally(() => app.quit());
  });
  app.on('window-all-closed', () => app.quit());
}
module.exports = { canRequestChatMedia, canCheckChatMedia };
