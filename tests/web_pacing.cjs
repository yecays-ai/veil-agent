// Execute the actual page script with a small DOM and controlled clock, without network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const script = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8')
  .split('<script>')[1].split('</script>')[0].replace('setBusy(true);poll();box.focus();', '');

function page(options = {}) {
  let clock = Date.parse('2026-01-01T12:00:00Z'), nextId = 0;
  const timers = new Map();
  const listeners = new Map();
  const storage = options.storage || new Map();
  const recognizers = [];
  class FakeSpeechRecognition {
    constructor() { this.lang = ''; this.results = []; recognizers.push(this); }
    start() { this.onstart?.(); }
    stop() { this.onend?.(); }
    abort() { this.onend?.(); }
  }
  class Element {
    constructor(id = '') { this.id = id; this.children = []; this.dataset = {}; this.style = {}; this.textContent = ''; this.value = id === 'speech-language' ? 'zh-CN' : ''; this.scrollHeight = 0; this.attributes = {}; this.handlers = {}; }
    appendChild(e) { e.parent = this; this.children.push(e); }
    insertBefore(e, target) { e.parent = this; this.children.splice(this.children.indexOf(target), 0, e); }
    remove() { this.parent.children.splice(this.parent.children.indexOf(this), 1); }
    addEventListener(name, fn) { this.handlers[name] = fn; }
    setAttribute(name, value) { this.attributes[name] = value; }
    focus() {}
  }
  const elements = new Map();
  const document = {
    getElementById(id) { if (!elements.has(id)) elements.set(id, new Element(id)); return elements.get(id); },
    createElement() { return new Element(); }, querySelectorAll() { return []; },
    documentElement: { setAttribute() {}, removeAttribute() {} },
  };
  let fetchCount = 0;
  const context = vm.createContext({ document, URLSearchParams,
    location: { search: '', pathname: '/' }, history: {},
    localStorage: { getItem() { return null; } },
    sessionStorage: { getItem(key) { return storage.has(key) ? storage.get(key) : null; }, setItem(key, value) { storage.set(key, String(value)); }, removeItem(key) { storage.delete(key); } },
    window: { SpeechRecognition: options.speechSupported === false ? undefined : FakeSpeechRecognition,
      addEventListener(name, fn) { listeners.set(name, fn); } },
    fetch() { fetchCount++; return Promise.resolve({ ok: true, json: async () => ({}) }); },
    crypto: { randomUUID() { return 'fake-request-id'; } },
    Date: class extends Date { static now() { return clock; } },
    setTimeout(fn, delay) { const id = ++nextId; timers.set(id, { at: clock + delay, fn }); return id; },
    clearTimeout(id) { timers.delete(id); },
  });
  vm.runInContext(script, context);
  const run = code => vm.runInContext(code, context);
  return {
    run, elements, timers, storage, recognizers, listeners,
    fetchCount: () => fetchCount,
    event(kind, text, initial = false, ago = 0) {
      context.event = { kind, text, id: ++nextId, time: new Date(clock - ago).toISOString() };
      run(`receiveEvent(event,${initial})`);
    },
    advance(ms) {
      const end = clock + ms;
      while (true) {
        const next = [...timers].sort((a, b) => a[1].at - b[1].at)[0];
        if (!next || next[1].at > end) break;
        clock = next[1].at; timers.delete(next[0]); next[1].fn();
      }
      clock = end;
    },
    messages() { return elements.get('col').children.filter(e => (e.className || '').startsWith('msg ')); },
    timestamps() { return elements.get('col').children.filter(e => e.className === 'timestamp'); },
  };
}

const p = page();
p.run(`renderActivity({started_at:'start',finished_at:'end',result:'部分完成',
  result_reason:'记忆连接不可用',decision:'本次中断',lab_files_changed:['lab/progress.md'],
  tools:[{tool:'pulse',path:'<script>fixture</script>',state:'出错'},
    {tool:'append_file',path:'lab/progress.md',state:'成功',result_state:'结果过长'}],next_step:'核实后继续',cost_cny:'0.1'})`);
assert.equal(p.elements.get('activity-details').hidden, false);
assert.match(p.elements.get('activity-record').textContent, /部分完成.*记忆连接不可用/);
assert.match(p.elements.get('activity-record').textContent, /lab\/progress.md/);
assert.match(p.elements.get('activity-record').textContent, /<script>fixture<\/script>/);
assert.match(p.elements.get('activity-record').textContent, /成功；结果过长，未提供给模型/);
p.run('renderActivity(null)');
assert.equal(p.elements.get('activity-details').hidden, true);
assert.equal(p.run('bubbleDelay("😀".repeat(60))'), 1200);
assert.equal(p.run('bubbleDelay("中".repeat(61))'), 2400);
assert.equal(p.run('bubbleDelay("a".repeat(200))'), 2400);
assert.equal(p.run('bubbleDelay("a".repeat(201))'), 3600);
p.event('user', 'hello', false, 700);
p.event('assistant', '你好。Hello 👋');
p.event('assistant', '中'.repeat(100));
p.event('assistant', 'a'.repeat(201));
p.run('setBusy(false)');
assert.equal(p.elements.get('send').disabled, true);
p.advance(499); assert.equal(p.messages().length, 1);
p.advance(1); assert.equal(p.messages().length, 2);
p.advance(2399); assert.equal(p.messages().length, 2);
p.advance(1); assert.equal(p.messages().length, 3);
assert.ok(p.messages()[2].className.includes('grouped'));
p.advance(3599); assert.equal(p.messages().length, 3);
p.advance(1); assert.equal(p.messages().length, 4);
assert.equal(p.elements.get('send').disabled, false);
assert.equal(p.messages()[1].innerHTML, '你好。Hello 👋');
assert.equal(p.messages()[3].innerHTML, 'a'.repeat(201));

