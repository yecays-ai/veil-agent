'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

const read = file => fs.readFile(path.join(__dirname, '..', file), 'utf8');

test('a refused tool call shows 被拒绝 with its real reason, never 权限拒绝 (ticket 415)', async () => {
  const js = await read('chat.js');
  // 拒绝的原因各不相同：状态只报「被拒绝」，真实原因在展开的执行结果里。
  assert.doesNotMatch(js, /权限拒绝/);
  assert.match(js, /if\(state==='被拒绝'\)return \{text:'被拒绝',className:'failure'\}/);
  assert.match(js, /function activityStatus\(state,detail\)/);
  assert.match(js, /activityStatus\(item\.state,item\.detail\)/);
  // 程序内部故障与普通失败分开显示，活动页不再把它说成他的参数错。
  assert.match(js, /工具内部故障/);
});
