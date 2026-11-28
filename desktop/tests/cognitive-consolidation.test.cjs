'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const brainSource = fs.readFileSync(path.join(__dirname, '..', 'workspace-brain.js'), 'utf8');
const budgetSource = fs.readFileSync(path.join(__dirname, '..', 'workspace-budget.js'), 'utf8');
const mainSource = fs.readFileSync(path.join(__dirname, '..', 'main.cjs'), 'utf8');

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
  get firstChild() { return this.children[0]; }
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

function descendants(node, tag) {
  return [...(node.tag === tag ? [node] : []), ...(node.children || []).flatMap(c => descendants(c, tag))];
}

const sampleConsolidation = {
  available: true,
  policy_version: 's6-3.v1',
  settings_revision: 3,
  persistence_path: 'peter/cognitive-consolidation.json',
  settings: {
    enabled: false, interval_hours: 24, max_runs_per_day: 2,
    daily_budget_cny: 0.05, timeout_seconds: 25, auto_apply_importance: false,
  },
  effective: {
    code: 'disabled', status_text: '已关闭（默认灰度关闭）',
    runs_today: 0, max_runs_per_day: 2, processed_memories: 14, processed_autonomy: 3,
    last_run_at: '2026-11-27T23:00:00+08:00', last_status: 'completed', last_reason: '',
  },
  budget: { day: '2026-11-28', daily_cap_cny: '0.05', spent_cny: '0.012', pending_cny: '0', remaining_cny: '0.038' },
  totals: { cognition_written: 2, cognition_revised: 1, cognition_blocked: 1, importance_proposed: 1 },
  p7_interface: { schema_version: 'p7.experience.v1', counts: { total: 1, confirmed: 0, uncertain: 1 } },
  experience_candidates: [{
    id: 'exp-abc123', kind: 'communication_pattern', status: 'uncertain',
    trigger_condition: '深夜疲惫表达低落时', guidance: '先安静陪伴，不急着长篇说教',
    reason: '两次夜间交流印证', evidence_refs: ['memory:m1', 'memory:m2'],
  }],
  recent_runs: [{
    run_id: 'cons-001', started_at: '2026-11-27T23:00:00+08:00', finished_at: '2026-11-27T23:00:04+08:00',
    status: 'completed', reason: '', cost_cny: 0.012,
    inputs: { new_memory_ids: ['m1', 'm2'], anchor_memory_ids: ['m0'], autonomy_ids: ['autonomy:2026-11-27T14:00:00+08:00'] },
    results: {
      cognition_updates: [{
        kind: 'revision', section: 'relationship',
        replace_old_text: '周末十点前别发消息', text: '周末九点后就可以发消息',
        reason: '新经历表明作息提前', evidence_refs: ['memory:m1', 'chat:u2'],
        effective_status: 'effective_confirmed',
      }],
      importance_proposals: [{
        memory_id: 'm1', memory_title: '周二加班晚归', current_importance: 5,
        proposed_importance: 6, outcome: 'pending_review', reason: '关系意义增强',
      }],
      experience_candidates: [{ id: 'exp-abc123' }],
    },
  }],
};

test('Brain Home renders cognitive consolidation panel with default-off switch, frequency and cost controls', async () => {
  const document = {
    createElement: tag => new Element(tag),
    getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
    body: new Element('body'), addEventListener() {}, removeEventListener() {},
  };
  const window = { confirm: () => false };
  vm.runInNewContext(brainSource, { window, document, navigator: {} });
  const host = new Element('section');
  const request = async (url) => {
    const target = String(url);
    if (target.includes('/api/brain/consolidation')) return { ok: true, status: 200, json: async () => sampleConsolidation };
    if (target.includes('/api/brain/memory-weights')) return { ok: true, status: 200, json: async () => ({ available: false }) };
    if (target.includes('/api/brain/overview')) return {
      ok: true, status: 200, json: async () => ({
        counts: { active: 2, pinned: 1, archived: 0, events_total: 1, events_pending_story: 0, events_pending_review: 0, week_added: 1, pending_vector: 0, tombstones: 0, week_updated: 1 },
        health_summary: {}, layers: [], recall_association: null,
      }),
    };
    return { ok: true, status: 200, json: async () => ({ summary: {}, days: [] }) };
  };
  await window.veilBrain.renderPage('brain-home', host, request);
  const out = texts(host).join('\n');
  assert.match(out, /后台认知巩固与经验提炼（灰度）/);
  assert.match(out, /策略 s6-3\.v1/);
  assert.match(out, /已关闭（默认灰度关闭）/);
  assert.match(out, /执行间隔（小时）/);
  assert.match(out, /每日费用上限（¥）/);
  assert.match(out, /立即运行一次巩固/);
  // Run button must be disabled when consolidation is off.
  const runButton = descendants(host, 'button').find(b => b.textContent === '立即运行一次巩固');
  assert.equal(runButton.disabled, true);
});

