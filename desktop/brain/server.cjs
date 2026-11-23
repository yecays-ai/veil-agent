'use strict';
/*
 * VEIL Brain · read-only prototype server.
 *
 * Serves the vendored mnemory management UI (Apache-2.0, see LICENSE-mnemory.txt)
 * and a small compatibility API on top of VEIL Brain's own brain.db.
 *
 * P4 prototype rules:
 *   - brain.db is opened read-only; this process never writes memory data;
 *   - no external service, no Qdrant, no LLM, no network calls;
 *   - every mutation endpoint answers 403 with a readable reason.
 *
 * Standalone:
 *   node desktop/brain/server.cjs --db <brain.db> [--port 8799] [--token <secret>]
 */
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { DatabaseSync } = require('node:sqlite');

const UI_ROOT = path.join(__dirname, 'ui');
const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.woff2': 'font/woff2',
};

function parseArgs(argv) {
  const out = { db: '', port: 0, token: '' };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    if (key === '--db') out.db = argv[++i];
    else if (key === '--port') out.port = Number(argv[++i]) || 0;
    else if (key === '--token') out.token = argv[++i];
  }
  return out;
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > 2 * 1024 * 1024) { reject(new Error('request body too large')); req.destroy(); return; }
      chunks.push(chunk);
    });
    req.on('end', () => {
      if (!chunks.length) { resolve({}); return; }
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
      catch { reject(new Error('invalid JSON body')); }
    });
    req.on('error', reject);
  });
}

function decodeCursor(cursor) {
  if (!cursor) return 0;
  const value = Number(Buffer.from(String(cursor), 'base64url').toString('utf8'));
  return Number.isInteger(value) && value >= 0 ? value : 0;
}

function encodeCursor(offset) {
  return Buffer.from(String(offset), 'utf8').toString('base64url');
}

function iso(value) {
  if (!value) return null;
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? null : time.toISOString();
}

/** brain.db versions store the full Markdown file; the detail tab compares bodies. */
function bodyOfVersion(text) {
  const value = String(text || '');
  if (!value.startsWith('---')) return value;
  const match = value.match(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/);
  return match ? value.slice(match[0].length) : value;
}

function parseList(value) {
  if (Array.isArray(value)) return value.filter((item) => typeof item === 'string');
  if (typeof value !== 'string' || !value.trim()) return [];
  return value.split(',').map((item) => item.trim()).filter(Boolean);
}

/**
 * Loads a small, immutable in-memory projection of brain.db. 283 memories is
 * far below the size where a second index costs anything, and it keeps the HTTP
 * layer free of per-request joins.
 */
