'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'workspace-brain.js'), 'utf8');

class Element {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.style = {};
    this.dataset = {};
    this.listeners = {};
    this.className = '';
    this.textContent = '';
    this.value = '';
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.type = '';
    this.classList = { add() {}, remove() {}, toggle() {} };
  }
  append(...children) { this.children.push(...children); }
  prepend(...children) { this.children.unshift(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  remove() {}
  querySelector() { return null; }
  querySelectorAll() { return []; }
  scrollIntoView() {}
}

function texts(node) {
  const own = node && node.textContent ? [String(node.textContent)] : [];
  return own.concat(...(node.children || []).map(texts));
}

function renderHome(weights) {
  const document = {
    createElement: tag => new Element(tag),
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    body: new Element('body'),
    addEventListener() {},
    removeEventListener() {},
  };
  const window = { confirm: () => false };
  vm.runInNewContext(source, { window, document, navigator: {} });
  const host = new Element('section');
  const overview = {
    counts: { active: 2, pinned: 1, archived: 0, events_total: 1, events_pending_story: 0,
      events_pending_review: 0, week_added: 1, pending_vector: 0, tombstones: 0,
      week_updated: 1 },
    health_summary: {},
    layers: [],
    recall_association: null,
  };
  const growth = { summary: {}, days: [] };
  const request = async (url) => {
    const target = String(url);
    let payload = {};
    if (target.includes('/api/brain/memory-weights')) payload = weights;
    else if (target.includes('/api/brain/overview')) payload = overview;
    else if (target.includes('/api/brain/growth')) payload = growth;
    return { ok: true, status: 200, json: async () => payload };
  };
  return window.veilBrain.renderPage('brain-home', host, request)
    .then(() => texts(host).join('\n'));
}

const weights = {
  available: true,
  policy_version: 's6-1.v1',
  persistence_path: 'peter/memory-weights.json',
  settings: {
    activation_enabled: true, decay_enabled: true, decay_half_life_days: 30, activity_cap: 30,
    ranking_enabled: false, ranking_weight: 0.05, reeval_enabled: true,
    reeval_auto_apply: true, reeval_cooldown_hours: 24, reeval_rejected_cooldown_hours: 168,
    reeval_min_confidence: 0.45, reeval_max_auto_importance: 7, reeval_max_step: 1,
    delivery_event_keep: 20000,
  },
  defaults: {},
  effective: {},
  guards: {},
  counters: { tracked_memories: 12, proposals_pending: 2, proposals_applied: 1,
    proposals_rejected: 0 },
};

test('S6-1 memory weights UI exposes activation, protective decay and review controls', async () => {
  assert.match(source, /\/api\/brain\/memory-weights/);
  assert.match(source, /\/api\/brain\/memory-proposals/);
  assert.match(source, /\/api\/brain\/memory-proposal/);
  assert.match(source, /\/api\/brain\/memory-reevaluate/);
  assert.match(source, /importance>=7 与置顶永不衰减，衰减不删除、不隐藏/);
  const text = await renderHome(weights);
  assert.match(text, /记忆权重与保护性衰减/);
  assert.match(text, /送达后激活/);
  assert.match(text, /保护性衰减/);
  assert.match(text, /活跃度参与排序（实验）/);
  assert.match(text, /重评建议（2）/);
  assert.match(text, /策略 s6-1\.v1/);
});

test('S6-1 library detail shows activation, pending review and history entry points', () => {
  assert.match(source, /重要度: \$\{item\.importance/);
  assert.match(source, /活跃度: \$\{act\.display/);
  assert.match(source, /送达 \$\{act\.deliveries/);
  assert.match(source, /待你确认的重评：重要度/);
  assert.match(source, /function renderMemoryReevalHistory/);
  assert.match(source, /只有真正送达你的回复才更新 last_active 与强化次数/);
});
