'use strict';
// Runs the actual Electron windows against fictional HTTP data, never a real Agent.
const { app, BrowserWindow, Menu } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const wait = async (predicate, message) => {
  const end = Date.now() + 15000;
  while (Date.now() < end) { if (await predicate()) return; await new Promise(r => setTimeout(r, 80)); }
  throw new Error(message);
};
let temp, server;
const failSafe = setTimeout(() => { console.error('SMOKE timeout'); app.exit(1); }, 50000);
(async () => {
  temp = await fs.mkdtemp(path.join(os.tmpdir(), 'veil-ui-smoke-'));
  app.setPath('userData', temp);
  await fs.writeFile(path.join(temp, 'desktop.json'), JSON.stringify({ root: temp, size: 320 }));
  const events = [{ id: 1, kind: 'assistant', text: 'Fictional saved history', history: true, time: new Date().toISOString() }];
  let sends = 0, approvals = 0, pending = null;
  const token = 'fictional_token_123456789';
  server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/') {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      res.end(await fs.readFile(path.join(__dirname, '../../web/index.html'))); return;
    }
    assert.equal(req.headers['x-token'], token);
    res.setHeader('Content-Type', 'application/json');
    if (url.pathname === '/api/events') {
      res.end(JSON.stringify({ name: 'Fictional Agent', events: events.filter(e => e.id > Number(url.searchParams.get('after') || 0)),
        busy: false, ready: true, stopped: false, stopping: false, pending,
        memory_panel_url: 'http://127.0.0.1:1/dashboard' }));
    } else if (url.pathname === '/api/send') {
      let raw = ''; for await (const chunk of req) raw += chunk;
      const body = JSON.parse(raw); assert.equal(body.text, 'fictional hello');
      sends++;
      events.push({ id: 2, kind: 'user', text: body.text, time: new Date().toISOString() });
      pending = 3; events.push({ id: 3, kind: 'approval', text: 'Fictional permission request', time: new Date().toISOString() });
      res.end('{}');
    } else if (url.pathname === '/api/approve') {
      let raw = ''; for await (const chunk of req) raw += chunk;
      const body = JSON.parse(raw); assert.equal(body.answer, 'n'); assert.equal(body.id, 3);
      approvals++; pending = null; res.end('{}');
    } else { res.statusCode = 404; res.end('{}'); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const backend = require('../backend.cjs');
  backend.connect = async () => ({ url: `${origin}/?t=${token}`, origin });
  backend.validRoot = async () => true;
  let menu, activeMenu;
  const build = Menu.buildFromTemplate;
  Menu.buildFromTemplate = template => { menu = template; activeMenu = build(template); return activeMenu; };
  require('../main.cjs');
  await app.whenReady();
  let pet;
  await wait(() => { pet = BrowserWindow.getAllWindows().find(w => w.webContents.getURL().endsWith('pet.html')); return pet?.isVisible(); }, 'pet not visible');
  assert.equal(pet.getBounds().width, 320);
  const prefs = pet.webContents.getLastWebPreferences();
  assert.equal(prefs.contextIsolation, true); assert.equal(prefs.sandbox, true); assert.equal(prefs.nodeIntegration, false);
  await wait(() => pet.webContents.executeJavaScript('document.querySelector("img").complete && document.querySelector("img").naturalWidth > 0'), 'image missing');
  await fs.mkdir(path.join(__dirname, '../test-output'), { recursive: true });
  await fs.writeFile(path.join(__dirname, '../test-output/pet.png'), (await pet.webContents.capturePage()).toPNG());
  const toggle = () => pet.webContents.executeJavaScript('document.querySelector("#pet").dispatchEvent(new KeyboardEvent("keydown", {key:"Enter",bubbles:true}))');
  await toggle();
  let chat;
  await wait(() => { chat = BrowserWindow.getAllWindows().find(w => w !== pet); return chat?.isVisible(); }, 'chat not visible');
  await wait(() => chat.webContents.executeJavaScript('document.body.textContent.includes("Fictional saved history")'), 'history missing');
  assert.equal(await chat.webContents.executeJavaScript('typeof window.companion'), 'undefined');
  assert.equal(await chat.webContents.executeJavaScript('typeof require'), 'undefined');
  assert.equal(await chat.webContents.executeJavaScript('document.querySelector("#memory-panel").disabled'), true);
  assert.ok(await chat.webContents.executeJavaScript('document.querySelector("#box").getBoundingClientRect().width >= 300'), 'desktop input too narrow');
  await wait(() => chat.webContents.executeJavaScript('!document.querySelector("#send").disabled'), 'send disabled');
  await chat.webContents.executeJavaScript('document.querySelector("#box").value="fictional hello"; document.querySelector("#send").click()');
  await wait(() => sends === 1, 'message not delivered');
  await wait(() => chat.webContents.executeJavaScript('!!document.querySelector(".approval button:not(.yes)")'), 'approval missing');
  await chat.webContents.executeJavaScript('document.querySelector(".approval button:not(.yes)").click()');
  await wait(() => approvals === 1, 'permission decision missing');
  chat.close(); assert.equal(chat.isDestroyed(), false); assert.equal(chat.isVisible(), false);
  await toggle(); await wait(() => chat.isVisible(), 'chat did not reopen');
  assert.equal(sends, 1); assert.equal(approvals, 1);
  await toggle(); await wait(() => !chat.isVisible(), 'chat did not hide');
  await pet.webContents.executeJavaScript('window.companion.menu()');
  await wait(() => !!menu, 'menu missing'); activeMenu.closePopup();
  assert.equal(menu.find(x => x.label?.startsWith('Memory')).enabled, false);
  const oldPet = pet;
  menu.find(x => x.label === 'Peter 大小').submenu.find(x => x.label === '420 px').click();
  await wait(() => oldPet.isDestroyed(), 'old pet not replaced');
  pet = BrowserWindow.getAllWindows().find(w => w !== chat);
  assert.equal(pet.getBounds().width, 420);
  chat.show();
  await fs.writeFile(path.join(__dirname, '../test-output/chat.png'), (await chat.webContents.capturePage()).toPNG());
  console.log('SMOKE PASS: original PNG, real windows, saved history, send once, permission refusal, hide/reopen, resize, sandbox, disabled entries');
  clearTimeout(failSafe);
  server.close(); app.exit(0);
})().catch(error => { console.error(error); clearTimeout(failSafe); server?.close(); app.exit(1); });
