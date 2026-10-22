'use strict';
// Runs the actual Electron windows against fictional HTTP data, never a real Agent.
const { app, BrowserWindow, Menu, session } = require('electron');
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
  let sends = 0, imageUploads = 0, imageAttempts = 0, imageSends = 0, approvals = 0, pending = null;
  const token = 'fictional_token_123456789';
  const validPng = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');
  server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/') {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      res.end(await fs.readFile(path.join(__dirname, '../../web/index.html'))); return;
    }
    if (url.pathname === '/media/fictional-image.png') {
      assert.equal(url.searchParams.get('t'), token);
      res.setHeader('Content-Type', 'image/png'); res.end(validPng); return;
    }
    assert.equal(req.headers['x-token'], token);
    res.setHeader('Content-Type', 'application/json');
    if (url.pathname === '/api/events') {
      res.end(JSON.stringify({ name: 'Fictional Agent', events: events.filter(e => e.id > Number(url.searchParams.get('after') || 0)),
        busy: false, ready: true, stopped: false, stopping: false, pending,
        memory_panel_url: 'http://127.0.0.1:1/dashboard' }));
    } else if (url.pathname === '/api/media') {
      let raw = ''; for await (const chunk of req) raw += chunk;
      const body = JSON.parse(raw); assert.equal(body.mime, 'image/jpeg');
      assert.equal(Buffer.from(body.data, 'base64').subarray(0, 3).equals(Buffer.from([0xFF, 0xD8, 0xFF])), true);
      imageUploads++;
      res.statusCode = 201; res.end(JSON.stringify({ ok: true, media: { id: 'fictional-image', filename: 'fictional-image.png', mime: 'image/png', name: 'fictional.png' } }));
    } else if (url.pathname === '/api/send') {
      let raw = ''; for await (const chunk of req) raw += chunk;
      const body = JSON.parse(raw);
      if (body.media?.id === 'fictional-image') {
        imageAttempts++; assert.equal(body.text, '[图片]');
        if (imageAttempts === 1) { res.statusCode = 409; res.end(JSON.stringify({ error: 'busy' })); return; }
        imageSends++;
        events.push({ id: 4, kind: 'user', text: '[图片]', request_id: body.request_id, media: { id: 'fictional-image', filename: 'fictional-image.png', mime: 'image/png', name: 'fictional.png' }, time: new Date().toISOString() });
      } else {
        sends++;
        assert.equal(body.text, 'fictional hello');
        events.push({ id: 2, kind: 'user', text: body.text, time: new Date().toISOString() });
        pending = 3; events.push({ id: 3, kind: 'approval', text: 'Fictional permission request', time: new Date().toISOString() });
      }
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
  await app.whenReady();
  let permissionRequest, permissionCheck;
  const setPermissionRequestHandler = session.defaultSession.setPermissionRequestHandler.bind(session.defaultSession);
  const setPermissionCheckHandler = session.defaultSession.setPermissionCheckHandler.bind(session.defaultSession);
  session.defaultSession.setPermissionRequestHandler = handler => { permissionRequest = handler; setPermissionRequestHandler(handler); };
  session.defaultSession.setPermissionCheckHandler = handler => { permissionCheck = handler; setPermissionCheckHandler(handler); };
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
  await wait(() => permissionRequest && permissionCheck, 'permission handlers missing');
  const ask = (contents, types, requestingUrl = `${origin}/`) => new Promise(resolve =>
    permissionRequest(contents, 'media', resolve, { requestingUrl, mediaTypes: types }));
  assert.equal(await ask(chat.webContents, ['audio']), true, 'local chat microphone should be allowed');
  assert.equal(await ask(chat.webContents, ['video']), false, 'camera must stay denied');
  assert.equal(await ask(pet.webContents, ['audio']), false, 'pet renderer must not get microphone');
  assert.equal(await ask(chat.webContents, ['audio'], 'https://example.invalid/'), false, 'foreign origin must not get microphone');
  assert.equal(permissionCheck(chat.webContents, 'media', origin, { mediaType: 'audio' }), true);
  assert.equal(await chat.webContents.executeJavaScript('typeof window.companion'), 'undefined');
  assert.equal(await chat.webContents.executeJavaScript('typeof require'), 'undefined');
  assert.equal(await chat.webContents.executeJavaScript('document.querySelector("#image-file").accept'), 'image/png,image/jpeg,image/gif,image/webp');
  assert.equal(await chat.webContents.executeJavaScript('document.querySelector("#voice-hold").textContent'), '按住说话');
  assert.equal(await chat.webContents.executeJavaScript('document.querySelector("#voice-hold").disabled'), false);
  assert.equal(await chat.webContents.executeJavaScript('document.querySelector("#memory-panel").disabled'), true);
  assert.ok(await chat.webContents.executeJavaScript('document.querySelector("#box").getBoundingClientRect().width >= 300'), 'desktop input too narrow');
  await wait(() => chat.webContents.executeJavaScript('!document.querySelector("#send").disabled'), 'send disabled');
  await chat.webContents.executeJavaScript('document.querySelector("#box").value="fictional hello"; document.querySelector("#send").click()');
  await wait(() => sends === 1, 'message not delivered');
  await wait(() => chat.webContents.executeJavaScript('!!document.querySelector(".approval button:not(.yes)")'), 'approval missing');
  await chat.webContents.executeJavaScript('document.querySelector(".approval button:not(.yes)").click()');
  await wait(() => approvals === 1, 'permission decision missing');
  await chat.webContents.executeJavaScript(`(() => { const input=document.querySelector('#image-file'); const bytes=Uint8Array.from(atob('${validPng.toString('base64')}'), c=>c.charCodeAt(0)); const file=new File([bytes], 'fictional.png', {type:'image/png'}); const dt=new DataTransfer(); dt.items.add(file); Object.defineProperty(input, 'files', {value:dt.files}); input.dispatchEvent(new Event('change')); })()`);
  await wait(() => chat.webContents.executeJavaScript('!document.querySelector("#image-preview").hidden'), 'image preview missing');
  assert.equal(imageUploads, 0);
  await chat.webContents.executeJavaScript('document.querySelector("#send").click()');
  await wait(() => imageAttempts === 1, 'image retry was not exercised');
  await wait(() => chat.webContents.executeJavaScript('!document.querySelector("#image-preview").hidden'), 'image was not retained for retry');
  await wait(() => chat.webContents.executeJavaScript('!document.querySelector("#send").disabled'), 'image retry button not enabled');
  await chat.webContents.executeJavaScript('document.querySelector("#send").click()');
  await wait(() => imageSends === 1, 'image not delivered');
  assert.equal(imageUploads, 1);
  await wait(() => chat.webContents.executeJavaScript('(() => { const i=document.querySelector(".msg.user .msg-image"); return !!i && i.complete && i.naturalWidth > 0; })()'), 'image bubble missing');
  chat.close(); assert.equal(chat.isDestroyed(), false); assert.equal(chat.isVisible(), false);
  await toggle(); await wait(() => chat.isVisible(), 'chat did not reopen');
  assert.equal(sends, 1); assert.equal(approvals, 1);
  const reloaded = new Promise(resolve => chat.webContents.once('did-finish-load', resolve));
  chat.webContents.reload();
  await reloaded;
  await wait(() => chat.webContents.executeJavaScript('(() => { const i=document.querySelector(".msg.user .msg-image"); return !!i && i.complete && i.naturalWidth > 0; })()'), 'image history missing after reload');
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
  console.log('SMOKE PASS: photo flow, chat-only microphone permission, camera/foreign renderer denial, hide/reopen, reload, resize, sandbox');
  clearTimeout(failSafe);
  server.close(); app.exit(0);
})().catch(error => { console.error(error); clearTimeout(failSafe); server?.close(); app.exit(1); });
