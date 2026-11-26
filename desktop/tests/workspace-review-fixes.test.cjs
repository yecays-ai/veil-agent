'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const brain = readFileSync(path.join(__dirname, '../workspace-brain.js'), 'utf8');
const preload = readFileSync(path.join(__dirname, '../desktop-preload.cjs'), 'utf8');
const chat = readFileSync(path.join(__dirname, '../chat.js'), 'utf8');
const mainSrc = readFileSync(path.join(__dirname, '../main.cjs'), 'utf8');
const budget = readFileSync(path.join(__dirname, '../workspace-budget.js'), 'utf8');

test('memory library sends panel_edit changes, keeps drafts and archives through a write route', () => {
  assert.match(brain, /changes: \{ content: submittedDraft \}/);
  assert.match(brain, /expected_sha256: item\.sha256/);
  assert.match(brain, /body_exact/);
  assert.match(brain, /drafts\.get\(item\.memory_id\)/);
  assert.match(brain, /\/api\/memory\/archive'/);
  assert.match(brain, /seq !== state\.listSeq/);
  assert.match(brain, /seq !== state\.byIdSeq/);
  assert.match(chat, /expected_sha256:item\.sha256/);
  assert.match(brain, /seq !== detailSeq/);
});

test('recall and health pages never silently drop or mislabel data', () => {
  assert.match(brain, /Array\.isArray\(costs\)/);
  assert.match(brain, /payload\.text/);
  assert.doesNotMatch(brain, /slice\(0, 6000\)/);
  assert.match(brain, /healthSettings = settings/);
  assert.match(brain, /report: res\.data\.report, settings: healthSettings/);
  assert.match(brain, /dataset\.loading/);
});

test('detached pin bridge returns booleans with names chat.js actually calls', () => {
  for (const name of ['getPinned', 'setPinned', 'onPinnedState']) {
    assert.match(preload, new RegExp(name + ':'));
    assert.match(chat, new RegExp(name));
  }
  assert.doesNotMatch(preload, /getDetachedPinned|setDetachedPinned|onDetachedPinnedChanged/);
  assert.match(chat, /typeof res\s*===\s*'boolean'\s*\?\s*res\s*:\s*next/);
});

test('default custom CSS passes the same validator that reads it back', () => {
  const match = mainSrc.match(/const DEFAULT_CUSTOM_CSS = `([\s\S]*?)`;/);
  assert.ok(match, 'default CSS template found');
  assert.doesNotMatch(match[1], /@import\b|url\s*\(|expression\s*\(|-moz-binding|behavior\s*:/i);
});

test('a new avatar is written before the old format file is deleted', () => {
  const writeAt = mainSrc.indexOf('await fs.writeFile(staging, buf)');
  const deleteAt = mainSrc.indexOf("fs.rm(path.join(app.getPath('userData'), `avatar${oldExt}`)");
  assert.ok(writeAt > 0 && deleteAt > 0);
  assert.ok(writeAt < deleteAt);
  assert.match(mainSrc, /'\/api\/memory\/archive'/);
});

test('draft, request-sequence and form-state races stay fixed', () => {
  assert.match(brain, /submittedDraft/);
  assert.match(brain, /drafts\.get\(item\.memory_id\) === submittedDraft/);
  assert.match(brain, /JSON\.stringify\(\{ enabled: enabledChk\.checked, schedule: schedSel\.value \}\)/);
  assert.match(brain, /listSeq/);
  assert.match(brain, /byIdSeq/);
  assert.match(brain, /healthControls = \{ enabledChk, schedSel \}/);
  assert.match(brain, /eventsRenderSeq/);
  assert.match(brain, /recallRenderSeq/);
  assert.match(brain, /renderSeq !== eventsRenderSeq/);
  assert.match(brain, /renderSeq !== recallRenderSeq/);
  assert.match(budget, /dailyInput\.step = 'any'/);
  assert.match(budget, /dailyInput\.min = '0\.01'/);
});
