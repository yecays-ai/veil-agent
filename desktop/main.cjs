'use strict';
const { app, BrowserWindow, Menu, ipcMain, dialog, screen } = require('electron');
const path = require('node:path');
const fs = require('node:fs/promises');
const { pathToFileURL } = require('node:url');
const { connect, validRoot } = require('./backend.cjs');
let pet, chat, connecting = false, quitting = false, drag;
let settings = { size: 320 };
const petURL = pathToFileURL(path.join(__dirname, 'pet.html')).href;
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
function visibleBounds(size, position = settings) {
  const area = screen.getDisplayNearestPoint({
    x: Number.isFinite(position.x) ? position.x : 0,
    y: Number.isFinite(position.y) ? position.y : 0
  }).workArea;
  return {
    x: Math.round(Math.max(area.x, Math.min(position.x ?? area.x + area.width - size - 24, area.x + area.width - size))),
    y: Math.round(Math.max(area.y, Math.min(position.y ?? area.y + area.height - size - 24, area.y + area.height - size))),
    width: size, height: size
  };
}
function createPet(size = settings.size) {
  // Recreate the transparent window rather than enabling unsupported native resizing.
  size = [160, 240, 320, 420, 540].includes(size) ? size : 320;
  const old = pet;
  const bounds = visibleBounds(size, old && !old.isDestroyed() ? old.getBounds() : settings);
  settings.size = size;
  pet = new BrowserWindow({ ...bounds, frame: false, transparent: true,
    resizable: false, maximizable: false, minimizable: false, hasShadow: false,
    alwaysOnTop: true, skipTaskbar: true, show: false,
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true, nodeIntegration: false, sandbox: true }
  });
  const current = pet;
  safeContents(current, url => url === petURL);
  current.once('ready-to-show', () => {
    current.showInactive();
    if (old && !old.isDestroyed()) old.destroy();
  });
  current.loadURL(petURL);
  current.on('closed', () => { if (pet === current) pet = null; });
}
async function selectRoot() {
  const choice = await dialog.showOpenDialog({ title: '选择已有的 VEIL Agent 文件夹（里面有 start-web.bat）', properties: ['openDirectory'] });
  if (choice.canceled) return null;
  if (!await validRoot(choice.filePaths[0])) throw new Error('此文件夹尚未安装 Agent。请先在原 Agent 文件夹运行 setup.bat。');
  settings.root = choice.filePaths[0];
  await save();
  return settings.root;
}
async function toggleChat() {
  if (chat && !chat.isDestroyed()) {
    if (chat.isVisible()) chat.hide(); else { chat.show(); chat.focus(); }
    return;
  }
  if (connecting) return;
  connecting = true;
  try {
    let root = settings.root;
    const adjacent = path.resolve(__dirname, '..');
    if (!root && !app.isPackaged && await validRoot(adjacent)) root = adjacent;
    if (!root) root = await selectRoot();
    if (!root) return;
    const endpoint = await connect(root);
    if (quitting) return;
    chat = new BrowserWindow({ width: 640, height: 780, minWidth: 420, minHeight: 480,
      title: 'VEIL · 聊天', show: false,
      webPreferences: { preload: path.join(__dirname, 'chat-preload.cjs'),
        contextIsolation: true, nodeIntegration: false, sandbox: true }
    });
    const current = chat;
    safeContents(current, url => {
      try { const u = new URL(url); return u.origin === endpoint.origin && u.pathname === '/'; }
      catch { return false; }
    });
    current.setMenu(null);
    current.on('close', event => { if (!quitting) { event.preventDefault(); current.hide(); } });
    current.on('closed', () => { if (chat === current) chat = null; });
    await current.loadURL(endpoint.url).catch(() => {
      throw new Error('聊天页面没有加载成功。请确认原网页聊天可用后，再点击 Peter 重试。');
    });
    if (!quitting) { current.show(); current.focus(); }
  } catch (error) {
    if (chat && !chat.isDestroyed()) chat.destroy();
    dialog.showErrorBox('VEIL · 暂时无法聊天', error.message);
  } finally { connecting = false; }
}
function showMenu() {
  Menu.buildFromTemplate([
    { label: '展开 / 收起聊天', click: toggleChat },
    { label: '重新连接聊天（服务退出后使用）', enabled: !connecting, click: () => {
      if (chat && !chat.isDestroyed()) chat.destroy();
      toggleChat();
    } },
    { label: 'Peter 大小', submenu: [160, 240, 320, 420, 540].map(size => ({
      label: `${size} px`, type: 'radio', checked: settings.size === size,
      click: () => { createPet(size); save().catch(() => {}); }
    })) },
    { type: 'separator' },
    { label: 'Memory / Ombre · Coming Soon（桌面入口未接通）', enabled: false },
    { label: 'Lab · Coming Soon（桌面入口未接通）', enabled: false },
    { label: '更换 Agent 文件夹', enabled: !connecting, click: async () => {
      try {
        const root = await selectRoot();
        if (root && chat && !chat.isDestroyed()) chat.destroy();
      } catch (error) { dialog.showErrorBox('VEIL', error.message); }
    } },
    { type: 'separator' },
    { label: '退出桌面形象（Agent 服务继续运行）', click: () => app.quit() }
  ]).popup({ window: pet });
}
const fromPet = event => pet && !pet.isDestroyed() && event.sender === pet.webContents && event.senderFrame?.url === petURL;
ipcMain.on('pet:toggle', event => { if (fromPet(event)) toggleChat(); });
ipcMain.on('pet:menu', event => { if (fromPet(event)) showMenu(); });
ipcMain.on('pet:drag-start', event => {
  if (fromPet(event)) drag = { cursor: screen.getCursorScreenPoint(), bounds: pet.getBounds() };
});
ipcMain.on('pet:drag-move', event => {
  if (!fromPet(event) || !drag) return;
  const cursor = screen.getCursorScreenPoint();
  pet.setPosition(drag.bounds.x + cursor.x - drag.cursor.x, drag.bounds.y + cursor.y - drag.cursor.y);
});
ipcMain.on('pet:drag-end', event => {
  if (fromPet(event)) { drag = null; save().catch(() => {}); }
});
ipcMain.on('pet:hit', (event, opaque) => {
  if (fromPet(event) && !drag) pet.setIgnoreMouseEvents(!opaque, { forward: true });
});
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { if (pet) pet.show(); });
  app.whenReady().then(async () => {
    try { Object.assign(settings, JSON.parse(await fs.readFile(settingsPath(), 'utf8'))); } catch {}
    // No remote renderer receives Electron/Node APIs, microphone, or device permissions.
    const { session } = require('electron');
    session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    createPet();
  });
  app.on('before-quit', () => { quitting = true; });
  app.on('window-all-closed', () => app.quit());
}
