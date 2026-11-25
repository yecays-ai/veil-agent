"""Exercise the existing desktop calendar handlers without starting VEIL or Electron."""

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')

# Use the production Life Records block with fake scheduling data and an in-memory
# DOM/API. This does not attempt to test native rendering or the backend's writes.
HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('desktop/chat.js', 'utf8');
const start = source.indexOf('const LIFE_LABEL=');
const end = source.indexOf('async function approve(', start);
assert.ok(start >= 0 && end > start, 'locate the Life Records renderer and its bindings');

class Element {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.attrs = {};
    this.dataset = {};
    this.style = {};
    this.className = '';
    this.textContent = '';
    this.disabled = false;
    this.classList = {
      contains: name => this.className.split(/\s+/).includes(name),
      toggle: (name, on) => {
        const names = new Set(this.className.split(/\s+/).filter(Boolean));
        if (on ?? !names.has(name)) names.add(name); else names.delete(name);
        this.className = [...names].join(' ');
      },
      add: name => this.classList.toggle(name, true),
      remove: name => this.classList.toggle(name, false),
    };
  }
  append(...items) { for (const item of items) item.parent = this; this.children.push(...items); }
  prepend(item) { item.parent = this; this.children.unshift(item); }
  replaceChildren(...items) { this.children = []; this.append(...items); }
  remove() { this.parent?.children.splice(this.parent.children.indexOf(this), 1); }
  after(item) { item.parent = this.parent; this.parent.children.splice(this.parent.children.indexOf(this) + 1, 0, item); }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  getAttribute(key) { return this.attrs[key]; }
  // The detail/form HTML contains static templates. Only their id/class nodes
  // are needed for event binding, not parsing arbitrary document markup.
  set innerHTML(html) {
    this.replaceChildren();
    for (const [, tag, attrs] of html.matchAll(/<([a-z][a-z0-9]*)\b([^>]*)>/gi)) {
      const item = new Element(tag);
      item.id = attrs.match(/\bid="([^"]+)"/)?.[1];
      item.className = attrs.match(/\bclass="([^"]+)"/)?.[1] || '';
      this.append(item);
    }
  }
  querySelector(selector) {
    if (selector.startsWith('#')) return all(this).find(item => item.id === selector.slice(1));
    assert.ok(selector.startsWith('.'), `unexpected detail selector ${selector}`);
    return all(this).find(item => item.classList.contains(selector.slice(1)));
  }
  async click() {
    if (this.disabled) return;
    assert.equal(typeof this.onclick, 'function', `missing click binding: ${this.id || this.className}`);
    await this.onclick();
  }
}
const all = element => element.children.flatMap(child => [child, ...all(child)]);
const withClass = (element, cls) => all(element).filter(child => child.classList.contains(cls));
const life = Object.fromEntries(['life-records', 'life-day', 'life-count', 'life-detail', 'life-new', 'life-detail-new']
  .map(id => [id, Object.assign(new Element('div'), {id})]));
const shell = new Element('section');
shell.append(...Object.values(life));
const chatHtml = fs.readFileSync('desktop/chat.html', 'utf8');
for (const id of Object.keys(life)) assert.match(chatHtml, new RegExp(`id="${id}"`));
const tabs = key => [...chatHtml.matchAll(new RegExp(`data-life-${key}="([^"]+)"`, 'g'))]
  .map(([, value]) => Object.assign(new Element('button'), {dataset: {[`life${key === 'kind' ? 'Kind' : 'Owner'}`]: value}}));
const kindTabs = tabs('kind');
const ownerTabs = tabs('owner');
const date = life['life-day'];
const host = life['life-records'];
const detail = life['life-detail'];
const today = '2026-01-15';
const DateStub = class extends Date {
  constructor(...args) { super(...(args.length ? args : [2026, 0, 15, 12])); }
};
const row = (id, kind, title, day, extra = {}) => ({
  id, kind, title, date: day, state: 'active', repeat: 'none', created_by: 'Lan',
  created_at: '2025-12-01T10:00:00', updated_by: 'Lan', updated_at: '2026-01-14T10:00:00', ...extra,
});
const records = [
  row('last', 'event', 'Late fictional event', '2026-01-14', {time: '14:00'}),
  row('first', 'event', 'Early fictional event', '2026-01-14', {time: '09:00', created_by: 'Peter'}),
  row('once', 'note', 'One-off fictional todo', '2026-01-14'),
  row('daily', 'note', 'Daily fictional todo', today, {repeat: 'daily'}),
  row('today', 'event', 'Today fictional event', today),
  row('future', 'event', 'Future fictional event', '2026-01-16'),
];
const payloads = [];
const snapshot = () => ({records, daily_checks: {started: '2026-01-14', days: {
  '2026-01-14': {daily: {state: 'completed', at: '2026-01-14T16:00:00', by: 'Lan'}},
}}});
const api = async (url, body) => {
  assert.equal(url, '/api/life');
  let data = snapshot();
  if (body !== undefined) {
    const request = JSON.parse(JSON.stringify(body));
    payloads.push(request);
    const existing = records.findIndex(record => record.id === request.id);
    const record = existing === -1 ? row('new', request.kind, request.title, request.date, request)
      : {...records[existing], ...request};
    if (existing === -1) records.push(record); else records[existing] = record;
    data = {record};
  }
  return {status: 200, ok: true, json: async () => data};
};
const sandbox = {
  Date: DateStub, api, window: {veilDesktop: {}},
  $: id => life[id] || all(shell).find(item => item.id === id),
  notify: (...args) => assert.fail(`unexpected error notice: ${args}`),
  document: {
    createElement: tag => new Element(tag),
    createTextNode: text => Object.assign(new Element('#text'), {textContent: text}),
    querySelectorAll: selector => {
      if (selector === '[data-life-kind]') return kindTabs;
      assert.equal(selector, '[data-life-owner]');
      return ownerTabs;
    },
  },
};
const context = vm.createContext(sandbox);
vm.runInContext(source.slice(start, end), context, {filename: 'desktop/chat.js (Life Records)'});
const selectDay = (value) => {
  const label = new Date(value + 'T12:00:00').toLocaleDateString('zh-CN', {year: 'numeric', month: 'long', day: 'numeric'});
  const button = withClass(host, 'calendar-day-number').map(number => number.parent)
    .find(item => item.getAttribute('aria-label')?.split('，')[0] === label);
  assert.ok(button, `date missing: ${value}`);
  return button;
};
const titles = () => withClass(host, 'agenda-title').map(item => item.textContent);
const button = label => all(host).find(item => item.tagName === 'button'
  && (item.getAttribute('aria-label') === label || item.textContent === label));