test('Brain Home sends only dirty fields + expected_revision when saving consolidation settings', async () => {
  const document = {
    createElement: tag => new Element(tag),
    getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
    body: new Element('body'), addEventListener() {}, removeEventListener() {},
  };
  const window = { confirm: () => false };
  vm.runInNewContext(brainSource, { window, document, navigator: {} });
  const host = new Element('section');
  const posts = [];
  const request = async (url, options) => {
    const target = String(url);
    if (options?.method === 'POST') {
      posts.push([target, JSON.parse(options.body)]);
      return { ok: true, status: 200, json: async () => ({ ok: true, persistence_path: 'peter/cognitive-consolidation.json' }) };
    }
    if (target.includes('/api/brain/consolidation')) return { ok: true, status: 200, json: async () => sampleConsolidation };
    if (target.includes('/api/brain/memory-weights')) return { ok: true, status: 200, json: async () => ({ available: false }) };
    if (target.includes('/api/brain/overview')) return {
      ok: true, status: 200, json: async () => ({
        counts: { active: 2, pinned: 1, archived: 0, events_total: 1, events_pending_story: 0, events_pending_review: 0, week_added: 1, pending_vector: 0, tombstones: 0, week_updated: 1 },
        health_summary: {}, layers: [], recall_association: null,
      }),
    };
    return { ok: true, status: 200, json: async () => ({ summary: {}, days: [] }) };
  };
  await window.veilBrain.renderPage('brain-home', host, request);
  // Modify ONLY the timeout field (last numeric input in consolidation panel).
  const numInputs = descendants(host, 'input').filter(i => i.type === 'number');
  const timeoutInput = numInputs[numInputs.length - 1];
  timeoutInput.value = '40';
  timeoutInput.listeners.change();
  const saveBtn = descendants(host, 'button').find(b => b.textContent === '保存巩固设置');
  await saveBtn.listeners.click();
  assert.equal(posts.length, 1);
  assert.deepEqual(posts[0][1], { values: { timeout_seconds: 40 }, expected_revision: 3 });
});

test('Brain Cognition renders all returned P7 candidates beyond 12 with uncertain first', async () => {
  const document = {
    createElement: tag => new Element(tag),
    getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
    body: new Element('body'), addEventListener() {}, removeEventListener() {},
  };
  const window = { confirm: () => false };
  vm.runInNewContext(brainSource, { window, document, navigator: {}, Blob: global.Blob });
  const host = new Element('section');
  const manyP7 = Array.from({ length: 15 }, (_, idx) => ({
    id: `exp-${idx}`, kind: 'tool_lesson', status: idx === 14 ? 'uncertain' : 'confirmed',
    trigger_condition: `情境 ${idx}`, guidance: `经验条目编号_${idx}`,
    reason: '测试不截断', evidence_refs: ['memory:m1'],
  }));
  const request = async (url) => {
    const target = String(url);
    if (target.includes('/api/brain/consolidation')) {
      return { ok: true, status: 200, json: async () => ({ ...sampleConsolidation, experience_candidates: manyP7 }) };
    }
    if (target.includes('/api/peter-page/versions')) return { ok: true, status: 200, json: async () => ({ items: [] }) };
    return {
      ok: true, status: 200, json: async () => ({
        learned: { max_bytes: 6200, sections: { facts: { text: '' }, growth: { text: '' } }, relationship: { items: [] }, ongoing: { items: [] } },
      }),
    };
  };
  await window.veilBrain.renderPage('brain-cognition', host, request);
  const out = texts(host).join('\n');
  assert.match(out, /经验条目编号_0/);
  assert.match(out, /经验条目编号_14/);
  assert.ok(out.indexOf('经验条目编号_14') < out.indexOf('经验条目编号_0'));
});