function loadBrain(dbPath) {
  const db = new DatabaseSync(dbPath, { readOnly: true });
  db.exec('PRAGMA query_only = ON');
  db.exec('PRAGMA busy_timeout = 3000');

  const memoryRows = db.prepare(`
    SELECT id, bucket, title, name, body, created, last_active, type, importance,
           domain_json, source_tool, is_hidden, is_tombstone, is_deleted,
           pinned, protected, dont_surface, digested, resolved
    FROM memories
  `).all();
  const versionRows = db.prepare(`
    SELECT memory_id, seq, time, operation_reason, old_content
    FROM versions ORDER BY memory_id, seq
  `).all();
  const versionsByMemory = new Map();
  for (const row of versionRows) {
    if (!versionsByMemory.has(row.memory_id)) versionsByMemory.set(row.memory_id, []);
    versionsByMemory.get(row.memory_id).push(row);
  }
  const sidecar = new Map();
  for (const row of db.prepare('SELECT memory_id, locked, merged_into, links_json, sources_json FROM sidecar_state').all()) {
    sidecar.set(row.memory_id, row);
  }
  const eventByMemory = new Map();
  for (const row of db.prepare('SELECT event_id, memory_id FROM events').all()) {
    if (row.memory_id) eventByMemory.set(row.memory_id, row.event_id);
  }
  const sourcesByMemory = new Map();
  for (const row of db.prepare('SELECT memory_id, kind, ref, status FROM sources ORDER BY memory_id, ord').all()) {
    if (!sourcesByMemory.has(row.memory_id)) sourcesByMemory.set(row.memory_id, []);
    sourcesByMemory.get(row.memory_id).push(row);
  }
  const relationsByMemory = new Map();
  for (const row of db.prepare('SELECT memory_id, target_id, type, score, status, label FROM relations ORDER BY memory_id, ord').all()) {
    if (!relationsByMemory.has(row.memory_id)) relationsByMemory.set(row.memory_id, []);
    relationsByMemory.get(row.memory_id).push(row);
  }
  const vectorIds = new Set(db.prepare('SELECT memory_id FROM vectors').all().map((row) => row.memory_id));
  const danglingEventSources = db.prepare(`
    SELECT es.event_id, es.memory_id, e.memory_id AS owner_id
    FROM event_sources es
    LEFT JOIN memories m ON m.id = es.memory_id
    LEFT JOIN events e ON e.event_id = es.event_id
    WHERE es.memory_id IS NOT NULL AND (m.id IS NULL OR m.is_deleted = 1)
  `).all();

  const memories = memoryRows.map((row) => {
    const versions = versionsByMemory.get(row.id) || [];
    const note = sidecar.get(row.id);
    const domains = JSON.parse(row.domain_json || '[]');
    const status = row.is_deleted ? 'deleted'
      : row.is_tombstone ? 'tombstone'
        : row.is_hidden ? 'hidden'
          : note?.locked ? 'locked' : 'active';
    const statusLabel = { deleted: '已删除', tombstone: '墓碑', hidden: '隐藏', locked: '锁定', active: '正常' }[status];
    const content = (row.title ? `${row.title}\n\n` : '') + (row.body || '');
    return {
      id: row.id,
      bucket: row.bucket,
      title: row.title || row.name || row.id,
      body: row.body || '',
      content: content.trim(),
      created: row.created,
      lastActive: row.last_active,
      importance: row.importance,
      domains,
      sourceTool: row.source_tool,
      status,
      statusLabel,
      hidden: !!row.is_hidden,
      tombstone: !!row.is_tombstone,
      deleted: !!row.is_deleted,
      pinned: !!row.pinned,
      protected: !!row.protected,
      dontSurface: !!row.dont_surface,
      digested: !!row.digested,
      resolved: !!row.resolved,
      versions,
      revision: versions.length + 1,
      mergedInto: note?.merged_into || null,
      locked: !!note?.locked,
      eventId: eventByMemory.get(row.id) || null,
      sources: sourcesByMemory.get(row.id) || [],
      relations: relationsByMemory.get(row.id) || [],
    };
  });
  const byId = new Map(memories.map((memory) => [memory.id, memory]));
  return { db, memories, byId, vectorIds, danglingEventSources };
}

/** mnemory's MemoryItem shape, with VEIL fields carried through `metadata`. */
function toItem(memory) {
  return {
    id: memory.id,
    memory: memory.content,
    score: null,
    has_artifacts: false,
    metadata: {
      memory_type: memory.bucket,
      categories: memory.domains,
      importance: memory.importance,
      pinned: memory.pinned,
      role: 'user',
      agent_id: 'peter',
      created_at_utc: iso(memory.created),
      last_accessed_at: iso(memory.lastActive),
      memory_layer: 'consolidated',
      revision: memory.revision,
      revision_state: memory.statusLabel,
      validation_state: memory.protected ? '受保护' : '未校验',
      validation_count: 0,
      labels: {
        状态: memory.statusLabel,
        来源: memory.sourceTool || '未知',
        事件: memory.eventId || '—',
        版本: `v${memory.revision}`,
      },
    },
  };
}

function statsOf(brain) {
  const live = brain.memories.filter((memory) => !memory.deleted);
  const byType = {};
  const byCategory = {};
  let pinned = 0;
  for (const memory of live) {
    byType[memory.bucket] = (byType[memory.bucket] || 0) + 1;
    if (memory.pinned) pinned += 1;
    for (const domain of memory.domains) byCategory[domain] = (byCategory[domain] || 0) + 1;
  }
  const totals = {
    memories: live.length,
    raw: 0,
    consolidated: live.length,
    pinned,
    decayed: 0,
    with_artifacts: 0,
  };
  const slice = {
    total: live.length,
    raw: 0,
    consolidated: live.length,
    pinned,
    decayed: 0,
    with_artifacts: 0,
    by_type: byType,
    by_category: byCategory,
    by_layer: { consolidated: live.length },
    by_role: { user: live.length },
  };
  return {
    totals,
    by_type: byType,
    by_category: byCategory,
    by_layer: { consolidated: live.length },
    by_role: { user: live.length },
    users: ['lan'],
    agents: ['peter'],
    by_user: { lan: slice },
    operations: {},
    active_sessions: 0,
    version: 'P4 原型',
    brain: {
      source: 'brain.db',
      schema: 1,
      memories: brain.memories.length,
      versions: brain.memories.reduce((sum, memory) => sum + memory.versions.length, 0),
      events: brain.memories.filter((memory) => memory.eventId).length,
      tombstone: brain.memories.filter((memory) => memory.tombstone).length,
      locked: brain.memories.filter((memory) => memory.locked).length,
      hidden: brain.memories.filter((memory) => memory.hidden).length,
    },
  };
}

function historyOf(brain, id) {
  const memory = brain.byId.get(id);
  if (!memory) return null;
  const revisions = [];
  memory.versions.forEach((version, index) => {
    revisions.push({
      id: `${id}:v${index + 1}`,
      memory: bodyOfVersion(version.old_content),
      metadata: {
        revision: index + 1,
        created_at_utc: iso(version.time),
        state_reason: version.operation_reason || '旧版本',
        memory_type: memory.bucket,
        categories: memory.domains,
        importance: memory.importance,
        labels: { 来源: memory.sourceTool || '未知' },
      },
      predecessor_id: index > 0 ? `${id}:v${index}` : null,
      successor_id: `${id}:v${index + 2}`,
      has_artifacts: false,
    });
  });
  revisions.push({
    id: `${id}:v${memory.revision}`,
    memory: memory.body,
    metadata: {
      revision: memory.revision,
      created_at_utc: iso(memory.lastActive || memory.created),
      state_reason: '当前正文',
      memory_type: memory.bucket,
      categories: memory.domains,
      importance: memory.importance,
      labels: { 来源: memory.sourceTool || '未知', 状态: memory.statusLabel },
    },
    predecessor_id: memory.revision > 1 ? `${id}:v${memory.revision - 1}` : null,
    has_artifacts: false,
  });
  const operations = memory.versions.map((version, index) => ({
    operation_id: `${id}:op${index + 1}`,
    operation_kind: 'update',
    status: 'completed',
    actor_kind: 'peter',
    source_kind: null,
    reason: version.operation_reason || '版本更新',
    created_at_utc: iso(version.time),
    completed_at_utc: iso(version.time),
  }));
  return {
    lineage_id: id,
    current_revision_id: `${id}:v${memory.revision}`,
    revisions,
    operations,
    next_revision_cursor: null,
    next_operation_cursor: null,
  };
}

function linkItem(memory, state = 'active') {
  return {
    id: memory.id,
    memory: memory.title,
    revision: memory.revision,
    revision_state: state,
    memory_type: memory.bucket,
    memory_layer: 'consolidated',
    has_artifacts: false,
  };
}

function linksOf(brain, id) {
  const memory = brain.byId.get(id);
  if (!memory) return null;
  const derived = [];
  for (const relation of memory.relations) {
    const target = relation.target_id && brain.byId.get(relation.target_id);
    if (target) derived.push(linkItem(target));
  }
  const notes = memory.sources.map((source) => ({
    id: `${id}:source:${source.kind || 'ref'}`,
    memory: `来源：${source.kind || '未知'} · ${source.ref || ''}${source.status ? ` · ${source.status}` : ''}`,
    revision: 1,
    revision_state: 'source',
    memory_type: memory.bucket,
    memory_layer: 'consolidated',
    has_artifacts: false,
  }));
  return {
    revision_id: `${id}:v${memory.revision}`,
    lineage_id: id,
    supersedes: null,
    successor: memory.mergedInto && brain.byId.get(memory.mergedInto)
      ? linkItem(brain.byId.get(memory.mergedInto), 'merged_target') : null,
    derived_from: derived.concat(notes),
    derived_outputs: [],
    provenance_quality: 'exact',
  };
}

function affectedItem(memory) {
  return {
    id: memory.id,
    content: memory.content.slice(0, 500),
    metadata: {
      title: memory.title,
      memory_type: memory.bucket,
      status: memory.statusLabel,
      created_at_utc: iso(memory.created),
    },
  };
}

/**
 * Deterministic, local health checks. These are exactly the checks the
 * prototype can prove with brain.db alone; semantic duplicate/contradiction
 * checks belong to P1 and are deliberately absent.
 */
function healthOf(brain) {
  const issues = [];
  const push = (issueId, type, severity, reasoning, memories, confidence = 1) => {
    issues.push({
      issue_id: issueId,
      type,
      severity,
      confidence,
      reasoning,
      affected_memories: memories.map(affectedItem),
      actions: [],
      applied: false,
    });
  };

  const live = brain.memories.filter((memory) => !memory.deleted);

  const byTitle = new Map();
  for (const memory of live) {
    const key = memory.title.trim().toLocaleLowerCase();
    if (!key) continue;
    if (!byTitle.has(key)) byTitle.set(key, []);
    byTitle.get(key).push(memory);
  }
  for (const [title, group] of byTitle) {
    if (group.length > 1) {
      push(
        `dup-title-${group[0].id}`,
        'duplicate', 'low',
        `标题重复：${group.length} 条记忆都叫《${group[0].title}》。需要人工判断是同一段经历的多次写入，还是不同经历同名。`,
        group,
      );
    }
  }

  const byBody = new Map();
  for (const memory of live) {
    const key = memory.body.trim();
    if (!key) continue;
    if (!byBody.has(key)) byBody.set(key, []);
    byBody.get(key).push(memory);
  }
  for (const group of byBody.values()) {
    if (group.length > 1) {
      push(
        `dup-body-${group[0].id}`,
        'duplicate', 'medium',
        `正文完全相同的记忆有 ${group.length} 条，可能是重复导入或重复保存。`,
        group,
      );
    }
  }

  const danglingByEvent = new Map();
  for (const row of brain.danglingEventSources) {
    if (!danglingByEvent.has(row.event_id)) danglingByEvent.set(row.event_id, []);
    danglingByEvent.get(row.event_id).push(row);
  }
  for (const [eventId, rows] of danglingByEvent) {
    const owner = rows[0].owner_id ? brain.byId.get(rows[0].owner_id) : null;
    push(
      `dangling-event-${eventId}`,
      'quality', 'medium',
      `事件 ${eventId} 有 ${rows.length} 个来源指向已删除的记忆（${rows.map((row) => row.memory_id).join('、')}），事件与记忆的关联断了一半。`,
      owner ? [owner] : [],
    );
  }

  const tombstonesWithVectors = brain.memories.filter((memory) => memory.tombstone && brain.vectorIds.has(memory.id));
  if (tombstonesWithVectors.length) {
    push(
      'tombstone-vectors',
      'quality', 'low',
      `${tombstonesWithVectors.length} 条墓碑记忆仍然保留向量。它们不会出现在正常召回里，但会继续占空间；是否清理需要 Lan 决定。`,
      tombstonesWithVectors.slice(0, 8),
    );
  }

  const longMemories = live.filter((memory) => memory.body.length > 5000);
  for (const memory of longMemories.slice(0, 10)) {
    push(
      `long-body-${memory.id}`,
      'split', 'low',
      `正文 ${memory.body.length} 字，超过单条健康阈值（5000 字）。可能应该拆成多条记忆，或确认它确实是一整段经历。`,
      [memory],
    );
  }

  const hidden = live.filter((memory) => memory.hidden);
  if (hidden.length) {
    push(
      'hidden-memories',
      'quality', 'low',
      `${hidden.length} 条记忆被标记为隐藏。它们不会进入召回，请确认这是 Lan 的意图。`,
      hidden,
    );
  }

  const summary = {
    duplicate: 0, quality: 0, split: 0, contradiction: 0, reclassify: 0, security: 0, total: issues.length,
  };
  for (const issue of issues) summary[issue.type] = (summary[issue.type] || 0) + 1;

  return {
    check_id: 'brain-health-local',
    status: 'completed',
    progress: {
      phase: 'done', total_memories: live.length, processed: live.length, percent: 100, issues_found: issues.length,
    },
    summary,
    issues,
    error: null,
    created_at: new Date().toISOString(),
    expires_at: null,
    mode: 'scan',
    target_state: 'complete',
  };
}

