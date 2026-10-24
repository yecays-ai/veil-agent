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
  throw new Error(typeof message === 'function' ? await message() : message);
};
let temp, server, mediaPermission;
function wavFixture() {
  const rate=8000,count=8000,data=Buffer.alloc(count*2),wav=Buffer.alloc(44+data.length);
  for(let i=0;i<count;i++)data.writeInt16LE(Math.round(Math.sin(2*Math.PI*440*i/rate)*12000),i*2);
  wav.write('RIFF',0);wav.writeUInt32LE(36+data.length,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);
  wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(rate,24);wav.writeUInt32LE(rate*2,28);
  wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(data.length,40);data.copy(wav,44);
  return wav;
}
const failSafe = setTimeout(() => { console.error('SMOKE timeout'); app.exit(1); }, 70000);
(async () => {
  temp = await fs.mkdtemp(path.join(os.tmpdir(), 'veil-ui-smoke-'));
  app.setPath('userData', temp);
  await fs.writeFile(path.join(temp, 'desktop.json'), JSON.stringify({ root: temp, size: 320 }));
  const token = 'fictional_token_123456789';
  let nextId = 3, pending = null, sends = 0, approvals = 0, images = 0, imageSendAttempts = 0, voices = 0, failVoiceUploadOnce = false;
  const requests = [], approvalBodies = [];
  const media = new Map();
  const voiceFixture=wavFixture();
  media.set('fictional-reply.wav',{id:'reply',filename:'fictional-reply.wav',type:'audio/wav',bytes:voiceFixture});
  const events = [
    { id: 1, message_id: 'fictional-history', request_id: 'fictional-history-request', kind: 'assistant', text: 'Fictional saved history', history: true, time: new Date().toISOString() },
    { id: 2, message_id: 'fictional-spoken-reply', request_id: 'fictional-reply-request', kind: 'assistant', text: 'Fictional spoken reply', media: { id: 'reply', filename: 'fictional-reply.wav', mime: 'audio/wav' }, history: true, time: new Date().toISOString() }
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
      if (body.media?.id?.startsWith('fictional-voice')) {
        assert.equal(body.text, '[voice message]', 'recording is submitted before transcription completes');
        assert.equal(body.request_id, `voice-send-${body.media.id}`);
      }
      if (body.media?.id === 'fictional-image' && imageSendAttempts++ === 0) {
        json(res, { error: 'busy' }, 409); return;
      }
      if (body.text === 'fictional hello') {
        sends++; events.push({ id: nextId++, message_id: body.request_id, kind: 'user', text: body.text, request_id: body.request_id, time: new Date().toISOString() });
        pending = nextId; events.push({ id: nextId++, kind: 'approval', text: 'Fictional permission request', time: new Date().toISOString() });
      } else {
        sends++;
        const attached = body.media ? [...media.values()].find(x => x.id === body.media.id) : null;
        events.push({ id: nextId++, message_id: body.request_id, kind: 'user', text: body.text, request_id: body.request_id,
          media: attached?.eventMedia, time: new Date().toISOString() });
        if (body.media?.id?.startsWith('fictional-voice')) setTimeout(() => events.push({ id: nextId++, kind: 'voice_status',
          media: { ...attached.eventMedia, transcript: body.media.id === 'fictional-voice' ? 'fictional voice transcript' : 'fictional retry transcript' }, time: new Date().toISOString() }), 180);
      }
      json(res, {});
    } else if (url.pathname === '/api/approve') {
      const body = await readBody(req);
      approvalBodies.push(body); approvals++; pending = null; json(res, {});
    } else if (url.pathname === '/api/media') {
      const body = await readBody(req); assert.equal(body.mime, 'image/jpeg');
      images++;
      const filename = 'fictional-image.jpg';
      const bytes=Buffer.from(body.data,'base64');assert.ok(bytes.length>0&&bytes.length<5*1024*1024);
      const entry = { id: 'fictional-image', filename, type: 'image/jpeg', bytes,
        eventMedia: { id: 'fictional-image', filename, mime: 'image/jpeg', kind: 'image', name: body.name } };
      media.set(filename, entry); json(res, { media: entry.eventMedia });
    } else if (url.pathname === '/api/voice') {
      const body = await readBody(req); assert.equal(body.mime, 'audio/wav'); assert.ok(body.data);
      assert.equal(body.send_first, true, 'desktop uploads must start background transcription after delivery');
      if (failVoiceUploadOnce) { failVoiceUploadOnce = false; json(res, { error: 'fictional upload failure' }, 500); return; }
      const id = voices === 0 ? 'fictional-voice' : 'fictional-voice-retry';
      voices++;
      const filename = id + '.wav';
      const entry = { id, filename, type: 'audio/wav', bytes: Buffer.from(body.data, 'base64'),
        eventMedia: { id, filename, mime: 'audio/wav', kind: 'voice', transcript: '' } };
      media.set(filename, entry); json(res, { media: entry.eventMedia }, 202);
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
  mediaPermission = require('../main.cjs');
  app.setPath('userData', temp);
  app.setPath('sessionData', path.join(temp, 'session'));
  await app.whenReady();
  let pet;
  await wait(() => { pet = BrowserWindow.getAllWindows().find(w => w.webContents.getURL().endsWith('pet.html')); return pet?.isVisible(); }, 'pet not visible');
  assert.equal(pet.getBounds().width, 320);
  const prefs = pet.webContents.getLastWebPreferences();
  assert.equal(prefs.contextIsolation, true); assert.equal(prefs.sandbox, true); assert.equal(prefs.nodeIntegration, false);
  await wait(() => pet.webContents.executeJavaScript('document.querySelector("img").complete && document.querySelector("img").naturalWidth > 0'), 'Peter image missing');
  const toggle = () => pet.webContents.executeJavaScript('document.querySelector("#pet-toggle").dispatchEvent(new KeyboardEvent("keydown", {key:"Enter",bubbles:true}))');
  await toggle();
  let chat;
  await wait(() => { chat = BrowserWindow.getAllWindows().find(w => w !== pet); return chat?.isVisible(); }, 'chat not visible');
  chat.webContents.on('console-message', (_event, level, message, line, sourceId) => console.log(`CHAT CONSOLE level=${level} ${sourceId}:${line} ${message}`));
  chat.webContents.on('render-process-gone', (_event, details) => console.error('CHAT RENDER PROCESS GONE', JSON.stringify(details)));
  await wait(async () => {
    try { return await chat.webContents.executeJavaScript('document.body.textContent.includes("Fictional saved history")'); }
    catch { return false; }
  }, 'history missing');
  assert.ok(chat.webContents.getURL().startsWith('file:'), 'desktop chat must load its own local UI');
  const chatPrefs = chat.webContents.getLastWebPreferences();
  assert.equal(chatPrefs.contextIsolation, true); assert.equal(chatPrefs.sandbox, true); assert.equal(chatPrefs.nodeIntegration, false);
  const requestingUrl=chat.webContents.getURL();
  assert.equal(mediaPermission.canRequestChatMedia(chat.webContents,'media',{requestingUrl,mediaTypes:['audio']},chat),true);
  assert.equal(mediaPermission.canRequestChatMedia(chat.webContents,'media',{requestingUrl,mediaTypes:['video']},chat),false);
  assert.equal(mediaPermission.canRequestChatMedia(chat.webContents,'media',{requestingUrl,mediaTypes:[]},chat),false);
  assert.equal(mediaPermission.canRequestChatMedia(chat.webContents,'media',{mediaTypes:['audio']},chat),false);
  assert.equal(mediaPermission.canCheckChatMedia(chat.webContents,'media',{mediaType:'audio',requestingUrl,isMainFrame:true},chat),true);
  assert.equal(mediaPermission.canCheckChatMedia(chat.webContents,'media',{mediaType:'video',requestingUrl,isMainFrame:true},chat),false);
  assert.equal(mediaPermission.canCheckChatMedia(chat.webContents,'media',{requestingUrl,isMainFrame:true},chat),false);
  assert.equal(mediaPermission.canCheckChatMedia(null,'media',{mediaType:'audio',requestingUrl,isMainFrame:true},chat),false);
  assert.equal(await chat.webContents.executeJavaScript('typeof window.veilDesktop.request'), 'function');
  assert.equal(await chat.webContents.executeJavaScript('typeof require'), 'undefined');
  assert.equal(await chat.webContents.executeJavaScript('document.querySelector("#speech-toggle").disabled'), false);
  assert.equal(await chat.webContents.executeJavaScript('document.querySelector("#image-button").disabled'), false);
  assert.equal(await chat.webContents.executeJavaScript('document.body.textContent.includes("Coming Soon")'), false);
  assert.equal(await chat.webContents.executeJavaScript('document.querySelector("#speech-language")'), null, 'dictation language is detected automatically');
  await wait(() => chat.webContents.executeJavaScript('!!document.querySelector(".msg-voice audio")'), 'saved voice reply did not render');
  await wait(() => chat.webContents.executeJavaScript('!document.querySelector("#send").disabled'), 'send disabled');

  await chat.webContents.executeJavaScript('document.querySelector("#box").value="fictional hello"; document.querySelector("#send").click()');
  await wait(() => sends === 1, 'text message not delivered');
  await wait(() => chat.webContents.executeJavaScript('!!document.querySelector(".approval button:not(.yes)")'), 'approval missing');
  await chat.webContents.executeJavaScript('document.querySelector(".approval button:not(.yes)").click()');
  await wait(() => approvals === 1, `permission refusal was not delivered; requests=${requests.join(',')}; approval=${JSON.stringify(approvalBodies)}; card=${JSON.stringify(await chat.webContents.executeJavaScript('(()=>{const c=document.querySelector(".approval");return c&&{id:c.dataset.id,className:c.className,status:c.querySelector(".status").textContent,buttons:[...c.querySelectorAll("button")].map(b=>({text:b.textContent,disabled:b.disabled}))}})()'))}`);
  assert.equal(approvalBodies[0].answer, 'n'); assert.equal(approvalBodies[0].id, 4);

  await chat.webContents.executeJavaScript(`(async()=>{const canvas=document.createElement('canvas');canvas.width=1800;canvas.height=1800;const ctx=canvas.getContext('2d');const pixels=ctx.createImageData(1800,1800);let n=0x13579bdf;for(let i=0;i<pixels.data.length;i+=4){n^=n<<13;n^=n>>>17;n^=n<<5;pixels.data[i]=n&255;pixels.data[i+1]=(n>>>8)&255;pixels.data[i+2]=(n>>>16)&255;pixels.data[i+3]=255}ctx.putImageData(pixels,0,0);const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(blob.size<=5*1024*1024)throw new Error('fixture must exceed 5 MB');const file=new File([blob],'fictional.png',{type:'image/png'});const input=document.querySelector('#image-file');Object.defineProperty(input,'files',{configurable:true,value:[file]});return input.onchange()})()`);
  await wait(() => chat.webContents.executeJavaScript('!document.querySelector("#image-preview").hidden'), `image preview missing: ${JSON.stringify(await chat.webContents.executeJavaScript('(()=>({errors:[...document.querySelectorAll(".error")].map(e=>e.textContent),input:document.querySelector("#image-file").files?.[0]&&{name:document.querySelector("#image-file").files[0].name,type:document.querySelector("#image-file").files[0].type,size:document.querySelector("#image-file").files[0].size},preview:document.querySelector("#image-preview").textContent,hidden:document.querySelector("#image-preview").hidden}))()'))}`);
  await chat.webContents.executeJavaScript('document.querySelector("#box").value="fictional image caption"; document.querySelector("#send").click()');
  await wait(() => images === 1, 'image was not uploaded');
  await wait(() => imageSendAttempts === 1 && chat.webContents.executeJavaScript('!document.querySelector("#send").disabled && [...document.querySelectorAll(".error")].some(e=>e.textContent.includes("正在处理上一条消息"))'), 'busy image send did not preserve a retryable preview');
  assert.equal(await chat.webContents.executeJavaScript('!document.querySelector("#image-preview").hidden'), true, 'busy send must keep the selected image');
  await chat.webContents.executeJavaScript('document.querySelector("#send").click()');
  await wait(() => imageSendAttempts === 2 && sends === 2, 'busy image send retry did not complete');
  await wait(() => chat.webContents.executeJavaScript('!!document.querySelector(".msg-image")'), 'uploaded image message missing');

  const voiceBase64=voiceFixture.toString('base64');
  await chat.webContents.executeJavaScript(`(()=>{
    const bytes=Uint8Array.from(atob('${voiceBase64}'),c=>c.charCodeAt(0));
    Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async()=>({getTracks:()=>[{stop(){}}]})}});
    class FixtureRecorder{constructor(){this.mimeType='audio/wav';this.state='inactive'}start(){this.state='recording'}stop(){this.state='inactive';this.ondataavailable({data:new Blob([bytes],{type:'audio/wav'})});this.onstop()}}
    FixtureRecorder.isTypeSupported=()=>false;Object.defineProperty(window,'MediaRecorder',{configurable:true,value:FixtureRecorder});
    document.querySelector('#voice-hold').dispatchEvent(new PointerEvent('pointerdown',{button:0,bubbles:true}));
  })()`);
  await wait(() => chat.webContents.executeJavaScript('document.querySelector("#voice-hold").dataset.recording === "true"'), 'voice recording did not start');
  await chat.webContents.executeJavaScript(`document.querySelector('#voice-hold').dispatchEvent(new PointerEvent('pointerup',{button:0,bubbles:true}))`);
  await wait(() => voices === 1 && sends === 3, 'voice upload and transcript message were not delivered');
  const userVoiceText='.msg[data-media-id="fictional-voice"] .voice-transcript';
  const userVoiceWrap='.msg[data-media-id="fictional-voice"] .msg-voice';
  await wait(() => chat.webContents.executeJavaScript(`!!document.querySelector('${userVoiceWrap} .voice-transcript')`), 'voice bubble missing');
  assert.equal(await chat.webContents.executeJavaScript(`document.querySelector('${userVoiceText}').hidden`),true,'voice transcript must stay hidden by default');
  await wait(() => chat.webContents.executeJavaScript(`document.querySelector('${userVoiceText}').textContent === "fictional voice transcript"`), async () => 'background transcription status missing: ' + JSON.stringify(events) + await chat.webContents.executeJavaScript('document.body.textContent.slice(-700)'));
  await chat.webContents.executeJavaScript(`document.querySelector('${userVoiceWrap}').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:80,clientY:80}))`);
  assert.deepEqual(await chat.webContents.executeJavaScript('[...document.querySelectorAll(".chat-message-actions .chat-action-item")].map(button=>button.textContent.trim())'),[
    'Transcribe','Favorite','Download','Delete','Recall'
  ]);
  await chat.webContents.executeJavaScript(`document.querySelector('.chat-message-actions .chat-action-item').click()`);
  assert.equal(await chat.webContents.executeJavaScript(`document.querySelector('${userVoiceText}').hidden`),false);

  failVoiceUploadOnce = true;
  await chat.webContents.executeJavaScript(`document.querySelector('#voice-hold').dispatchEvent(new PointerEvent('pointerdown',{button:0,bubbles:true}))`);
  await wait(() => chat.webContents.executeJavaScript('document.querySelector("#voice-hold").dataset.recording === "true"'), 'retry voice recording did not start');
  await chat.webContents.executeJavaScript(`document.querySelector('#voice-hold').dispatchEvent(new PointerEvent('pointerup',{button:0,bubbles:true}))`);
  await wait(() => chat.webContents.executeJavaScript('!!document.querySelector(".msg[data-media-id^=local-] .voice-retry")'), 'failed voice upload was not retained for retry');
  await chat.webContents.executeJavaScript('document.querySelector(".msg[data-media-id^=local-] .voice-retry").click()');
  await wait(() => voices === 2 && sends === 4, 'retained voice was not delivered after retry');
  await wait(() => chat.webContents.executeJavaScript('!!document.querySelector(".msg[data-media-id=fictional-voice-retry] .msg-voice")'), 'retried voice message id was not updated');
  await wait(() => chat.webContents.executeJavaScript('!document.querySelector(".msg[data-media-id=fictional-voice-retry] .voice-retry")'), 'successful upload retry still shows an error');

  await chat.webContents.executeJavaScript('document.querySelector("[data-page=memory]").click()');
  await wait(() => chat.webContents.executeJavaScript('!document.querySelector("#memory-view").hidden'), 'memory page did not open');
  assert.equal(await chat.webContents.executeJavaScript('document.querySelector("#memory-panel").hidden'), false, 'real Memory entry should appear when available');
  await wait(() => chat.webContents.executeJavaScript('document.querySelector(".msg-image").complete && document.querySelector(".msg-image").naturalWidth > 0'), 'compressed image did not decode');
  await wait(() => chat.webContents.executeJavaScript('[...document.querySelectorAll(".msg-voice audio")].some(a=>a.readyState>=2)'), 'voice media did not load');
  const historyReloaded = new Promise(resolve => chat.webContents.once('did-finish-load', resolve));
  chat.webContents.reload();
  await historyReloaded;
  await wait(() => chat.webContents.executeJavaScript('!!document.querySelector(".msg-image") && !!document.querySelector(".msg[data-media-id=fictional-voice] .msg-voice") && !!document.querySelector(".msg[data-media-id=fictional-voice-retry] .msg-voice")'), 'image and sent voice messages did not restore from history');
  assert.equal(await chat.webContents.executeJavaScript('document.querySelector(".msg[data-media-id=fictional-voice] .voice-transcript").hidden && document.querySelector(".msg[data-media-id=fictional-voice-retry] .voice-transcript").hidden'),true,'restored voice transcripts should remain hidden');
  await chat.webContents.executeJavaScript(`document.querySelector('.msg[data-media-id="fictional-voice"] .msg-voice').dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,clientX:80,clientY:80}))`);
  await chat.webContents.executeJavaScript(`document.querySelector('.chat-message-actions .chat-action-item').click()`);
  assert.equal(await chat.webContents.executeJavaScript('!document.querySelector(".msg[data-media-id=fictional-voice] .voice-transcript").hidden'),true,'transcription should appear only after choosing the menu item');
  await wait(() => chat.webContents.executeJavaScript('document.querySelector(".msg-image").complete && document.querySelector(".msg-image").naturalWidth > 0'), 'restored image did not decode');
  await fs.mkdir(path.join(__dirname, '../test-output'), { recursive: true });
  await fs.writeFile(path.join(__dirname, '../test-output/pet.png'), (await pet.webContents.capturePage()).toPNG());
  await fs.writeFile(path.join(__dirname, '../test-output/chat.png'), (await chat.webContents.capturePage()).toPNG());

  chat.close(); assert.equal(chat.isDestroyed(), false); assert.equal(chat.isVisible(), false);
  await toggle(); await wait(() => chat.isVisible(), 'chat did not reopen');
  assert.equal(sends, 4); assert.equal(approvals, 1);
  await pet.webContents.executeJavaScript('window.companion.menu()');
  let menuWindow;
  await wait(() => { menuWindow = BrowserWindow.getAllWindows().find(w => w !== pet && w !== chat); return menuWindow?.isVisible(); }, 'pet menu missing');
  const menuText = await menuWindow.webContents.executeJavaScript('document.body.textContent');
  assert.match(menuText, /展开 \/ 收起聊天/); assert.match(menuText, /外观设置/); assert.match(menuText, /查看自主活动/);
  assert.doesNotMatch(menuText, /Coming Soon/);
  await menuWindow.webContents.executeJavaScript('document.querySelector("[data-action=appearance]").click()');
  await wait(() => chat.webContents.executeJavaScript('!document.querySelector("#more-view").hidden'), 'appearance settings did not open');
  console.log('SMOKE PASS: local dark chat, history, audio-only permissions, >5 MB image compression and decode, automatic-language dictation, send-first voice with hidden transcript and appearance settings, right-click menu, audio loading, activity controls, hide/reopen, sandbox');
  clearTimeout(failSafe);
  server.close(); app.exit(0);
})().catch(error => { console.error(error); clearTimeout(failSafe); server?.close(); app.exit(1); });