test('Brain Cognition displays consolidation results, evidence sources, cognitive changes and P7 candidates', async () => {
  const document = {
    createElement: tag => new Element(tag),
    getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
    body: new Element('body'), addEventListener() {}, removeEventListener() {},
  };
  const window = { confirm: () => false };
  vm.runInNewContext(brainSource, { window, document, navigator: {}, Blob: global.Blob });
  const host = new Element('section');
  const request = async (url) => {
    const target = String(url);
    if (target.includes('/api/brain/consolidation')) return { ok: true, status: 200, json: async () => sampleConsolidation };
    if (target.includes('/api/peter-page/versions')) return { ok: true, status: 200, json: async () => ({ items: [] }) };
    return {
      ok: true, status: 200, json: async () => ({
        learned: {
          max_bytes: 6200,
          sections: { facts: { text: '' }, growth: { text: '' } },
          relationship: { items: [] },
          ongoing: { items: [] },
        },
      }),
    };
  };
  await window.veilBrain.renderPage('brain-cognition', host, request);
  const out = texts(host).join('\n');
  assert.match(out, /后台认知巩固结果与演化记录/);
  assert.match(out, /修订认知 · relationship/);
  assert.match(out, /已生效（已确认）/);
  assert.match(out, /旧：周末十点前别发消息/);
  assert.match(out, /新：周末九点后就可以发消息/);
  assert.match(out, /memory:m1、chat:u2/);
  assert.match(out, /预留 P7 情境经验候选/);
  assert.match(out, /先安静陪伴，不急着长篇说教/);
});

test('Budget page renders cognitive consolidation cap/frequency form and persists to consolidation settings API', async () => {
  const document = { createElement: tag => new Element(tag), getElementById: () => null };
  const host = new Element('section');
  const posts = [];
  const payload = {
    schema_version: 1, day: '2026-11-28', month: '2026-11',
    daily: { spent_cny: '0.50', limit_cny: '10', base_limit_cny: '10', raise_cny: '0', remaining_cny: '9.50' },
    monthly: { spent_cny: '1.50', limit_cny: '300', base_limit_cny: '300', raise_cny: '0', remaining_cny: '298.50' },
    pending_cny: '0', ledger_unverified: false,
    categories: [{ id: 'cognitive_consolidation', spent_cny: '0.012', pending_cny: '0', limit_cny: null }],
    model_summary: 'fake',
    cognitive_consolidation: {
      enabled: false, daily_cap_cny: '0.05', spent_cny: '0.012', pending_cny: '0', remaining_cny: '0.038',
      interval_hours: 24, max_runs_per_day: 2, runs_today: 1, status_text: '已关闭（默认灰度关闭）',
      cognition_written: 2, cognition_revised: 1, importance_proposed: 1, settings_revision: 5,
    },
  };
  const request = async (url, options) => {
    if (options?.method === 'POST') {
      posts.push([url, JSON.parse(options.body)]);
      return { ok: true, json: async () => ({ ok: true }) };
    }
    return { ok: true, json: async () => payload };
  };
  const window = { renderWorkspaceBudget: null };
  vm.runInNewContext(budgetSource, { window, document });
  await window.renderWorkspaceBudget(host, request);

  const out = texts(host).join('\n');
  assert.match(out, /后台认知巩固 · 当前角色/);
  assert.match(out, /认知巩固/);
  assert.match(out, /今日已用 ¥0\.012/);

  const forms = descendants(host, 'form');
  const consForm = forms[2];
  const inputs = descendants(consForm, 'input');
  inputs[0].checked = true;
  inputs[1].value = '0.08';
  inputs[2].value = '12';
  inputs[3].value = '3';
  await consForm.listeners.submit({ preventDefault() {} });
  assert.equal(posts.length, 1);
  assert.equal(posts[0][0], '/api/brain/consolidation/settings');
  assert.deepEqual(posts[0][1], {
    values: { enabled: true, daily_budget_cny: 0.08, interval_hours: 12, max_runs_per_day: 3 },
    expected_revision: 5,
  });
});

test('Electron main.cjs allows all P6-S6-3 consolidation and S6-1 memory-weights API routes', () => {
  const start = mainSource.indexOf('const MEMORY_POSTS =');
  const end = mainSource.indexOf("ipcMain.handle('chat:request'", start);
  const context = vm.createContext({ URL });
  const allowed = vm.runInContext(mainSource.slice(start, end) + '; allowedChatRequest', context);
  assert.equal(allowed('/api/brain/consolidation', 'GET'), true);
  assert.equal(allowed('/api/brain/consolidation?limit=12', 'GET'), true);
  assert.equal(allowed('/api/brain/consolidation?evil=1', 'GET'), false);
  assert.equal(allowed('/api/brain/consolidation/settings', 'POST'), true);
  assert.equal(allowed('/api/brain/consolidation/run', 'POST'), true);
  assert.equal(allowed('/api/brain/consolidation/experience', 'POST'), true);
  assert.equal(allowed('/api/brain/memory-weights', 'GET'), true);
  assert.equal(allowed('/api/brain/memory-weights', 'POST'), true);
});