const byTitle = title => withClass(host, 'agenda-title').find(item => item.textContent === title);
const drain = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  await vm.runInContext('loadLife()', context);
  assert.equal(date.value, today);
  assert.equal(selectDay(today).getAttribute('aria-pressed'), 'true');
  assert.ok(titles().includes('Today fictional event'));
  await button('上个月').click();
  assert.equal(date.value, '2025-12-01');
  await button('下个月').click();
  assert.equal(date.value, '2026-01-01');
  await selectDay('2026-01-14').click();
  assert.equal(date.value, '2026-01-14');
  assert.equal(selectDay('2026-01-14').getAttribute('aria-pressed'), 'true');
  assert.equal(selectDay(today).getAttribute('aria-pressed'), 'false');
  assert.deepEqual(titles(), ['Early fictional event', 'Late fictional event', 'One-off fictional todo', 'Daily fictional todo']);
  const historicCheck = button('那天完成了：Daily fictional todo');
  assert.equal(historicCheck.disabled, true);
  await historicCheck.click();
  assert.equal(payloads.length, 0, 'past daily checks must be read-only');

  await byTitle('Early fictional event').click();
  assert.equal(detail.querySelector('.detail-title').textContent, 'Early fictional event');
  await detail.querySelector('#life-edit').click();
  assert.equal(detail.querySelector('#lf-date').value, '2026-01-14');
  assert.equal(detail.querySelector('#lf-time').value, '09:00');
  detail.querySelector('#lf-title').value = 'Edited fictional event';
  await detail.querySelector('#lf-save').click();
  await drain();
  assert.equal(payloads.at(-1).id, 'first');
  assert.equal(payloads.at(-1).title, 'Edited fictional event');
  assert.equal(detail.querySelector('.detail-title').textContent, 'Edited fictional event');

  await button('＋ 写一条待办').click();
  assert.equal(detail.querySelector('#lf-kind').value, 'note');
  assert.equal(detail.querySelector('#lf-date').value, '2026-01-14');
  detail.querySelector('#lf-title').value = 'New fictional todo';
  await detail.querySelector('#lf-save').click();
  await drain();
  assert.equal(payloads.at(-1).date, '2026-01-14');
  assert.equal(payloads.at(-1).kind, 'note');
  assert.ok(titles().includes('New fictional todo'));

  await selectDay('2026-01-16').click();
  assert.ok(titles().includes('Future fictional event'));
  assert.equal(button('还没到这一天：Daily fictional todo').disabled, true);
  await button('今天').click();
  assert.equal(date.value, today);
  const currentCheck = button('标记完成：Daily fictional todo');
  assert.equal(currentCheck.disabled, false);
  await currentCheck.click();
  assert.deepEqual(payloads.at(-1), {id: 'daily', state: 'completed'});
  assert.ok(button('改回未完成：Daily fictional todo'));

  // The calendar still exists under 日程; 待办 retains its dedicated sticky-note view.
  await kindTabs.find(item => item.dataset.lifeKind === 'event').click();
  assert.equal(withClass(host, 'life-calendar').length, 1);
  assert.equal(withClass(host, 'life-card').length, 1);
  await kindTabs.find(item => item.dataset.lifeKind === 'note').click();
  assert.ok(withClass(host, 'life-sticky').length > 0);
  await kindTabs.find(item => item.dataset.lifeKind === 'all').click();
  assert.equal(withClass(host, 'life-calendar').length, 1);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


class DesktopLifeCalendarContractTests(unittest.TestCase):
    @unittest.skipUnless(NODE, 'Node.js is needed to execute the desktop renderer handlers')
    def test_calendar_navigation_and_todo_actions(self):
        result = subprocess.run(
            [NODE, '-'], input=HARNESS.encode('utf-8'), cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout.decode('utf-8', errors='replace'))
