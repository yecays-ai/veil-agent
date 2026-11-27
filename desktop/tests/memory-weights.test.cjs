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

function renderLibraryDetail() {
  const document = {
    createElement: tag => new Element(tag),
    getElementById: id => (id === 'memory-view' ? view : null),
    querySelector: () => null,
    querySelectorAll: () => [],
    body: new Element('body'),
    addEventListener() {},
    removeEventListener() {},
  };
  const view = new Element('section');
  const window = { confirm: () => false };
  vm.runInNewContext(source, { window, document, navigator: {}, URLSearchParams });
  const item = {
    memory_id: 'm1', title: '共同经历', created: '2026-11-01T10:00:00+0800',
    updated_at: '2026-11-02T10:00:00+0800', kind: 'experience', bucket: 'dynamic',
    importance: 8, pinned: false, archived: false, locked: false, vector_state: 'ready',
    merged_into: null, delete_pending: false, lan_edited: null, peter_after_lan: null,
    why: '为什么记住', body: '正文', body_exact: '正文', sha256: 'abcdef1234567890',
    recalled: 2, version_count: 1, source_channel: 'chat', event: null, links: [],
    continued_by: [], sources: [],
    activation: { last_active: '2026-11-02T10:00:00+0800', last_delivery_at: '',
      deliveries: 3, distinct_days: 2, distinct_sessions: 2, score: 3.5, display: 64,
      stored_score: 3.5, protected: true, decayed: false, last_request_id: 'r1',
      reeval_pending: true },
    reeval: { pending: { proposal_id: 'p1', current_importance: 8, proposed_importance: 9,
      confidence: 0.8, policy_version: 's6-1.v1', reasons: [{ code: 'event', label: '关联共同经历', detail: '旧事' }] },
      latest: null, applied: 0, rejected: 0, reverted: 0 },
  };
  const request = async (url) => {
    if (String(url).includes('/api/memory/library')) {
      return { ok: true, status: 200, json: async () => ({ items: [item], total: 1, filtered: 1 }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };
  window.veilBrain.mountMemoryLibraryAddon(request);
  return new Promise(resolve => setTimeout(() => {
    const findCard = node => {
      if (String(node.className).includes('index-item')) return node;
      for (const child of node.children || []) {
        const found = findCard(child);
        if (found) return found;
      }
      return null;
    };
    const card = findCard(view);
    if (card && card.listeners.click) card.listeners.click();
    resolve(texts(view).join('\n'));
  }, 30));
}

test('S6-1 memory detail is actually rendered with activation, importance and review controls', async () => {
  const text = await renderLibraryDetail();
  assert.match(text, /重要度: 8 \/ 10/);
  assert.match(text, /最近活跃:/);
  assert.match(text, /活跃度: 64%/);
  assert.match(text, /送达 3 次/);
  assert.match(text, /保护: 重要或置顶，永不衰减/);
  assert.match(text, /待你确认的重评：重要度 8 → 9/);
  assert.match(text, /接受并写入版本/);
  assert.match(text, /查看重评历史/);
});

test('S6-1 library detail shows activation, pending review and history entry points', () => {
  assert.match(source, /重要度: \$\{item\.importance/);
  assert.match(source, /活跃度: \$\{act\.display/);
  assert.match(source, /送达 \$\{act\.deliveries/);
  assert.match(source, /待你确认的重评：重要度/);
  assert.match(source, /function renderMemoryReevalHistory/);
  assert.match(source, /只有真正送达你的回复才更新 last_active 与强化次数/);
});