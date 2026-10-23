'use strict';
// Windows Electron smoke: exercise local desktop UI with fictional Agent APIs and media.
const { app, BrowserWindow } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const wait = async (predicate, message) => {
  const end = Date.now() + 20000;
  while (Date.now() < end) { if (await predicate()) return; await new Promise(r => setTimeout(r, 80)); }
  throw new Error(message);
};
let temp, server;
const failSafe = setTimeout(() => { console.error('SMOKE timeout'); app.exit(1); }, 70000);
(async () => {
  temp = await fs.mkdtemp(path.join(os.tmpdir(), 'veil-ui-smoke-'));
  app.setPath('userData', temp);
  await fs.writeFile(path.join(temp, 'desktop.json'), JSON.stringify({ root: temp, size: 320 }));
  const token = 'fictional_token_123456789';
  let nextId = 3, pending = null, sends = 0, approvals = 0, images = 0, voices = 0;
  const requests = [], approvalBodies = [];
  const media = new Map();
  const events = [
    { id: 1, kind: 'assistant', text: 'Fictional saved history', history: true, time: new Date().toISOString() },
    { id: 2, kind: 'assistant', text: 'Fictional spoken reply', media: { id: 'reply', filename: 'fictional-reply.mp3', mime: 'audio/mpeg' }, history: true, time: new Date().toISOString() }
  ];
  const json = (res, value, status = 200) => { res.statusCode = status; res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(value)); };
  const readBody = async req => { let raw = ''; for await (const part of req) raw += part; return JSON.parse(raw); };
  server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    requests.push(url.pathname);
    if (url.pathname.startsWith('/media/')) {
      assert.equal(url.searchParams.get('t'), token);
      const item = media.get(decodeURIComponent(url.pathname.slice('/media/'.length)));
      if (!item) { res.statusCode = 404; res.end(); return; }
      res.setHeader('Content-Type', item.type); res.end(item.bytes); return;
    }
    assert.equal(req.headers['x-token'], token, 'Agent API requests must keep the auth header');
    if (url.pathname === '/api/events') {
      json(res, { name: 'Fictional Agent', events: events.filter(e => e.id > Number(url.searchParams.get('after') || 0)),
        busy: false, ready: true, stopped: false, stopping: false, pending,
        memory_panel_url: 'http://127.0.0.1:1/dashboard', activity_enabled: false });
    } else if (url.pathname === '/api/send') {
      const body = await readBody(req);
      if (body.text === 'fictional hello') {
        sends++; events.push({ id: nextId++, kind: 'user', text: body.text, request_id: body.request_id, time: new Date().toISOString() });
        pending = nextId; events.push({ id: nextId++, kind: 'approval', text: 'Fictional permission request', time: new Date().toISOString() });
      } else {
        sends++;
        const attached = body.media ? [...media.values()].find(x => x.id === body.media.id) : null;
        events.push({ id: nextId++, kind: 'user', text: body.text, request_id: body.request_id,
          media: attached?.eventMedia, time: new Date().toISOString() });
      }
      json(res, {});
    } else if (url.pathname === '/api/approve') {
      const body = await readBody(req);
      approvalBodies.push(body); approvals++; pending = null; json(res, {});
    } else if (url.pathname === '/api/media') {
      const body = await readBody(req); assert.equal(body.mime, 'image/jpeg');
      images++;
      const filename = 'fictional-image.jpg';
      const entry = { id: 'fictional-image', filename, type: 'image/jpeg', bytes: Buffer.from('fictional-image'),
        eventMedia: { id: 'fictional-image', filename, mime: 'image/jpeg', kind: 'image', name: body.name } };
      media.set(filename, entry); json(res, { media: entry.eventMedia });
    } else if (url.pathname === '/api/voice') {
      const body = await readBody(req); assert.equal(body.mime, 'audio/webm'); assert.ok(body.data);
      voices++;
      const filename = 'fictional-voice.webm';
      const entry = { id: 'fictional-voice', filename, type: 'audio/webm', bytes: Buffer.from('fictional-audio'),
        eventMedia: { id: 'fictional-voice', filename, mime: 'audio/webm', kind: 'voice', transcript: 'fictional voice transcript' } };
      media.set(filename, entry); json(res, { media: { ...entry.eventMedia, transcript: entry.eventMedia.transcript } });
    } else if (url.pathname === '/api/activity') {
      json(res, { enabled: false });
    } else {
      json(res, { error: 'not found' }, 404);
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const backend = require('../backend.cjs');
  backend.connect = async () => ({ url: `${origin}/?t=${token}`, origin });
  backend.validRoot = async () => true;
  require('../main.cjs');
  await app.whenReady();
  let pet;
  await wait(() => { pet = BrowserWindow.getAllWindows().find(w => w.webContents.getURL().endsWith('pet.html')); return pet?.isVisible(); }, 'pet not visible');
  assert.equal(pet.getBounds().width, 320);
  const prefs = pet.webContents.getLastWebPreferences();
  assert.equal(prefs.contextIsolation, true); assert.equal(prefs.sandbox, true); assert.equal(prefs.nodeIntegration, false);
  await wait(() => pet.webContents.executeJavaScript('document.querySelector("img").complete && document.querySelector("img").naturalWidth > 0'), 'Peter image missing');
  const toggle = () => pet.webContents.executeJavaScript('document.querySelector("#pet").dispatchEvent(new KeyboardEvent("keydown", {key:"Enter",bubbles:true}))');
  await toggle();
  let chat;
  await wait(() => { chat = BrowserWindow.getAllWindows().find(w => w !== pet); return chat?.isVisible(); }, 'chat not visible');
  await wait(() => chat.webContents.executeJavaScript('document.body.textContent.includes("Fictional saved history")'), 'history missing');
  assert.ok(chat.webContents.getURL().startsWith('file:'), 'desktop chat must load its own local UI');
  const chatPrefs = chat.webContents.getLastWebPreferences();
  assert.equal(chatPrefs.contextIsolation, true); assert.equal(chatPrefs.sandbox, true); assert.equal(chatPrefs.nodeIntegration, false);
  assert.equal(await chat.webContents.executeJavaScript('typeof window.veilDesktop.request'), 'function');
  assert.equal(await chat.webContents.executeJavaScript('typeof require'), 'undefined');
  assert.equal(await chat.webContents.executeJavaScript('document.querySelector("#speech-toggle").disabled'), false);
  assert.equal(await chat.webContents.executeJavaScript('document.querySelector("#image-button").disabled'), false);
  assert.equal(await chat.webContents.executeJavaScript('document.body.textContent.includes("Coming Soon")'), false);
  await wait(() => chat.webContents.executeJavaScript('!!document.querySelector(".msg-voice audio")'), 'saved voice reply did not render');
  await wait(() => chat.webContents.executeJavaScript('!document.querySelector("#send").disabled'), 'send disabled');

  await chat.webContents.executeJavaScript('document.querySelector("#box").value="fictional hello"; document.querySelector("#send").click()');
  await wait(() => sends === 1, 'text message not delivered');
  await wait(() => chat.webContents.executeJavaScript('!!document.querySelector(".approval button:not(.yes)")'), 'approval missing');
  await chat.webContents.executeJavaScript('document.querySelector(".approval button:not(.yes)").click()');
  await wait(() => approvals === 1, `permission refusal was not delivered; requests=${requests.join(',')}; approval=${JSON.stringify(approvalBodies)}; card=${JSON.stringify(await chat.webContents.executeJavaScript('(()=>{const c=document.querySelector(".approval");return c&&{id:c.dataset.id,className:c.className,status:c.querySelector(".status").textContent,buttons:[...c.querySelectorAll("button")].map(b=>({text:b.textContent,disabled:b.disabled}))}})()'))}`);
  assert.equal(approvalBodies[0].answer, 'n'); assert.equal(approvalBodies[0].id, 4);

  await chat.webContents.executeJavaScript(`(async()=>{const canvas=document.createElement('canvas');canvas.width=2;canvas.height=2;const ctx=canvas.getContext('2d');ctx.fillStyle='#7c66aa';ctx.fillRect(0,0,2,2);const data=canvas.toDataURL('image/png').split(',')[1];const bytes=Uint8Array.from(atob(data),c=>c.charCodeAt(0));const file=new File([bytes],'fictional.png',{type:'image/png'});const input=document.querySelector('#image-file');Object.defineProperty(input,'files',{configurable:true,value:[file]});return input.onchange()})()`);
  await wait(() => chat.webContents.executeJavaScript('!document.querySelector("#image-preview").hidden'), `image preview missing: ${JSON.stringify(await chat.webContents.executeJavaScript('(()=>({errors:[...document.querySelectorAll(".error")].map(e=>e.textContent),input:document.querySelector("#image-file").files?.[0]&&{name:document.querySelector("#image-file").files[0].name,type:document.querySelector("#image-file").files[0].type,size:document.querySelector("#image-file").files[0].size},preview:document.querySelector("#image-preview").textContent,hidden:document.querySelector("#image-preview").hidden}))()'))}`);
  await chat.webContents.executeJavaScript('document.querySelector("#box").value="fictional image caption"; document.querySelector("#send").click()');
  await wait(() => images === 1, 'image was not uploaded');
  await wait(() => chat.webContents.executeJavaScript('!!document.querySelector(".msg-image")'), 'uploaded image message missing');

  const voiceUpload = await chat.webContents.executeJavaScript("window.veilDesktop.request('/api/voice',{mime:'audio/webm',name:'fictional-voice.webm',data:'ZmFrZSBhdWRpbw=='},60000)");
  assert.equal(voiceUpload.ok, true);
  await chat.webContents.executeJavaScript(`window.veilDesktop.request('/api/send',{text:'[voice message] fictional voice transcript',request_id:'fictional-voice-send',media:{id:'fictional-voice'}})`);
  await wait(() => voices === 1 && sends === 3, 'voice upload and transcript message were not delivered');
  await wait(() => chat.webContents.executeJavaScript('document.body.textContent.includes("fictional voice transcript")'), 'voice transcript not shown');

  await chat.webContents.executeJavaScript('document.querySelector("#toolbar-toggle").click()');
  await wait(() => chat.webContents.executeJavaScript('!document.querySelector("#tool-panel").hidden'), 'desktop toolbar did not open');
  assert.equal(await chat.webContents.executeJavaScript('document.querySelector("#memory-panel").hidden'), false, 'real Memory entry should appear when available');
  await wait(() => chat.webContents.executeJavaScript('document.querySelector(".msg-image").src.includes("127.0.0.1")'), 'image media URL missing');
  await fs.mkdir(path.join(__dirname, '../test-output'), { recursive: true });
  await fs.writeFile(path.join(__dirname, '../test-output/pet.png'), (await pet.webContents.capturePage()).toPNG());
  await fs.writeFile(path.join(__dirname, '../test-output/chat.png'), (await chat.webContents.capturePage()).toPNG());

  chat.close(); assert.equal(chat.isDestroyed(), false); assert.equal(chat.isVisible(), false);
  await toggle(); await wait(() => chat.isVisible(), 'chat did not reopen');
  assert.equal(sends, 3); assert.equal(approvals, 1);
  await pet.webContents.executeJavaScript('window.companion.menu()');
  let menuWindow;
  await wait(() => { menuWindow = BrowserWindow.getAllWindows().find(w => w !== pet && w !== chat); return menuWindow?.isVisible(); }, 'pet menu missing');
  const menuText = await menuWindow.webContents.executeJavaScript('document.body.textContent');
  assert.match(menuText, /展开 \/ 收起聊天/); assert.match(menuText, /调整 Peter 大小/); assert.match(menuText, /查看活动状态/);
  assert.doesNotMatch(menuText, /Coming Soon/);
  const oldPet = pet;
  await menuWindow.webContents.executeJavaScript('document.querySelector("[data-size=\'420\']").click()');
  await wait(() => oldPet.isDestroyed(), 'old pet was not replaced');
  pet = BrowserWindow.getAllWindows().find(w => w !== chat);
  assert.equal(pet.getBounds().width, 420);
  console.log('SMOKE PASS: local dark chat, history, permission refusal, compressed image upload, voice media upload/transcript, TTS playback, activity controls, hide/reopen, resize, sandbox');
  clearTimeout(failSafe);
  server.close(); app.exit(0);
})().catch(error => { console.error(error); clearTimeout(failSafe); server?.close(); app.exit(1); });
