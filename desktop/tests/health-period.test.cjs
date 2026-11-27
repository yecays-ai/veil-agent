'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const sourcePath = path.join(__dirname, '..', 'workspace-brain.js');
const source = fs.readFileSync(sourcePath, 'utf8');

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
  }
  append(...children) { this.children.push(...children); }
  prepend(...children) { this.children.unshift(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
}

function texts(node) {
  const own = node && node.textContent ? [String(node.textContent)] : [];
  return own.concat(...(node.children || []).map(texts));
}

function renderHealth(report) {
  const document = {
    createElement: tag => new Element(tag),
    getElementById: () => null,
    querySelector: () => null,
  };
  const window = {};
  vm.runInNewContext(source, { window, document, navigator: {} });
  const host = new Element('section');
  const request = async (url) => {
    if (String(url).includes('/api/brain/health')) {
      return { ok: true, status: 200, json: async () => ({ report, settings: { enabled: true, schedule: 'daily_night', idle_only: true } }) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };
  return window.veilBrain.renderPage('brain-health', host, request).then(() => texts(host).join('\n'));
}

test('ticket 461 health page separates whole-database health from a date-range slice', async () => {
  const js = source;
  assert.match(js, /指定期间变化与异常（与全库当前分开）/);
  assert.match(js, /全库当前 \+ 指定期间/);
  assert.match(js, /period\.historical_state/);
  assert.match(js, /function describeHealthPeriod/);
  assert.match(js, /没有巡检历史快照/);
  assert.match(js, /index_anomalies/);
  assert.match(js, /数据范围：/);
  assert.match(js, /periodCard \? \[periodCard\]/);
});

test('incomplete period card is inserted and does not show unchecked zeros', async () => {
  const text = await renderHealth({
    summary: { status: 'incomplete', status_label: '本轮未完成', total_checks: 1, good_count: 0, warn_count: 1, error_count: 0 },
    checks: [],
    from_date: '2026-11-01',
    to_date: '2026-11-20',
    scope: { label: '全库当前 + 指定期间' },
    period: {
      requested: true,
      status: 'incomplete',
      growth: { memories_created: 0, events_created: 0, event_stages: 0, versions: 0, learned_context_changes: 0 },
      duplicates: { count: 0, evidences: [] },
      fragments: { count: 0, evidences: [] },
      dangling: { count: 0, evidences: [] },
      index_anomalies: { count: 0, evidences: [] },
      historical_state: { available: false, reason: 'no_snapshot_in_range' },
    },
  });
  assert.match(text, /指定期间变化与异常/);
  assert.match(text, /未完成/);
  assert.match(text, /未统计/);
  assert.doesNotMatch(text, /期间新增：记忆 0/);
  assert.doesNotMatch(text, /没有巡检历史快照/);
});

test('one complete snapshot is unknown, not a verified zero change', async () => {
  const text = await renderHealth({
    summary: { status: 'good', status_label: '状态正常', total_checks: 1, good_count: 1, warn_count: 0, error_count: 0 },
    checks: [],
    from_date: '2026-11-01',
    to_date: '2026-11-20',
    scope: { label: '全库当前 + 指定期间' },
    period: {
      requested: true,
      status: 'available',
      growth: { memories_created: 2, events_created: 1, event_stages: 0, versions: 0, learned_context_changes: 0 },
      duplicates: { count: 0, evidences: [] },
      fragments: { count: 0, evidences: [] },
      dangling: { count: 0, evidences: [] },
      index_anomalies: { count: 0, evidences: [] },
      historical_state: {
        available: true,
        delta_known: false,
        observations: 1,
        reason: 'single_complete_snapshot',
        delta: { active_memories: null, events_total: null },
        limit: '只有一条完整快照，没有第二个观测值，变化未知，不能写成 0。',
      },
    },
  });
  assert.match(text, /指定期间变化与异常/);
  assert.match(text, /期间新增：记忆 2/);
  assert.match(text, /变化未知/);
  assert.doesNotMatch(text, /活跃记忆变化 0/);
  assert.doesNotMatch(text, /没有巡检历史快照/);
});