function browse(brain, url) {
  const limit = Math.max(1, Math.min(200, Number(url.searchParams.get('limit')) || 50));
  const offset = decodeCursor(url.searchParams.get('cursor'));
  const type = url.searchParams.get('memory_type') || '';
  const categories = parseList(url.searchParams.get('categories'));
  const sort = url.searchParams.get('sort') || 'newest';
  const start = url.searchParams.get('created_start');
  const end = url.searchParams.get('created_end');

  let rows = brain.memories.filter((memory) => !memory.deleted);
  if (type) rows = rows.filter((memory) => memory.bucket === type);
  if (categories.length) rows = rows.filter((memory) => categories.every((name) => memory.domains.includes(name)));
  const role = url.searchParams.get('role') || '';
  const agent = url.searchParams.get('agent_id') || '';
  if (role && role !== 'user') rows = [];
  if (agent && agent !== 'peter') rows = [];
  if (url.searchParams.get('has_artifacts') === 'true') rows = [];
  if (url.searchParams.get('decayed_only') === 'true') rows = [];
  if (start) rows = rows.filter((memory) => memory.created && new Date(memory.created) >= new Date(start));
  if (end) rows = rows.filter((memory) => memory.created && new Date(memory.created) <= new Date(end));
  rows.sort((a, b) => {
    const left = new Date(a.created || 0).getTime() || 0;
    const right = new Date(b.created || 0).getTime() || 0;
    return sort === 'oldest' ? left - right : right - left;
  });
  const page = rows.slice(offset, offset + limit);
  const nextOffset = offset + page.length;
  return {
    results: page.map(toItem),
    next_cursor: nextOffset < rows.length ? encodeCursor(nextOffset) : null,
    has_more: nextOffset < rows.length,
  };
}

function search(brain, body = {}) {
  const needle = String(body.query || '').trim().toLocaleLowerCase();
  if (!needle) return [];
  const limit = Math.min(50, Math.max(1, Number(body.limit) || 10));
  const type = body.memory_type || '';
  const categories = parseList(body.categories);
  const role = body.role || '';
  return brain.memories
    .filter((memory) => !memory.deleted)
    .filter((memory) => !type || memory.bucket === type)
    .filter((memory) => !categories.length || categories.every((name) => memory.domains.includes(name)))
    .filter((memory) => !role || role === 'user')
    .filter((memory) => memory.content.toLocaleLowerCase().includes(needle))
    .sort((a, b) => (new Date(b.created || 0).getTime() || 0) - (new Date(a.created || 0).getTime() || 0))
    .slice(0, limit)
    .map((memory) => ({ ...toItem(memory), score: 1 }));
}

function serveStatic(req, res, pathname) {
  let relative;
  try {
    relative = decodeURIComponent(pathname.replace(/^\/ui\/?/, ''));
  } catch {
    res.writeHead(400); res.end('bad path'); return;
  }
  if (!relative || relative.endsWith('/')) relative += 'index.html';
  const target = path.resolve(UI_ROOT, relative);
  if (!target.startsWith(UI_ROOT + path.sep) && target !== UI_ROOT) {
    res.writeHead(403); res.end('forbidden'); return;
  }
  fs.stat(target, (error, info) => {
    if (error || !info.isFile()) { res.writeHead(404); res.end('not found'); return; }
    res.writeHead(200, {
      'Content-Type': MIME[path.extname(target)] || 'application/octet-stream',
      'Cache-Control': 'no-store',
      'Content-Length': info.size,
    });
    fs.createReadStream(target).pipe(res);
  });
}

function sendJson(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    'Content-Length': Buffer.byteLength(payload),
  });
  res.end(payload);
}

