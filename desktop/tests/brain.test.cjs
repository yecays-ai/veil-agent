'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { DatabaseSync } = require('node:sqlite');
const { start } = require('../brain/server.cjs');

function makeBrain() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'veil-brain-'));
  const file = path.join(dir, 'brain.db');
  const db = new DatabaseSync(file);
  db.exec(`
    CREATE TABLE memories(
      id TEXT PRIMARY KEY, bucket TEXT NOT NULL, file_rel TEXT NOT NULL,
      title TEXT, name TEXT, body TEXT NOT NULL, body_sha256 TEXT NOT NULL,
      raw_markdown TEXT NOT NULL, raw_sha256 TEXT NOT NULL,
      frontmatter_raw TEXT NOT NULL, frontmatter_json TEXT NOT NULL,
      created TEXT, last_active TEXT, type TEXT, importance INTEGER, valence REAL, arousal REAL,
      domain_json TEXT, tags_json TEXT, why_remembered TEXT, meaning TEXT,
      source_tool TEXT, triggered_by TEXT, is_hidden INTEGER NOT NULL DEFAULT 0,
      is_tombstone INTEGER NOT NULL DEFAULT 0, is_deleted INTEGER NOT NULL DEFAULT 0,
      erasure_mode TEXT, deleted_at TEXT, pinned INTEGER, protected INTEGER, anchor INTEGER,
      dont_surface INTEGER, digested INTEGER, resolved INTEGER, first_of_kind INTEGER,
      extra_json TEXT NOT NULL DEFAULT '{}');
    CREATE TABLE versions(
      seq INTEGER PRIMARY KEY, memory_id TEXT NOT NULL, time TEXT, operation_reason TEXT,
      why_remembered TEXT, fields_json TEXT, old_content TEXT, old_sha256 TEXT, source_index INTEGER);
    CREATE TABLE sidecar_state(
      memory_id TEXT PRIMARY KEY, locked INTEGER NOT NULL DEFAULT 0, lan_edited TEXT,
      peter_after_lan TEXT, links_json TEXT NOT NULL, sources_json TEXT NOT NULL, merged_into TEXT);
    CREATE TABLE events(event_id TEXT PRIMARY KEY, memory_id TEXT, source_count INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE event_sources(event_id TEXT NOT NULL, ord INTEGER NOT NULL, memory_id TEXT,
      part TEXT, PRIMARY KEY(event_id, ord));
    CREATE TABLE sources(memory_id TEXT NOT NULL, ord INTEGER NOT NULL, kind TEXT, ref TEXT,
      status TEXT, ranges_json TEXT, PRIMARY KEY(memory_id, ord));
    CREATE TABLE relations(memory_id TEXT NOT NULL, ord INTEGER NOT NULL, target_id TEXT, type TEXT,
      score REAL, status TEXT, auto INTEGER, label TEXT, PRIMARY KEY(memory_id, ord));
    CREATE TABLE vectors(memory_id TEXT PRIMARY KEY, dim INTEGER NOT NULL, vector BLOB NOT NULL,
      vector_sha256 TEXT NOT NULL, updated_at TEXT, content_hash TEXT, meaning_dim INTEGER,
      meaning_vector BLOB, meaning_sha256 TEXT);
  `);
  const insert = db.prepare(`INSERT INTO memories
    (id, bucket, file_rel, title, name, body, body_sha256, raw_markdown, raw_sha256,
     frontmatter_raw, frontmatter_json, created, last_active, type, importance, domain_json,
     source_tool, is_hidden, is_tombstone, is_deleted, pinned, protected, dont_surface,
     digested, resolved, first_of_kind)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 0, 0, 0, 0, 0, 0)`);
  insert.run('a1', 'dynamic', 'dynamic/a1.md', '第一段', '第一段', '正文一', 'h1', '# 一', 'r1',
    '', '{}', '2026-11-20T10:00:00+08:00', '2026-11-20T10:00:00+08:00', 'dynamic', 5,
    '["工作"]', 'hold', 0, 0);
  insert.run('b2', 'archive', 'archive/b2.md', '第二段', '第二段', '正文二', 'h2', '# 二', 'r2',
    '', '{}', '2026-11-19T09:00:00+08:00', '2026-11-19T09:00:00+08:00', 'archive', 3,
    '["生活"]', 'save', 1, 0);
  db.prepare('INSERT INTO versions VALUES (1, ?, ?, ?, NULL, ?, ?, ?, NULL)')
    .run('a1', '2026-11-20T09:00:00+08:00', '首次写入', '一', '---\nid: a1\n---\n旧正文', 'h0');
  db.prepare('INSERT INTO sidecar_state VALUES (?, 0, NULL, NULL, ?, ?, NULL)')
    .run('a1', '[]', '[]');
  db.prepare('INSERT INTO events VALUES (?, ?, ?)').run('V1', 'a1', 2);
  db.prepare('INSERT INTO event_sources VALUES (?, ?, ?, NULL)').run('V1', 0, 'a1');
  db.prepare('INSERT INTO event_sources VALUES (?, ?, ?, NULL)').run('V1', 1, 'gone');
  db.prepare('INSERT INTO sources VALUES (?, 0, ?, ?, ?, NULL)').run('a1', 'conversation', 'chat:1', 'active');
  db.prepare('INSERT INTO vectors VALUES (?, 1024, ?, ?, NULL, NULL, NULL, NULL, NULL)')
    .run('a1', Buffer.alloc(4), 'v1');
  db.close();
  return { dir, file };
}

test('brain read-only adapter serves browse, detail, health and rejects writes', async () => {
  const { dir, file } = makeBrain();
  const runtime = await start({ dbPath: file, port: 0, token: 'test-token' });
  const base = runtime.url;
  const headers = { 'X-Brain-Token': 'test-token', 'Content-Type': 'application/json' };
  try {
    const who = await (await fetch(`${base}/api/whoami`, { headers })).json();
    assert.equal(who.user_id, 'lan');

    const stats = await (await fetch(`${base}/api/stats`, { headers })).json();
    assert.equal(stats.totals.memories, 2);
    assert.equal(stats.by_type.dynamic, 1);

    const browse = await (await fetch(`${base}/api/memories/browse?limit=10&sort=newest`, { headers })).json();
    assert.equal(browse.results.length, 2);
    assert.equal(browse.results[0].id, 'a1');
    assert.match(browse.results[0].memory, /第一段/);

    const history = await (await fetch(`${base}/api/memories/a1/history`, { headers })).json();
    assert.equal(history.revisions.length, 2);
    assert.match(history.revisions[0].memory, /旧正文/);
    assert.equal(history.revisions.at(-1).memory, '正文一');

    const health = await (await fetch(`${base}/api/fsck/local`, { headers })).json();
    assert.equal(health.status, 'completed');
    assert.ok(health.issues.some((issue) => issue.reasoning.includes('gone')));

    const write = await fetch(`${base}/api/memories/a1`, {
      method: 'POST', headers, body: '{}',
    });
    assert.equal(write.status, 403);
  } finally {
    await new Promise((resolve) => runtime.server.close(resolve));
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
