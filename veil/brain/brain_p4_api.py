"""P4: Read-only Brain overview, full searchable library, version diff, and optimistic-lock helpers.

Designed to stay cleanly decoupled from P6-S2 in-flight write modules so parallel branches do not conflict.
All direct database queries open brain.db with ?mode=ro URI.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

from veil.brain.brain_health import load_latest_report, load_snapshots


def _ro_connect(db_path):
    # Encoded file URI: '#'/'?'/'%' in the path must not change which file (and mode) SQLite opens.
    uri = Path(db_path).resolve().as_uri()
    conn = sqlite3.connect(f'{uri}?mode=ro', uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _json_load(text, default):
    try:
        val = json.loads(text) if isinstance(text, str) else text
        return val if val is not None else default
    except (ValueError, TypeError):
        return default


def _parse_dt(value):
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    for candidate in (s, s[:19], s[:10]):
        try:
            return datetime.fromisoformat(candidate).replace(tzinfo=None)
        except ValueError:
            continue
    return None


def _brain_db_path(hub):
    chat = getattr(hub, 'chat', None)
    memory = getattr(chat, 'memory', None)
    auth = getattr(memory, 'brain_authority', None)
    if auth is not None and getattr(auth, 'db_path', None):
        return Path(auth.db_path)
    cfg = getattr(chat, 'cfg', None)
    store = getattr(chat, 'store', None)
    if cfg is not None and store is not None:
        candidate = Path(store.root) / cfg.id / 'brain.db'
        if candidate.is_file():
            return candidate
    return None


def compute_line_diff(old_text, new_text):
    old_lines = (old_text or '').splitlines()
    new_lines = (new_text or '').splitlines()
    return list(difflib.unified_diff(old_lines, new_lines, fromfile='旧版本', tofile='当前版本', lineterm='', n=2))


def memory_sha256(hub, memory_id):
    db = _brain_db_path(hub)
    if db is None or not db.is_file():
        return None
    conn = _ro_connect(db)
    try:
        row = conn.execute('SELECT body, body_sha256, title FROM memories WHERE id=?', (memory_id,)).fetchone()
        if row is None:
            return None
        body = row['body'] or ''
        return row['body_sha256'] or hashlib.sha256(body.encode('utf-8')).hexdigest()
    finally:
        conn.close()


def library(hub, query_params):
    """Serve full searchable, filterable, sortable, paginated P4 Memory Library from brain.db."""
    db = _brain_db_path(hub)
    q = (query_params.get('q', [''])[0] or '').strip().casefold()
    status = (query_params.get('status', ['active'])[0] or 'active').strip()
    kind = (query_params.get('kind', ['all'])[0] or 'all').strip()
    source = (query_params.get('source', ['all'])[0] or 'all').strip()
    sort = (query_params.get('sort', ['updated'])[0] or 'updated').strip()
    try:
        limit = max(1, min(200, int(query_params.get('limit', ['100'])[0] or 100)))
    except ValueError:
        limit = 100
    try:
        offset = max(0, int(query_params.get('offset', ['0'])[0] or 0))
    except ValueError:
        offset = 0

    if db is None or not db.is_file():
        return {'items': [], 'total': 0, 'filtered': 0, 'source': 'unavailable'}

    recalled_counts = {}
    chat = getattr(hub, 'chat', None)
    if chat is not None and getattr(chat, 'store', None) and getattr(chat, 'cfg', None):
        try:
            for row in chat.store.read_tail(f'{chat.cfg.id}/context-log.jsonl', 200):
                for mid in row.get('memory_ids') or []:
                    recalled_counts[mid] = recalled_counts.get(mid, 0) + 1
        except Exception:
            pass

    conn = _ro_connect(db)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = conn.execute(
            'SELECT m.id, m.title, m.name, m.created, m.last_active, m.type, m.bucket, '
            'm.importance, m.pinned, m.why_remembered, m.body, m.body_sha256, '
            'm.vector_state, m.is_tombstone, m.is_deleted, '
            's.locked, s.links_json, s.sources_json, s.merged_into, s.lan_edited, s.peter_after_lan '
            'FROM memories m LEFT JOIN sidecar_state s ON s.memory_id=m.id '
            'WHERE m.is_tombstone=0 AND m.is_deleted=0'
        ).fetchall()

        ver_meta = {}
        if 'versions' in tables:
            for vr in conn.execute(
                'SELECT memory_id, COUNT(*) AS cnt, MAX(time) AS last_ver_time FROM versions GROUP BY memory_id'
            ):
                ver_meta[vr['memory_id']] = (vr['cnt'], vr['last_ver_time'])

        pending_del = set()
        if 'delete_requests' in tables:
            for dr in conn.execute("SELECT memory_id FROM delete_requests WHERE status='pending'"):
                pending_del.add(dr['memory_id'])

        event_by_mem = {}
        if 'events' in tables:
            for er in conn.execute('SELECT event_id, title, memory_id FROM events WHERE memory_id IS NOT NULL'):
                event_by_mem[er['memory_id']] = {'event_id': er['event_id'], 'title': er['title'] or er['event_id']}

        titles = {r['id']: (r['title'] or r['name'] or r['id']) for r in rows}
        continued_by = {}
        parsed_links = {}
        for r in rows:
            mid = r['id']
            links = _json_load(r['links_json'], [])
            if not isinstance(links, list):
                links = []
            parsed_links[mid] = links
            for lid in links:
                continued_by.setdefault(lid, []).append({'memory_id': mid, 'title': titles.get(mid, mid)})

        items = []
        for r in rows:
            mid = r['id']
            title = r['title'] or r['name'] or mid
            if title.strip() == '原文来源':
                continue
            archived = r['bucket'] == 'archive'
            locked = bool(r['locked'])
            vstate = r['vector_state'] or 'ready'
            pinned = bool(r['pinned'])
            raw_body = r['body'] or ''
            body = raw_body.strip()
            why = (r['why_remembered'] or '').strip()
            sources = _json_load(r['sources_json'], [])
            if not isinstance(sources, list):
                sources = []

            src_kind = 'chat'
            if r['lan_edited'] and not r['peter_after_lan']:
                src_kind = 'lan'
            elif any(isinstance(s, dict) and s.get('kind') == 'experience' for s in sources):
                src_kind = 'experience'

            vcnt, last_ver_time = ver_meta.get(mid, (0, ''))
            last_src_time = ''
            if sources and isinstance(sources[-1], dict):
                last_src_time = str(sources[-1].get('time') or '')
            updated_at = max((t for t in (r['created'] or '', last_ver_time or '', last_src_time, r['last_active'] or '') if t), default='')
            ev_info = event_by_mem.get(mid)

            # Filtering
            if status == 'active' and archived:
                continue
            if status == 'archived' and not archived:
                continue
            if status == 'locked' and not locked:
                continue
            if status == 'pending_vector' and vstate == 'ready':
                continue

            if kind == 'pinned' and not pinned:
                continue
            if kind == 'event_link' and not ev_info:
                continue
            if kind == 'fragment' and not (len(body) < 40 and not pinned and not why and not ev_info):
                continue

            if source != 'all' and src_kind != source:
                continue

            if q:
                hay = f"{title} {why} {body} {mid}".casefold()
                if q not in hay:
                    continue

            # Hash the stored body exactly as the edit endpoint does; a stripped copy would
            # produce a permanent 409 for memories whose stored body ends with whitespace.
            sha = r['body_sha256'] or hashlib.sha256(raw_body.encode('utf-8')).hexdigest()
            items.append({
                'memory_id': mid,
                'title': title,
                'created': r['created'] or '',
                'updated_at': updated_at,
                'kind': r['type'] or '',
                'bucket': r['bucket'] or 'dynamic',
                'importance': r['importance'] if r['importance'] is not None else '',
                'pinned': pinned,
                'archived': archived,
                'locked': locked,
                'vector_state': vstate,
                'merged_into': r['merged_into'],
                'delete_pending': mid in pending_del,
                'lan_edited': r['lan_edited'],
                'peter_after_lan': r['peter_after_lan'],
                'why': why,
                'body': body,
                'body_exact': raw_body,
                'sha256': sha,
                'recalled': recalled_counts.get(mid, 0),
                'version_count': vcnt,
                'source_channel': src_kind,
                'event': ev_info,
                'links': [{'memory_id': k, 'title': titles.get(k, k)} for k in parsed_links.get(mid, []) if k in titles],
                'continued_by': continued_by.get(mid, []),
                'sources': sources[-5:],
            })

        total_live = sum(1 for r in rows if not (r['title'] or '').strip() == '原文来源')
        if sort == 'importance':
            items.sort(key=lambda x: (float(x['importance']) if str(x['importance']).replace('.', '', 1).isdigit() else 0.0, x['updated_at']), reverse=True)
        elif sort == 'recalled':
            items.sort(key=lambda x: (x['recalled'], x['updated_at']), reverse=True)
        elif sort == 'created':
            items.sort(key=lambda x: x['created'], reverse=True)
        else:
            items.sort(key=lambda x: x['updated_at'], reverse=True)

        paged = items[offset:offset + limit]
        return {
            'items': paged,
            'total': total_live,
            'filtered': len(items),
            'offset': offset,
            'limit': limit,
            'source': 'brain.db',
        }
    finally:
        conn.close()


def overview(hub):
    """Aggregate read-only P4 overview: counts, 7d/30d trends, 6 memory layers, activities, and health summary."""
    chat = getattr(hub, 'chat', None)
    cfg = getattr(chat, 'cfg', None)
    store = getattr(chat, 'store', None)
    db = _brain_db_path(hub)

    counts = {
        'active': 0, 'pinned': 0, 'archived': 0, 'tombstones': 0,
        'pending_vector': 0, 'events_total': 0, 'events_pending_story': 0, 'events_pending_review': 0,
        'week_added': 0, 'week_updated': 0,
        'by_bucket': {}, 'by_type': {},
    }
    trend_7d = []
    trend_30d = []
    activities = []

    now_dt = datetime.now()
    seven_days_ago = now_dt - timedelta(days=7)

    if db is not None and db.is_file():
        conn = _ro_connect(db)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            mem_rows = conn.execute(
                'SELECT id, title, created, type, bucket, pinned, vector_state, is_tombstone, is_deleted '
                'FROM memories'
            ).fetchall() if 'memories' in tables else []
            tomb_cnt = conn.execute('SELECT COUNT(*) FROM tombstones').fetchone()[0] if 'tombstones' in tables else 0
            counts['tombstones'] = tomb_cnt

            day_added = {}
            day_updated = {}
            for i in range(6, -1, -1):
                key = (now_dt - timedelta(days=i)).strftime('%Y-%m-%d')
                day_added[key] = 0
                day_updated[key] = 0

            week_added_buckets = [0, 0, 0, 0]
            week_upd_buckets = [0, 0, 0, 0]

            for r in mem_rows:
                if r['is_tombstone'] or r['is_deleted']:
                    continue
                if r['bucket'] == 'archive':
                    counts['archived'] += 1
                    continue
                counts['active'] += 1
                if r['pinned']:
                    counts['pinned'] += 1
                if (r['vector_state'] or 'ready') != 'ready':
                    counts['pending_vector'] += 1
                b = r['bucket'] or 'dynamic'
                t = r['type'] or 'memory'
                counts['by_bucket'][b] = counts['by_bucket'].get(b, 0) + 1
                counts['by_type'][t] = counts['by_type'].get(t, 0) + 1

                cdt = _parse_dt(r['created'])
                if cdt:
                    if cdt >= seven_days_ago:
                        counts['week_added'] += 1
                    dk = cdt.strftime('%Y-%m-%d')
                    if dk in day_added:
                        day_added[dk] += 1
                    diff_days = (now_dt.date() - cdt.date()).days
                    if 0 <= diff_days < 28:
                        week_added_buckets[3 - (diff_days // 7)] += 1

            if 'versions' in tables:
                # Filter by the reporting window (28 days covers both charts); the newest-400 cap
                # silently undercounted a busy week.
                window_start = (now_dt - timedelta(days=27)).strftime('%Y-%m-%d')
                for vr in conn.execute(
                        "SELECT memory_id, time, operation_reason FROM versions "
                        "WHERE substr(time, 1, 10) >= ?", (window_start,)):
                    vdt = _parse_dt(vr['time'])
                    if vdt:
                        if vdt >= seven_days_ago:
                            counts['week_updated'] += 1
                        dk = vdt.strftime('%Y-%m-%d')
                        if dk in day_updated:
                            day_updated[dk] += 1
                        diff_days = (now_dt.date() - vdt.date()).days
                        if 0 <= diff_days < 28:
                            week_upd_buckets[3 - (diff_days // 7)] += 1

            running_total = counts['active']
            day_keys = sorted(day_added.keys())
            # Build 7d series using real created & version counts (do not fabricate missing snapshots)
            for idx, dk in enumerate(day_keys):
                label = '今天' if idx == len(day_keys) - 1 else dk[5:].replace('-', '/')
                trend_7d.append({
                    'label': label,
                    'date': dk,
                    'added': day_added[dk],
                    'updated': day_updated[dk],
                })
            for w in range(4):
                trend_30d.append({
                    'label': f'第{w + 1}周',
                    'added': week_added_buckets[w],
                    'updated': week_upd_buckets[w],
                })

            if 'events' in tables:
                for er in conn.execute('SELECT event_id, payload_json FROM events'):
                    counts['events_total'] += 1
                    p = _json_load(er['payload_json'], {})
                    if p.get('story_pending'):
                        counts['events_pending_story'] += 1
                    if p.get('pending_review'):
                        counts['events_pending_review'] += 1

            if 'write_journal' in tables:
                for jr in conn.execute('SELECT time, operation, memory_id, detail_json, status FROM write_journal ORDER BY seq DESC LIMIT 12'):
                    detail = _json_load(jr['detail_json'], {})
                    activities.append({
                        'time': jr['time'] or '',
                        'kind': 'write',
                        'title': f"{jr['operation']} · {detail.get('title') or jr['memory_id'] or ''}",
                        'meta': f"status={jr['status']} · {jr['time'] or ''}",
                        'memory_id': jr['memory_id'] or '',
                        'detail': detail.get('reason') or jr['operation'],
                    })
            if 'event_ledger' in tables:
                for lr in conn.execute("SELECT at, kind, event_id, payload_json FROM event_ledger WHERE kind IN ('event-op','event-dream-report') ORDER BY id DESC LIMIT 6"):
                    payload = _json_load(lr['payload_json'], {})
                    activities.append({
                        'time': lr['at'] or '',
                        'kind': 'event',
                        'title': f"事件维护 ({lr['kind']}) · {lr['event_id'] or ''}",
                        'meta': lr['at'] or '',
                        'event_id': lr['event_id'] or '',
                        'detail': json.dumps(payload, ensure_ascii=False)[:180],
                    })
            activities.sort(key=lambda x: x.get('time', ''), reverse=True)
            activities = activities[:15]
        finally:
            conn.close()

    # 6-layer statuses (enabled / ready / running)
    experience = getattr(chat, 'experience', None)
    exp_status = experience.status() if experience is not None else {'enabled': False, 'available': False}
    judge = getattr(getattr(chat, 'memory', None), 'judge', None)
    judge_status = judge.status() if judge is not None else {'enabled': False, 'available': False}
    frozen = bool(store and cfg and (Path(store.root) / cfg.id / 'brain-write-freeze').exists())
    learned_status = chat.profile.status(with_text=False) if chat and getattr(chat, 'profile', None) and hasattr(chat.profile, 'status') else {}
    short_notes_cfg = getattr(cfg, 'short_term_notes', None)
    short_notes_enabled = bool(getattr(short_notes_cfg, 'enabled', False)) if short_notes_cfg else False

    layers = [
        {
            'id': 'core',
            'name': 'Brain 核心存储',
            'enabled': True,
            'ready': db is not None and db.is_file(),
            'running': not frozen,
            'tone': 'warn' if frozen else 'good',
            'status_text': '已冻结写入' if frozen else '已启用 · 就绪 · 运行中',
            'detail': 'brain.db 唯一读写权威' + (' · 当前处于写入冻结态' if frozen else ' · 写入正常'),
        },
        {
            'id': 'vector',
            'name': '向量混合索引',
            'enabled': True,
            'ready': counts['pending_vector'] == 0,
            'running': True,
            'tone': 'warn' if counts['pending_vector'] else 'good',
            'status_text': f"待补向量 {counts['pending_vector']} 条" if counts['pending_vector'] else '已启用 · 就绪 · 运行中',
            'detail': 'BGE-M3 1024维 · 降级时自动保底 BM25',
        },
        {
            'id': 'experience',
            'name': '自动经历整理',
            'enabled': bool(exp_status.get('enabled')),
            'ready': bool(exp_status.get('available')),
            'running': bool(exp_status.get('enabled') and exp_status.get('available')),
            'tone': 'good' if exp_status.get('enabled') else '',
            'status_text': '已启用 · 就绪' if exp_status.get('enabled') else '已关闭',
            'detail': f"待处理 {exp_status.get('pending_chars', 0)} / {exp_status.get('min_chars', 3000)} 字 · 今日 ¥{exp_status.get('spent_today', '0')}",
            'toggle_key': 'experience',
        },
        {
            'id': 'judge',
            'name': '想起判官',
            'enabled': bool(judge_status.get('enabled')),
            'ready': bool(judge_status.get('available')) and not judge_status.get('breaker_open'),
            'running': bool(judge_status.get('enabled') and not judge_status.get('breaker_open')),
            'tone': 'warn' if judge_status.get('breaker_open') else ('good' if judge_status.get('enabled') else ''),
            'status_text': '已熔断暂停' if judge_status.get('breaker_open') else ('已启用 · 运行中' if judge_status.get('enabled') else '已关闭'),
            'detail': f"今日 ¥{judge_status.get('spent_today', '0')} · 连续失败={judge_status.get('breaker_open', False)}",
            'toggle_key': 'judge',
        },
        {
            'id': 'cognition',
            'name': '关系底座与习得认知',
            'enabled': bool(learned_status.get('available', True)),
            'ready': bool(learned_status.get('available', True)),
            'running': True,
            'tone': 'good',
            'status_text': '已启用 · 就绪 · 每轮注入',
            'detail': f"共享预算 {learned_status.get('bytes', 0)} / {learned_status.get('max_bytes', 4000)} B",
        },
        {
            'id': 'short_notes',
            'name': '短期便签',
            'enabled': short_notes_enabled,
            'ready': short_notes_enabled,
            'running': short_notes_enabled,
            'tone': 'good' if short_notes_enabled else '',
            'status_text': '已启用 · 运行中' if short_notes_enabled else '已关闭（默认关）',
            'detail': '最多 10 条 / 800 字随实时对话淡出',
        },
    ]

    latest_health = None
    snapshots = []
    data_root = getattr(hub, 'data_root', None) or (getattr(store, 'root', None) if store is not None else None)
    if data_root is not None and cfg is not None:
        latest_health = load_latest_report(data_root, cfg.id)
        snapshots = load_snapshots(data_root, cfg.id, limit=14)

    return {
        'schema_version': 1,
        'source': 'brain.db',
        'counts': counts,
        'trends': {'7d': trend_7d, '30d': trend_30d},
        'layers': layers,
        'activities': activities,
        'experience': exp_status,
        'recall_judge': judge_status,
        'health_summary': latest_health.get('summary') if latest_health else None,
        'health_scanned_at': latest_health.get('scanned_at') if latest_health else None,
        'health_snapshots': snapshots,
    }


def learned_versions(hub):
    chat = getattr(hub, 'chat', None)
    cfg = getattr(chat, 'cfg', None)
    store = getattr(chat, 'store', None)
    if cfg is None or store is None:
        return []
    path = Path(store.root) / cfg.id / 'learned-context.versions.jsonl'
    if not path.is_file():
        return []
    rows = []
    try:
        with path.open(encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        rows.append(obj)
                except ValueError:
                    continue
    except OSError:
        return []
    return list(reversed(rows[-20:]))
