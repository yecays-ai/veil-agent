// Execute the actual page script with a small DOM and controlled clock, without network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const script = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8')
  .split('<script>')[1].split('</script>')[0].replace('setBusy(true);poll();box.focus();', '');

function page() {
  let clock = Date.parse('2026-01-01T12:00:00Z'), nextId = 0;
  const timers = new Map();
  class Element {
    constructor() { this.children = []; this.dataset = {}; this.style = {}; this.textContent = ''; }
    appendChild(e) { e.parent = this; this.children.push(e); }
    insertBefore(e, target) { e.parent = this; this.children.splice(this.children.indexOf(target), 0, e); }
    remove() { this.parent.children.splice(this.parent.children.indexOf(this), 1); }
    addEventListener() {}
    setAttribute() {}
  }
  const elements = new Map();
  const document = {
    getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
    createElement() { return new Element(); }, querySelectorAll() { return []; },
    documentElement: { setAttribute() {}, removeAttribute() {} },
  };
  const context = vm.createContext({ document, URLSearchParams,
    location: { search: '', pathname: '/' }, history: {},
    localStorage: { getItem() { return null; } },
    Date: class extends Date { static now() { return clock; } },
    setTimeout(fn, delay) { const id = ++nextId; timers.set(id, { at: clock + delay, fn }); return id; },
    clearTimeout(id) { timers.delete(id); },
  });
  vm.runInContext(script, context);
  const run = code => vm.runInContext(code, context);
  return {
    run, elements, timers,
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
console.log('Page pacing, Unicode, grouping, order and refresh passed.');
