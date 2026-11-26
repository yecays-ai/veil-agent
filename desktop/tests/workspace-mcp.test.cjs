'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const script = readFileSync(path.join(__dirname, '../chat.js'), 'utf8');
const renderer = script.slice(script.indexOf('function renderMCP(){'), script.indexOf('function openMCPForm('));

function render({ service = {}, enabled = true, allow = true, success = true } = {}) {
  class Node {
    constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.attrs = {}; }
    get childElementCount() { return this.children.length; }
    append(...children) { this.children.push(...children); }
    replaceChildren() { this.children = []; }
    contains(target) { return this === target || this.children.some(child => child.contains(target)); }
    all(tag) { return [...(this.tag === tag ? [this] : []), ...this.children.flatMap(child => child.all(tag))]; }
    querySelectorAll() { return this.all('details').filter(child => child.open); }
    setAttribute(name, val) { this.attrs[name] = val; }
    focus() { document.activeElement = this; }
  }
  const document = { createElement: tag => new Node(tag), activeElement: null };
  const ids = Object.fromEntries(['mcp-add', 'mcp-enabled', 'mcp-status', 'mcp-list'].map(id => [id, new Node(id)]));
  const item = { id: 'sample', enabled: false, status: 'off', permission: 'ask', tools: ['echo'], ...service };
  const state = { enabled, services: [item] };
  const calls = [];
  const context = vm.createContext({ document, $: id => ids[id], mcpReady: true, mcpView: state, mcpActionError: '', setTimeout() {},
    confirm: () => allow, saveMCP: async fn => { calls.push(fn(state)); return success; } });
  const redraw = () => vm.runInContext(`${renderer}\nrenderMCP()`, context);
  redraw();
  return { ids, list: ids['mcp-list'], calls, redraw };
}

test('MCP shows existing on/off states, keeps details collapsed, and leaves errors visible', () => {
  const { ids, list, redraw } = render({ enabled: false, service: { enabled: true, status: 'error', error: 'fixture connection error' } });
  assert.equal(ids['mcp-enabled'].checked, false);
  assert.equal(list.all('input')[0].checked, true);
  assert.equal(list.all('input')[0].attrs.role, 'switch');
  const details = list.all('details')[0];
  assert.equal(details.open, false);
  assert.equal(details.all('p').some(p => p.textContent === 'echo'), true);
  assert.equal(list.children[0].children.some(p => p.textContent === 'fixture connection error'), true);
  assert.deepEqual(details.all('button').map(button => button.textContent), ['编辑', '移除']);
  details.open = true;
  redraw();
  assert.equal(list.all('details')[0].open, true);
});

test('service switch preserves permission and restores the prior state when saving fails', async () => {
  const { list, calls } = render({ success: false });
  const control = list.all('input')[0];
  control.checked = true;
  await control.onchange();
  assert.equal(calls[0].services[0].enabled, true);
  assert.equal(calls[0].services[0].permission, 'ask');
  assert.equal(control.checked, false);
});

test('managed service requires assent to enable and revokes the grant on disable', async () => {
  for (const [previous, allow, expectSave] of [[false, false, false], [false, true, true], [true, true, true]]) {
    const { list, calls } = render({ allow, service: { managed: 'nowhere', enabled: previous, direct_grant: previous } });
    const control = list.all('input')[0];
    control.checked = !previous;
    await control.onchange();
    assert.equal(calls.length, Number(expectSave));
    if (expectSave) assert.equal(calls[0].services[0].direct_grant, !previous);
    else assert.equal(control.checked, previous);
  }
});
