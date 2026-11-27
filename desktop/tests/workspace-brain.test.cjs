'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');

const brainSrc = readFileSync(path.join(__dirname, '../workspace-brain.js'), 'utf8');
const shellSrc = readFileSync(path.join(__dirname, '../workspace-shell.js'), 'utf8');
const html = readFileSync(path.join(__dirname, '../chat.html'), 'utf8');
const mainSrc = readFileSync(path.join(__dirname, '../main.cjs'), 'utf8');

test('workspace-brain and workspace-shell avoid unsafe innerHTML and cover all 6 Brain sub-workspaces', () => {
  assert.equal(/\binnerHTML\b/.test(brainSrc), false, 'workspace-brain must construct DOM nodes safely');
  for (const page of ['brain-home', 'workspace-events', 'brain-cognition', 'brain-recall', 'brain-health']) {
    assert.match(brainSrc, new RegExp(page));
    assert.match(shellSrc, new RegExp(page));
  }
  assert.match(brainSrc, /\/api\/brain\/overview/);
  assert.match(brainSrc, /\/api\/memory\/library/);
  assert.match(brainSrc, /\/api\/brain\/health/);
  assert.match(brainSrc, /expected_sha256/);
});

test('4 visual themes, detached pin controls, and 3-tier custom CSS hooks are wired', () => {
  for (const themeId of ['rose', 'gold', 'blue', 'night']) {
    assert.match(html, new RegExp(`data-visual-theme-pick="${themeId}"`));
  }
  for (const id of ['detached-pin', 'detached-css', 'detached-avatar', 'open-custom-css-file', 'reload-custom-css']) {
    assert.match(html, new RegExp(`id="${id}"`));
  }
  for (const ipc of ['chat:get-avatar', 'chat:save-avatar', 'chat:get-custom-css', 'chat:save-custom-css', 'view:set-pinned']) {
    assert.match(mainSrc, new RegExp(ipc));
  }
});

test('P6-S6-2 autonomous leisure association status and diagnostics are wired in Brain UI', () => {
  assert.match(brainSrc, /leisure_status_text/);
  assert.match(brainSrc, /leisure_diagnostics/);
  assert.match(brainSrc, /自主闲暇诊断/);
  assert.match(brainSrc, /自主门控/);
});
