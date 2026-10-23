'use strict';
const { app, BrowserWindow, ipcMain, dialog, screen, shell } = require('electron');
const path = require('node:path');
const fs = require('node:fs/promises');
const { pathToFileURL } = require('node:url');
const { connect, validRoot } = require('./backend.cjs');

let pet, chat, menu, connecting = false, quitting = false, drag, agentEndpoint;
const settings = { size: 320 };
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
  size = [160, 240, 320, 420, 540].includes(size) ? size : 320;
  const old = pet; const bounds = visibleBounds(size, old && !old.isDestroyed() ? old.getBounds() : settings);
  settings.size = size;
  pet = new BrowserWindow({ ...bounds, frame: false, transparent: true, resizable: false,
    maximizable: false, minimizable: false, hasShadow: false, alwaysOnTop: true,
    skipTaskbar: true, show: false, webPreferences: { preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true, nodeIntegration: false, sandbox: true } });
  const current = pet; safeContents(current, url => url === petURL);
  current.once('ready-to-show', () => {
    current.showInactive(); if (old && !old.isDestroyed()) old.destroy();
    if (chat && !chat.isDestroyed()) positionChat(); save().catch(() => {});
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
  if (chat && !chat.isDestroyed()) { if (chat.isVisible()) chat.hide(); else { positionChat(); chat.show(); chat.focus(); } return; }
  if (connecting) return; connecting = true;
  try {
    let root = settings.root;
    const adjacent = app.isPackaged
      ? path.resolve(path.dirname(process.execPath), '..', '..', '..', 'veil-agent')
      : path.resolve(__dirname, '..', 'veil-agent');
    if (!root && await validRoot(adjacent)) { root = adjacent; settings.root = root; await save().catch(() => {}); }
    if (!root) root = await selectRoot(); if (!root) return;
    agentEndpoint = await connect(root); if (quitting) return;
    chat = new BrowserWindow({ x: 0, y: 0, width: 410, height: 520, minWidth: 390, minHeight: 460,
      frame: false, transparent: true, hasShadow: true, alwaysOnTop: true, skipTaskbar: true,
      title: 'VEIL · Peter', show: false,
      webPreferences: { preload: path.join(__dirname, 'desktop-preload.cjs'),
        contextIsolation: true, nodeIntegration: false, sandbox: true } });
    const current = chat; safeContents(current, url => url === chatURL);
    current.on('close', event => { if (!quitting) { event.preventDefault(); current.hide(); } });
    current.on('closed', () => { if (chat === current) chat = null; });
    await current.loadURL(chatURL); positionChat(); if (!quitting) { current.show(); current.focus(); }
  } catch (error) {
    if (chat && !chat.isDestroyed()) chat.destroy();
    dialog.showErrorBox('VEIL · 暂时无法聊天', error.message);
  } finally { connecting = false; }
}
async function showActivity() {
  if (!chat || chat.isDestroyed()) await toggleChat();
  else if (!chat.isVisible()) { positionChat(); chat.show(); chat.focus(); }
  if (chat && !chat.isDestroyed()) chat.webContents.send('desktop:show-activity');
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
  const bounds = clampPosition(point.x, point.y, 246, 334, area);
  if (!menu || menu.isDestroyed()) {
    menu = new BrowserWindow({ ...bounds, width: 246, height: 334, frame: false, transparent: true,
      resizable: false, show: false, skipTaskbar: true, alwaysOnTop: true,
      webPreferences: { preload: path.join(__dirname, 'menu-preload.cjs'), contextIsolation: true,
        nodeIntegration: false, sandbox: true } });
    safeContents(menu, url => url === menuURL); menu.on('blur', hideMenu);
    menu.on('closed', () => { menu = null; });
    menu.once('ready-to-show', () => { if (menu && !menu.isDestroyed()) { menu.show(); menu.focus(); } });
    menu.loadURL(menuURL);
  } else { menu.setBounds({ ...bounds, width: 246, height: 334 }); }
  if (!menu.webContents.isLoading()) { menu.show(); menu.focus(); }
}
async function endpointRequest(pathname, method = 'GET', body) {
  if (!agentEndpoint) throw new Error('Agent 尚未连接。');
  const token = new URL(agentEndpoint.url).searchParams.get('t');
  const response = await fetch(`${agentEndpoint.origin}${pathname}`, { method, headers: {
    'X-Token': token, ...(body ? { 'Content-Type': 'application/json' } : {})
  }, body: body ? JSON.stringify(body) : undefined });
  const data = await response.json().catch(() => ({})); if (!response.ok) throw new Error(data.error || '请求失败'); return data;
}
const fromPet = event => pet && !pet.isDestroyed() && event.sender === pet.webContents && event.senderFrame?.url === petURL;
const fromChat = event => chat && !chat.isDestroyed() && event.sender === chat.webContents && event.senderFrame?.url === chatURL;
function allowedChatRequest(pathname, method) {
  if (method === 'GET') return /^\/api\/events\?after=\d+$/.test(pathname);
  return method === 'POST' && ['/api/send', '/api/media', '/api/voice', '/api/voice/retry',
    '/api/approve', '/api/activity', '/api/exit'].includes(pathname);
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
  if (chat && chat.isVisible()) positionChat();
});
ipcMain.on('pet:drag-end', event => { if (fromPet(event)) { drag = null; save().catch(() => {}); } });
ipcMain.on('pet:hit', (event, opaque) => { if (fromPet(event) && !drag) pet.setIgnoreMouseEvents(!opaque, { forward: true }); });
ipcMain.on('menu:action', (event, action, value) => {
  if (!menu || event.sender !== menu.webContents) return;
  hideMenu();
  if (action === 'chat') toggleChat();
  else if (action === 'size' && [160, 240, 320, 420, 540].includes(value)) { createPet(value); save().catch(() => {}); }
  else if (action === 'activity') showActivity();
  else if (action === 'quit') app.quit();
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
