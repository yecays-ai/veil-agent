'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const script = readFileSync(path.join(__dirname, '../workspace-budget.js'), 'utf8');

class Element {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.style = {};
    this.dataset = {};
    this.listeners = {};
    this.value = '';
    this.textContent = '';
    this.className = '';
    this.disabled = false;
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
}

function setup(payload) {
  const document = { createElement: tag => new Element(tag), getElementById: () => null };
  const host = new Element('section');
  const requests = [];
  const bodies = [];
  const request = async (url, options) => {
    requests.push([url, options]);
    if (options?.method === 'POST') {
      bodies.push(JSON.parse(options.body));
      return { ok: true, json: async () => ({ ok: true }) };
    }
    return { ok: true, json: async () => payload };
  };
  const window = { renderWorkspaceBudget: null };
  vm.runInNewContext(script, { window, document });
  const descendants = (node, tag) => [...(node.tag === tag ? [node] : []), ...node.children.flatMap(child => descendants(child, tag))];
  return { host, requests, bodies, window, render: () => window.renderWorkspaceBudget(host, request), descendants: tag => descendants(host, tag) };
}

function budgetPayload(dailyBase, monthlyBase, dailyLimit, monthlyLimit) {
  return {
    schema_version: 1, day: '2026-11-26', month: '2026-11',
    daily: { spent_cny: '1.25', limit_cny: dailyLimit, base_limit_cny: dailyBase, raise_cny: '1', remaining_cny: '9.25' },
    monthly: { spent_cny: '1.25', limit_cny: monthlyLimit, base_limit_cny: monthlyBase, raise_cny: '1', remaining_cny: '24.25' },
    pending_cny: '0', ledger_unverified: false, categories: [], model_summary: 'fixture',
  };
}

test('budget form edits the base limit, not the effective limit with today\'s raise', async () => {
  const view = setup(budgetPayload('10', '25', '11', '26'));
  await view.render();
  const inputs = view.descendants('input');
  assert.deepEqual(inputs.map(input => input.value), ['10', '25']);
  assert.deepEqual(inputs.map(input => [input.min, input.step]), [['0.01', 'any'], ['0.01', 'any']]);
  const kpiText = view.descendants('div').map(node => node.textContent).join(' ');
  assert.match(kpiText, /上限 ¥11/);
});

test('saving limits posts only limits and keeps a working form', async () => {
  const view = setup(budgetPayload('10', '25', '11', '26'));
  await view.render();
  const inputs = view.descendants('input');
  inputs[0].value = '1.5';
  inputs[1].value = '26.5';
  const form = view.descendants('form')[0];
  await form.listeners.submit({ preventDefault() {} });
  assert.deepEqual(view.bodies[0], { limits: { daily_cny: '1.5', monthly_cny: '26.5' } });
  assert.match(view.descendants('small').map(node => node.textContent).join(' '), /已保存/);
  assert.equal(view.descendants('button')[1].disabled, false);
});