function createServer({ dbPath, token = '' }) {
  const brain = loadBrain(dbPath);
  const server = http.createServer(async (req, res) => {
    const localPort = req.socket.localPort;
    const host = req.headers.host || '';
    if (host !== `127.0.0.1:${localPort}` && host !== `localhost:${localPort}`) {
      res.writeHead(403); res.end('forbidden host'); return;
    }
    const url = new URL(req.url, 'http://127.0.0.1');
    const pathname = url.pathname;

    if (pathname === '/') {
      // Never redirect with the bearer token: any local process could read it
      // from a plain GET. The app opens /ui/?t=<token> directly instead.
      res.writeHead(404); res.end('not found'); return;
    }
    if (pathname.startsWith('/ui/')) {
      if (token) {
        const cookie = req.headers.cookie || '';
        if (url.searchParams.get('t') === token) {
          res.setHeader('Set-Cookie', `brain_token=${token}; Path=/; HttpOnly; SameSite=Strict`);
        } else if (!cookie.includes(`brain_token=${token}`)) {
          res.writeHead(403); res.end('token required'); return;
        }
      }
      serveStatic(req, res, pathname); return;
    }
    if (!pathname.startsWith('/api/')) { sendJson(res, 404, { detail: 'not found' }); return; }
    if (token) {
      const cookie = req.headers.cookie || '';
      if (!cookie.includes(`brain_token=${token}`) && req.headers['x-brain-token'] !== token) {
        sendJson(res, 401, { detail: 'Unauthorized' }); return;
      }
    }

    try {
      if (req.method === 'GET' && pathname === '/api/whoami') {
        sendJson(res, 200, {
          user_id: 'lan', agent_id: 'peter', timezone: 'Asia/Shanghai', can_switch_user: false,
        }); return;
      }
      if (req.method === 'GET' && pathname === '/api/stats') {
        sendJson(res, 200, statsOf(brain)); return;
      }
      if (req.method === 'GET' && pathname === '/api/categories') {
        const counts = new Map();
        for (const memory of brain.memories) {
          if (memory.deleted) continue;
          for (const domain of memory.domains) counts.set(domain, (counts.get(domain) || 0) + 1);
        }
        sendJson(res, 200, {
          categories: [...counts.entries()].sort((a, b) => b[1] - a[1])
            .map(([name, count]) => ({ name, description: `${count} 条记忆`, count })),
        }); return;
      }
      if (req.method === 'GET' && (pathname === '/api/memories' || pathname === '/api/memories/browse')) {
        const page = browse(brain, url);
        if (pathname === '/api/memories') page.results = page.results.slice(0, 500);
        sendJson(res, 200, page); return;
      }
      if (req.method === 'POST' && pathname === '/api/memories/search') {
        const body = await readJsonBody(req);
        sendJson(res, 200, { results: search(brain, body) }); return;
      }
      if (req.method === 'POST' && (pathname === '/api/memories/find' || pathname === '/api/memories/ask')) {
        const body = await readJsonBody(req);
        const results = search(brain, body);
        sendJson(res, 200, {
          answer: `本地只读原型：按关键词找到 ${results.length} 条记忆。语义检索属于 P1，尚未接入。`,
          results, count: results.length, queries: [String(body.question || body.query || '')], stats: {},
        }); return;
      }
      if (req.method === 'POST' && pathname === '/api/memories/by-ids') {
        const body = await readJsonBody(req);
        const results = (body.ids || []).map((id) => brain.byId.get(id)).filter(Boolean)
          .filter((memory) => !memory.deleted).map(toItem);
        sendJson(res, 200, { results }); return;
      }
      const history = pathname.match(/^\/api\/memories\/([^/]+)\/history$/);
      if (req.method === 'GET' && history) {
        const payload = historyOf(brain, decodeURIComponent(history[1]));
        if (!payload) { sendJson(res, 404, { detail: 'memory not found' }); return; }
        sendJson(res, 200, payload); return;
      }
      const links = pathname.match(/^\/api\/memories\/([^/]+)\/links$/);
      if (req.method === 'GET' && links) {
        const payload = linksOf(brain, decodeURIComponent(links[1]));
        if (!payload) { sendJson(res, 404, { detail: 'memory not found' }); return; }
        sendJson(res, 200, payload); return;
      }
      if (req.method === 'POST' && (pathname === '/api/fsck' || pathname === '/api/fsck/auto-run')) {
        sendJson(res, 200, { check_id: 'brain-health-local', status: 'running' }); return;
      }
      if (req.method === 'GET' && pathname.startsWith('/api/fsck/')) {
        sendJson(res, 200, healthOf(brain)); return;
      }
      sendJson(res, 403, { detail: 'VEIL Brain 只读原型：写入、修复与语义检索暂未接入。' });
    } catch (error) {
      sendJson(res, 500, { detail: error.message || 'internal error' });
    }
  });
  return { server, brain };
}

function start(options) {
  const { server, brain } = createServer(options);
  server.on('close', () => {
    try { brain.db.close(); } catch { /* already closed */ }
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(options.port || 0, '127.0.0.1', () => {
      const port = server.address().port;
      resolve({ server, brain, port, url: `http://127.0.0.1:${port}` });
    });
  });
}

if (require.main === module) {
  const args = parseArgs(process.argv.slice(2));
  if (!args.db) { console.error('usage: node server.cjs --db <brain.db> [--port 8799] [--token secret]'); process.exit(2); }
  const token = args.token || crypto.randomBytes(16).toString('hex');
  start({ dbPath: path.resolve(args.db), port: args.port, token }).then(({ port, brain }) => {
    console.log(`VEIL Brain prototype: http://127.0.0.1:${port}/?t=${token}`);
    console.log(`read-only brain.db: ${brain.memories.length} memories`);
  }).catch((error) => { console.error(error); process.exit(1); });
}

module.exports = { start };
