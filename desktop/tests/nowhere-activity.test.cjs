'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { parse } = require('../nowhere-activity.js');

const result = data => JSON.stringify({ text: 'fixture result', data });

test('quotes are presented as query results with their returned order and fields', () => {
  const item = { tool: 'mcp__nowhere__quotes', state: '成功', request_id: 'request-1',
    detail: result({ quotes: [
      { text: 'Earlier fictional note', place: 'North Ridge', sim_time: '2026-10-30T10:00:00Z' },
      { text: 'Birthday fictional note', place: 'North Ridge', pos: [1, 2] },
    ] }) };
  const view = parse(item, [item]);
  assert.equal(view.kind, 'quotes');
  assert.deepEqual(view.quotes.map(quote => quote.text),
    ['Earlier fictional note', 'Birthday fictional note']);
  assert.equal(view.quotes[0].place, 'North Ridge');
  assert.equal(view.quotes[1].sim_time, '');
  assert.equal(view.duplicateCount, 0);
});

test('all quotes already shown from say are omitted from the same-activity query', () => {
  const say = { tool: 'mcp__nowhere__say', state: '成功', autonomy_run_id: 'wake-1',
    tool_input: 'One fictional note', detail: result({ saved: true }) };
  const quotes = { tool: 'mcp_nowhere_quotes', state: '成功', autonomy_run_id: 'wake-1',
    detail: result({ quotes: [
      { text: 'Earlier fictional note' },
      { text: 'One fictional note' },
      { text: 'One fictional note' },
    ] }) };
  const view = parse(quotes, [say, quotes]);
  assert.equal(view.duplicateCount, 2);
  assert.deepEqual(view.quotes.map(quote => quote.text), ['Earlier fictional note']);
  assert.equal(parse(say, [say]).text, 'One fictional note');
});

test('truncated say records do not hide quote text from the query result', () => {
  const say = { tool: 'mcp__nowhere__say', state: '成功', result_state: '已截断',
    autonomy_run_id: 'wake-truncated', tool_input: 'Hidden by truncated result' };
  const quotes = { tool: 'mcp__nowhere__quotes', state: '成功',
    autonomy_run_id: 'wake-truncated',
    detail: result({ quotes: [{ text: 'Hidden by truncated result' }] }) };
  assert.deepEqual(parse(quotes, [say, quotes]).quotes.map(quote => quote.text),
    ['Hidden by truncated result']);
});

test('aliased tools use their canonical name and malformed quote rows fall back', () => {
  const item = { tool: 'quote_alias', canonical_tool: 'mcp__nowhere__quotes', state: '成功',
    detail: result({ quotes: [{ text: 'A fictional quote' }, { place: 'Missing text' }] }) };
  assert.equal(parse(item), null);
});

test('buried items show only returned fields and malformed, failed, or truncated results fall back', () => {
  const item = { tool: 'mcp__nowhere__bury', state: '成功',
    detail: result({ buried: { name: 'A fictional ice shard', from: 'Peter', pos: [3, 4],
      buried_at: '2026-10-31T11:39:27Z', note: 'A fictional birthday note.' } }) };
  assert.deepEqual(parse(item), {
    kind: 'bury', name: 'A fictional ice shard', description: '', source: 'Peter',
    pos: [3, 4], buried_at: '2026-10-31T11:39:27Z', note: 'A fictional birthday note.'
  });
  assert.equal(parse({ ...item, detail: '{broken' }), null);
  assert.equal(parse({ ...item, state: '失败' }), null);
  assert.equal(parse({ ...item, result_state: '已截断' }), null);
  assert.equal(parse({ ...item, tool: 'read_text_file' }), null);
});
