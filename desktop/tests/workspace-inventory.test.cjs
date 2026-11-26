'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const script = readFileSync(path.join(__dirname, '../workspace-inventory.js'), 'utf8');

function setup(response) {
  const document = { createElement: tag => new Element(tag) };
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.value = ''; this.textContent = ''; this.listeners = {}; this.ownerDocument = document; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    contains(target) { return this === target || this.children.some(child => child.contains(target)); }
    setAttribute() {}
    focus() { document.activeElement = this; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
  }
  const host = document.createElement('main');
  const responses = Array.isArray(response) ? response : [response];
  const requests = [];
  const window = { veilDesktop: { request: async (...args) => { requests.push(args); return responses[requests.length - 1]; } } };
  vm.runInNewContext(script, { window });
  const descendants = (node, tag) => [ ...(node.tag === tag ? [node] : []), ...node.children.flatMap(child => descendants(child, tag)) ];
  return { host, requests, render: () => window.renderWorkspaceInventory(host), descendants: tag => descendants(host, tag) };
}
const fixture = n => ({ id: `ITEM-${n}`, domain: 'fixture', module: 'test', item: `<Inventory ${n}>`, key: `key${n}`, source: '', storage: '', default: '', effective: '', range: '', modify: '', precedence: '', timing: '', persistence: '', risk: '', category: n === 25 ? 'P' : 'U', page: '', effective_class: 'readable_unverified', effective_check: '' });
const payload = { ok: true, data: { schema_version: 1, audit: '#446', rows: Array.from({ length: 25 }, (_, index) => fixture(index + 1)) } };

test('inventory paginates, searches and classifies without editable controls', async () => {
  const view = setup(payload);
  const first = view.render();
  assert.equal(view.render(), first);
  await first;
  assert.deepEqual(view.requests, [['/api/config/inventory']]);
  assert.equal(view.descendants('input').length, 1);
  assert.equal(view.descendants('input')[0].type, 'search');
  assert.equal(view.descendants('details').length, 24);
  assert.equal(view.descendants('summary')[0].textContent, 'ITEM-1 · <Inventory 1>');
  const [previous, next] = view.descendants('button');
  assert.equal(previous.disabled, true);
  next.listeners.click();
  assert.equal(next.disabled, true);
  assert.equal(view.descendants('details').length, 1);
  assert.match(view.descendants('summary')[0].textContent, /ITEM-25/);
  const search = view.descendants('input')[0];
  search.value = 'ＫＥＹ１';
  search.listeners.input();
  assert.equal(view.descendants('details').length, 11);
  const category = view.descendants('select')[1];
  category.value = 'P';
  category.listeners.change();
  assert.equal(view.descendants('details').length, 0);
  search.value = '';
  search.listeners.input();
  assert.equal(view.descendants('details').length, 1);
  assert.equal(view.descendants('table').length, 1);
  assert.equal(view.descendants('th').length, 4);
  assert.ok(view.descendants('td').some(element => element.textContent === '程序安全约束'));
});

test('request failure remains visible and retry loads the inventory', async () => {
  const view = setup([{ ok: false }, payload]);
  await view.render();
  assert.ok(view.descendants('p').some(element => element.textContent.includes('无法读取')));
  view.descendants('button')[0].listeners.click();
  await view.render();
  assert.equal(view.requests.length, 2);
  assert.equal(view.descendants('details').length, 24);
});
