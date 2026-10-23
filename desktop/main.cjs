'use strict';
const { app, BrowserWindow, ipcMain, dialog, screen, shell } = require('electron');
const path = require('node:path');
const fs = require('node:fs/promises');
const { pathToFileURL } = require('node:url');
const { connect, validRoot, readEndpoint } = require('./backend.cjs');

let pet, chat, menu, connecting = false, quitting = false, drag, petResize, agentEndpoint, saveTimer;
const settings = { size: 320, theme: 'light' };
const petURL = pathToFileURL(path.join(__dirname, 'pet.html')).href;
const chatURL = pathToFileURL(path.join(__dirname, 'chat.html')).href;
const menuURL = pathToFileURL(path.join(__dirname, 'menu.html')).href;
function canRequestChatMedia(contents, permission, details, currentChat = chat) {
  const audioOnly = Array.isArray(details?.mediaTypes) && details.mediaTypes.length === 1 && details.mediaTypes[0] === 'audio';
  return Boolean(currentChat && !currentChat.isDestroyed() && contents === currentChat.webContents && permission === 'media' &&
    details?.requestingUrl === chatURL && audioOnly);
}
function canCheckChatMedia(contents, permission, details, currentChat = chat) {
  return Boolean(currentChat && !currentChat.isDestroyed() && contents === currentChat.webContents && permission === 'media' &&
    details?.mediaType === 'audio' && details?.requestingUrl === chatURL && details?.isMainFrame === true);
}
const runtimeDataPath = app.isPackaged
  ? path.join(app.getPath('appData'), 'VEIL Desktop')
  : path.join(__dirname, '.runtime-data');
app.setPath('userData', runtimeDataPath);
app.setPath('sessionData', path.join(runtimeDataPath, 'session-data'));
const settingsPath = () => path.join(app.getPath('userData'), 'desktop.json');

async function save() {
  if (pet && !pet.isDestroyed()) Object.assign(settings, pet.getBounds());
  await fs.mkdir(app.getPath('userData'), { recursive: true });
  await fs.writeFile(settingsPath(), JSON.stringify(settings), 'utf8');
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
  } catch (error) {
    if (chat && !chat.isDestroyed()) chat.destroy();
    dialog.showErrorBox('VEIL · 暂时无法聊天', error.message);
  } finally { connecting = false; }
}
async function showActivity() {
  if (!chat || chat.isDestroyed()) await toggleChat();
  else if (!chat.isVisible()) { chat.show(); chat.focus(); }
  if (chat && !chat.isDestroyed()) chat.webContents.send('desktop:show-activity');
}
async function showAppearance() {
  if (!chat || chat.isDestroyed()) await toggleChat();
  else if (!chat.isVisible()) { chat.show(); chat.focus(); }
  if (chat && !chat.isDestroyed()) chat.webContents.send('desktop:show-settings');
}
async function reconnectChat(event) {
  if (event && !fromChat(event)) return false;
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
  const data = await response.json().catch(() => ({})); if (!response.ok) throw new Error(data.error || '请求失败'); return data;
}
const fromPet = event => pet && !pet.isDestroyed() && event.sender === pet.webContents && event.senderFrame?.url === petURL;
const fromChat = event => chat && !chat.isDestroyed() && event.sender === chat.webContents && event.senderFrame?.url === chatURL;
function allowedChatRequest(pathname, method) {
  if (method === 'GET') {
    let url;
    try { url = new URL(pathname, 'http://127.0.0.1'); } catch { return false; }
    const keys = [...url.searchParams.keys()];
    if (url.pathname === '/api/events') return keys.length === 1 && keys[0] === 'after' && /^\d+$/.test(url.searchParams.get('after'));
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
    '/api/transcribe', '/api/approve', '/api/activity', '/api/exit'].includes(pathname);
}
ipcMain.handle('chat:request', async (event, pathname, body, timeoutMs) => {
  if (!fromChat(event) || typeof pathname !== 'string' || !allowedChatRequest(pathname, body ? 'POST' : 'GET'))
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
  if (!fromChat(event) || !agentEndpoint) throw new Error('请求未允许。');
  if (typeof filename !== 'string' || filename.length > 180 || !/^[A-Za-z0-9_.-]+$/.test(filename) || filename.includes('..'))
    throw new Error('无效的媒体文件名。');
  const token = new URL(agentEndpoint.url).searchParams.get('t');
  return `${agentEndpoint.origin}/media/${encodeURIComponent(filename)}?t=${encodeURIComponent(token)}`;
});
const backgroundMime = { '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp' };
function backgroundFile() {
  const ext = typeof settings.backgroundExt === 'string' ? settings.backgroundExt.toLowerCase() : '';
  return backgroundMime[ext] ? { ext, mime: backgroundMime[ext], path: path.join(app.getPath('userData'), `appearance-background${ext}`) } : null;
}
ipcMain.handle('chat:choose-background', async event => {
  if (!fromChat(event)) return null;
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
  if (!fromChat(event)) return null;
  const stored = backgroundFile(); if (!stored) return null;
  try { return `data:${stored.mime};base64,${(await fs.readFile(stored.path)).toString('base64')}`; }
  catch { return null; }
});
ipcMain.handle('chat:clear-background', async event => {
  if (!fromChat(event)) return false;
  settings.backgroundExt = null; await save(); return true;
});
ipcMain.handle('chat:pet-size', event => fromChat(event) && pet && !pet.isDestroyed() ? pet.getBounds().width : settings.size);
ipcMain.handle('chat:save-theme', (event, theme) => {
  if (!fromChat(event) || !['light', 'dark'].includes(theme)) return false;
  settings.theme = theme; saveSoon();
  if (menu && !menu.isDestroyed() && !menu.webContents.isLoading()) menu.webContents.send('menu:theme', theme);
  return true;
});
ipcMain.handle('chat:set-pet-size', (event, value) => {
  if (!fromChat(event) || !Number.isFinite(value)) return false;
  createPet(Math.max(160, Math.min(720, Math.round(value))));
  saveSoon();
  return true;
});
ipcMain.handle('chat:open-memory', async event => {
  if (!fromChat(event)) return false;
  const state = await endpointRequest('/api/events?after=0'); if (!state.memory_panel_url) return false;
  const url = new URL(state.memory_panel_url);
  if (!['127.0.0.1', 'localhost'].includes(url.hostname)) return false;
  await shell.openExternal(url.href); return true;
});
ipcMain.on('desktop:collapse', () => { if (chat) chat.hide(); });
ipcMain.handle('chat:reconnect', event => reconnectChat(event));
ipcMain.on('desktop:expand', () => { if (chat && !chat.isDestroyed()) { chat.setSize(760, 780); positionChat(); } });
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
    if (!backgroundFile()) settings.backgroundExt = null;
    const { session } = require('electron');
    session.defaultSession.setPermissionRequestHandler((contents, permission, callback, details) =>
      callback(canRequestChatMedia(contents, permission, details)));
    session.defaultSession.setPermissionCheckHandler((contents, permission, _origin, details) =>
      canCheckChatMedia(contents, permission, details));
    createPet();
  });
  app.on('before-quit', () => { quitting = true; }); app.on('window-all-closed', () => app.quit());
}
module.exports = { canRequestChatMedia, canCheckChatMedia };