const refreshed = page();
refreshed.event('assistant', 'first', true);
refreshed.event('assistant', 'second', true);
assert.equal(refreshed.messages().length, 2);
assert.equal(refreshed.timers.size, 0);
assert.equal(refreshed.timestamps().length, 1);
refreshed.advance(15 * 60 * 1000);
refreshed.event('assistant', 'new group', true);
assert.equal(refreshed.timestamps().length, 2);
assert.ok(!refreshed.messages()[2].className.includes('grouped'));

const slow = page();
slow.event('user', 'hello', false, 5000);
slow.event('assistant', 'already waited');
assert.equal(slow.messages().length, 2);
assert.equal(slow.timers.size, 0);

const voice = page();
voice.run('setBusy(false)');
voice.elements.get('box').value = 'Peter,';
voice.elements.get('speech-language').value = 'en-US';
voice.elements.get('speech-toggle').onclick();
const recognition = voice.recognizers[0];
assert.equal(recognition.lang, 'en-US');
assert.equal(voice.elements.get('box').disabled, true);
assert.equal(voice.elements.get('speech-language').disabled, true);
assert.equal(voice.elements.get('send').disabled, true);
const result = (text, isFinal) => Object.assign([{ transcript: text }], { isFinal });
recognition.onresult({ results: [result('I wanted to tell you something.', false)] });
assert.equal(voice.elements.get('box').value, 'Peter, I wanted to tell you something.');
voice.run('send()');
assert.equal(voice.fetchCount(), 0, 'recognition must never submit a message');
recognition.onresult({ results: [result('I wanted to tell you something.', false)] });
assert.equal(voice.elements.get('box').value, 'Peter, I wanted to tell you something.', 'repeated interim events must not duplicate text');
recognition.onresult({ results: [result('I wanted to tell you something.', true)] });
assert.equal(voice.elements.get('box').value, 'Peter, I wanted to tell you something.');
voice.elements.get('speech-toggle').onclick();
assert.equal(voice.elements.get('box').disabled, false);
assert.equal(voice.elements.get('speech-status').textContent, '识别完成，可编辑后发送。');
assert.equal(voice.elements.get('send').disabled, false);
assert.equal(voice.storage.get('veil-speech-draft'), 'Peter, I wanted to tell you something.');
const restored = page({ storage: voice.storage, speechSupported: false });
assert.equal(restored.elements.get('box').value, 'Peter, I wanted to tell you something.', 'confirmed voice text should survive refresh');
assert.equal(restored.elements.get('speech-toggle').disabled, true);
assert.match(restored.elements.get('speech-status').textContent, /不支持/);

const chinese = page();
chinese.run('setBusy(false)');
chinese.elements.get('box').value = '你好，';
chinese.elements.get('speech-language').value = 'zh-CN';
chinese.elements.get('speech-toggle').onclick();
chinese.recognizers[0].onresult({ results: [result('今天晚上我准备早点睡觉。', true)] });
assert.equal(chinese.elements.get('box').value, '你好，今天晚上我准备早点睡觉。');
chinese.elements.get('speech-toggle').onclick();
assert.equal(chinese.elements.get('box').value, '你好，今天晚上我准备早点睡觉。', 'final results must append once and keep Chinese order');

const denied = page();
denied.run('setBusy(false)');
denied.elements.get('box').value = '已有文字';
denied.elements.get('speech-toggle').onclick();
denied.recognizers[0].onerror({ error: 'not-allowed' });
denied.recognizers[0].onend();
assert.match(denied.elements.get('speech-status').textContent, /权限被拒绝/);
assert.equal(denied.elements.get('box').value, '已有文字');
assert.equal(denied.recognizers.length, 1, 'recognition errors must not auto-retry');
assert.equal(denied.fetchCount(), 0);

const noSpeech = page();
noSpeech.run('setBusy(false)');
noSpeech.elements.get('speech-toggle').onclick();
noSpeech.recognizers[0].onerror({ error: 'no-speech' });
noSpeech.recognizers[0].onend();
assert.match(noSpeech.elements.get('speech-status').textContent, /没有检测到语音/);

(async () => {
  await voice.run('send()');
  assert.equal(voice.fetchCount(), 1, 'the existing send path submits only after explicit send');
  assert.equal(voice.elements.get('box').value, '');
  assert.equal(voice.storage.has('veil-speech-draft'), false);
  console.log('Page pacing and speech-input lifecycle, append, error, refresh, and explicit-send checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });

