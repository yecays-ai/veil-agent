'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const read = file => fs.readFile(path.join(__dirname, '..', file), 'utf8');

test('ticket 461 health page separates whole-database health from a date-range slice', async () => {
  const js = await read('workspace-brain.js');
  assert.match(js, /指定期间变化与异常（与全库当前分开）/);
  assert.match(js, /全库当前 \+ 指定期间/);
  assert.match(js, /period\.historical_state/);
  assert.match(js, /no_snapshot_in_range|没有巡检历史快照/);
  assert.match(js, /index_anomalies/);
  assert.match(js, /数据范围：/);
});
