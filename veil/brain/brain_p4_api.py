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

            mem_lookup = {r['id']: r for r in mem_rows} if mem_rows else {}
            mem_bodies = {}
            if 'memories' in tables:
                for br in conn.execute('SELECT id, title, body, why_remembered FROM memories'):
                    mem_bodies[br['id']] = {
                        'title': br['title'] or br['id'],
                        'body': (br['body'] or '').strip(),
                        'why': (br['why_remembered'] or '').strip(),
                    }

            ver_history = {}
            if 'versions' in tables:
                v_cols = {c[1] for c in conn.execute('PRAGMA table_info(versions)')}
                v_id_col = 'version_id' if 'version_id' in v_cols else 'seq'
                v_txt_col = 'content' if 'content' in v_cols else 'old_content'
                for vr in conn.execute(f'SELECT {v_id_col} AS vid, memory_id, time, operation_reason, {v_txt_col} AS content FROM versions ORDER BY time ASC'):
                    ver_history.setdefault(vr['memory_id'], []).append({
                        'version_id': vr['vid'],
                        'time': vr['time'] or '',
                        'reason': vr['operation_reason'] or '',
                        'content': (vr['content'] or '').strip(),
                    })

            _OP_LABELS = {
                'add': '新增记忆',
                'create': '新增记忆',
                'insert': '新增记忆',
                'edit': '修订记忆',
                'update': '修订记忆',
                'panel_edit': '档案编辑',
                'archive': '移入归档',
                'unarchive': '恢复活跃',
                'restore': '恢复活跃',
                'merge': '合并记忆',
                'dream': '夜间整理',
                'experience': '经历提炼',
            }
            _STATUS_LABELS = {
                'ok': '已完成',
                'success': '已完成',
                'done': '已完成',
                'pending': '处理中',
                'failed': '未完成',
                'error': '异常',
            }
            _EVENT_KIND_LABELS = {
                'event-op': '事件整理',
                'event-update': '事件更新',
                'event-dream-report': '经历梳理',
            }

            if 'write_journal' in tables:
                for jr in conn.execute('SELECT time, operation, memory_id, detail_json, status FROM write_journal ORDER BY seq DESC LIMIT 12'):
                    detail = _json_load(jr['detail_json'], {})
                    op_raw = (jr['operation'] or '').strip()
                    op_lower = op_raw.lower()
                    if op_lower in ('noop', 'heartbeat', 'index_sync'):
                        continue
                    mid = jr['memory_id'] or ''
                    m_info = mem_bodies.get(mid, {})
                    title_text = detail.get('title') or m_info.get('title') or mid or '记忆变更'
                    op_label = _OP_LABELS.get(op_lower, '记忆变更')
                    status_label = _STATUS_LABELS.get(str(jr['status'] or '').lower(), '已完成')
                    source_label = detail.get('source') or ('经历整理' if 'experience' in op_lower else ('档案编辑' if 'panel' in op_lower else '日常对话'))

                    # Compute version diff if available
                    diff_lines = []
                    curr_content = detail.get('content') or m_info.get('body') or ''
                    vers = ver_history.get(mid, [])
                    if vers:
                        prev_text = vers[-1]['content']
                        if len(vers) >= 2 and prev_text == curr_content:
                            prev_text = vers[-2]['content']
                        if prev_text and curr_content and prev_text != curr_content:
                            diff_lines = compute_line_diff(prev_text, curr_content)
                    if not diff_lines and op_lower in ('add', 'create', 'insert') and curr_content:
                        diff_lines = [f'+ {ln}' for ln in curr_content.splitlines()[:8]]

                    activities.append({
                        'time': jr['time'] or '',
                        'kind': op_lower or 'write',
                        'kind_label': op_label,
                        'title': f"{op_label} · {title_text}",
                        'meta': f"{source_label} · {status_label} · {jr['time'] or ''}",
                        'memory_id': mid,
                        'source': source_label,
                        'detail': detail.get('reason') or m_info.get('why') or f'{op_label}已同步写入记忆库',
                        'content': curr_content,
                        'diff': diff_lines,
                    })
            if 'event_ledger' in tables:
                for lr in conn.execute("SELECT at, kind, event_id, payload_json FROM event_ledger WHERE kind IN ('event-op','event-dream-report','event-update') ORDER BY id DESC LIMIT 6"):
                    payload = _json_load(lr['payload_json'], {})
                    k_label = _EVENT_KIND_LABELS.get(lr['kind'], '事件记录')
                    ev_title = payload.get('title') or lr['event_id'] or '共同经历'
                    ev_summary = payload.get('summary') or payload.get('note') or payload.get('action') or '事件时间线已更新'
                    activities.append({
                        'time': lr['at'] or '',
                        'kind': 'event',
                        'kind_label': k_label,
                        'title': f"{k_label} · {ev_title}",
                        'meta': f"共同经历 · 已完成 · {lr['at'] or ''}",
                        'event_id': lr['event_id'] or '',
                        'source': '共同经历整理',
                        'detail': ev_summary,
                        'content': payload.get('story') or ev_summary,
                        'diff': [],
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
    learned_status = chat.profile.status(with_text=True) if chat and getattr(chat, 'profile', None) and hasattr(chat.profile, 'status') else {}
    cognition_budget = _compute_cognition_budget(learned_status)
    short_notes_cfg = getattr(cfg, 'short_term_notes', None)
    short_notes_enabled = bool(getattr(short_notes_cfg, 'enabled', False)) if short_notes_cfg else False

    layers = [
        {
            'id': 'core',
            'name': '核心记忆存储',
            'enabled': True,
            'ready': db is not None and db.is_file(),
            'running': not frozen,
            'protected': True,
            'protected_reason': '核心记忆存储底座，关闭将导致记忆读写不可用',
            'tone': 'warn' if frozen else 'good',
            'status_text': '已冻结写入' if frozen else '受保护 · 运行中',
            'detail': '主记忆库' + (' · 当前处于写入冻结态' if frozen else ' · 读写正常'),
        },
        {
            'id': 'vector',
            'name': '向量混合索引',
            'enabled': True,
            'ready': counts['pending_vector'] == 0,
            'running': True,
            'protected': True,
            'protected_reason': '与混合检索管线绑定，异常时自动切换关键词保底',
            'tone': 'warn' if counts['pending_vector'] else 'good',
            'status_text': f"待补索引 {counts['pending_vector']} 条" if counts['pending_vector'] else '受保护 · 就绪',
            'detail': '语义向量 + 关键词混合检索 · 降级自动保底',
        },
        {
            'id': 'experience',
            'name': '自动经历整理',
            'enabled': bool(exp_status.get('enabled')),
            'ready': bool(exp_status.get('available')),
            'running': bool(exp_status.get('enabled') and exp_status.get('available')),
            'protected': False,
            'tone': 'good' if exp_status.get('enabled') else '',
            'status_text': '已启用 · 就绪' if exp_status.get('enabled') else '已关闭',
            'detail': f"待整理 {exp_status.get('pending_chars', 0)} / {exp_status.get('min_chars', 3000)} 字 · 今日 ¥{exp_status.get('spent_today', '0')}",
            'toggle_key': 'experience',
        },
        {
            'id': 'judge',
            'name': '想起判官',
            'enabled': bool(judge_status.get('enabled')),
            'ready': bool(judge_status.get('available')) and not judge_status.get('breaker_open'),
            'running': bool(judge_status.get('enabled') and not judge_status.get('breaker_open')),
            'protected': False,
            'tone': 'warn' if judge_status.get('breaker_open') else ('good' if judge_status.get('enabled') else ''),
            'status_text': '已熔断暂停' if judge_status.get('breaker_open') else ('已启用 · 运行中' if judge_status.get('enabled') else '已关闭'),
            'detail': f"今日 ¥{judge_status.get('spent_today', '0')} · {'运行熔断中' if judge_status.get('breaker_open') else '状态正常'}",
            'toggle_key': 'judge',
        },
        {
            'id': 'cognition',
            'name': '关系底座与习得认知',
            'enabled': bool(learned_status.get('available', True)),
            'ready': bool(learned_status.get('available', True)),
            'running': True,
            'protected': True,
            'protected_reason': '每轮人格与关系上下文底座，受保护不可关闭',
            'tone': 'good',
            'status_text': '已启用 · 就绪 · 每轮注入',
            'detail': f"共享预算 {cognition_budget['used_bytes']} / {cognition_budget['max_bytes']} B",
            'budget_used': cognition_budget['used_bytes'],
            'budget_max': cognition_budget['max_bytes'],
        },
        {
            'id': 'short_notes',
            'name': '短期便签',
            'enabled': short_notes_enabled,
            'ready': short_notes_enabled,
            'running': short_notes_enabled,
            'protected': False,
            'tone': 'good' if short_notes_enabled else '',
            'status_text': '已启用 · 运行中' if short_notes_enabled else '已关闭',
            'detail': '最多 10 条 / 800 字随实时对话淡出',
            'toggle_key': 'short_notes',
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


def _utf8_bytes(text):
    return len(str(text or '').encode('utf-8'))


def _compute_cognition_budget(learned_status):
    """Compute shared byte budget consistently with peter-page.js (growth + facts + open ongoing + current relationship)."""
    if not isinstance(learned_status, dict):
        return {'used_bytes': 0, 'max_bytes': 4000, 'by_section': {}}
    max_bytes = int(learned_status.get('max_bytes') or 4000)
    sections = learned_status.get('sections') or {}
    growth_text = (sections.get('growth') or {}).get('text') or ''
    facts_text = (sections.get('facts') or {}).get('text') or ''
    ongoing_items = (learned_status.get('ongoing') or {}).get('items') or []
    rel_items = (learned_status.get('relationship') or {}).get('items') or []

    growth_b = _utf8_bytes(growth_text)
    facts_b = _utf8_bytes(facts_text)
    ongoing_b = sum(_utf8_bytes(it.get('text') or '') for it in ongoing_items if isinstance(it, dict) and (it.get('status') or 'active') not in ('done', 'closed', 'archived'))
    rel_b = sum(_utf8_bytes(it.get('text') or '') for it in rel_items if isinstance(it, dict) and not it.get('stale') and (it.get('status') or 'current') != 'outdated')
    used = growth_b + facts_b + ongoing_b + rel_b
    return {
        'used_bytes': used,
        'max_bytes': max_bytes,
        'by_section': {
            'growth': growth_b,
            'facts': facts_b,
            'ongoing': ongoing_b,
            'relationship': rel_b,
        },
    }


def toggle_layer(hub, layer_id, enabled):
    """Persistently toggle a safe, controllable memory layer and read back verified state."""
    chat = getattr(hub, 'chat', None)
    cfg = getattr(chat, 'cfg', None)
    if cfg is None:
        return {'ok': False, 'error': '未找到角色配置', 'status_code': 400}

    enabled = bool(enabled)
    if layer_id in ('core', 'vector', 'cognition'):
        return {'ok': False, 'error': '该层属于核心受保护机制，不支持独立关闭', 'status_code': 400}

    try:
        if layer_id == 'experience':
            exp = getattr(chat, 'experience', None)
            if exp is not None and hasattr(exp, 'set_enabled'):
                exp.set_enabled(enabled)
            elif getattr(cfg, 'experience_memory', None) is not None:
                cfg.experience_memory.enabled = enabled
            actual = bool(exp.status().get('enabled')) if exp is not None else bool(getattr(getattr(cfg, 'experience_memory', None), 'enabled', enabled))
            return {'ok': True, 'layer_id': layer_id, 'enabled': actual}

        if layer_id == 'judge':
            judge = getattr(getattr(chat, 'memory', None), 'judge', None)
            if judge is not None and hasattr(judge, 'set_enabled'):
                judge.set_enabled(enabled)
            elif getattr(cfg, 'recall_judge', None) is not None:
                cfg.recall_judge.enabled = enabled
            actual = bool(judge.status().get('enabled')) if judge is not None else bool(getattr(getattr(cfg, 'recall_judge', None), 'enabled', enabled))
            return {'ok': True, 'layer_id': layer_id, 'enabled': actual}

        if layer_id == 'short_notes':
            st = getattr(cfg, 'short_term_notes', None)
            if st is not None:
                st.enabled = enabled
                actual = bool(getattr(st, 'enabled', enabled))
                return {'ok': True, 'layer_id': layer_id, 'enabled': actual}
            return {'ok': False, 'error': '当前角色未配置短期便签模块', 'status_code': 400}

        return {'ok': False, 'error': f'未知的记忆层: {layer_id}', 'status_code': 404}
    except Exception as exc:
        return {'ok': False, 'error': f'切换失败: {exc}', 'status_code': 500}


def _resolve_date_range(range_key='7d', from_date='', to_date=''):
    today = datetime.now().date()
    if range_key == 'today':
        return today, today
    if range_key == '30d':
        return today - timedelta(days=29), today
    if range_key == 'custom' and from_date and to_date:
        try:
            d1 = datetime.strptime(from_date[:10], '%Y-%m-%d').date()
            d2 = datetime.strptime(to_date[:10], '%Y-%m-%d').date()
            if d1 <= d2:
                return d1, d2
            return d2, d1
        except ValueError:
            pass
    return today - timedelta(days=6), today


def growth_analysis(hub, range_key='7d', from_date='', to_date=''):
    """Real-ledger memory & event growth analysis over today / 7d / 30d / custom range."""
    d_from, d_to = _resolve_date_range(range_key, from_date, to_date)
    db = _brain_db_path(hub)
    days = []
    cur = d_from
    day_map = {}
    while cur <= d_to:
        dk = cur.strftime('%Y-%m-%d')
        row = {
            'date': dk,
            'label': '今天' if cur == datetime.now().date() else dk[5:].replace('-', '/'),
            'added': 0,
            'updated': 0,
            'archived': 0,
            'other_state': 0,
            'events_created': 0,
            'events_updated': 0,
            'net_growth': 0,
            'cumulative': 0,
            'by_type': {},
        }
        days.append(row)
        day_map[dk] = row
        cur += timedelta(days=1)

    summary = {
        'from_date': d_from.strftime('%Y-%m-%d'),
        'to_date': d_to.strftime('%Y-%m-%d'),
        'total_active': 0,
        'total_archived': 0,
        'total_events': 0,
        'actual_added': 0,
        'actual_updated': 0,
        'actual_archived': 0,
        'events_created': 0,
        'events_updated': 0,
        'net_growth': 0,
        'by_type': {},
        'by_bucket': {},
        'last_updated_at': datetime.now().isoformat(timespec='seconds'),
    }

    if db is None or not db.is_file():
        return {'summary': summary, 'days': days}

    conn = _ro_connect(db)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        before_count = 0
        archived_in_window_mids = set()
        if 'write_journal' in tables:
            for jr in conn.execute('SELECT time, operation, memory_id FROM write_journal'):
                jdt = _parse_dt(jr['time'])
                if not jdt:
                    continue
                dk = jdt.date().strftime('%Y-%m-%d')
                if dk not in day_map:
                    continue
                op = (jr['operation'] or '').lower()
                if 'archive' in op and 'unarchive' not in op:
                    day_map[dk]['archived'] += 1
                    summary['actual_archived'] += 1
                    if jr['memory_id']:
                        archived_in_window_mids.add(jr['memory_id'])
                elif op in ('restore', 'unarchive', 'delete', 'hard_delete', 'state'):
                    day_map[dk]['other_state'] += 1

        if 'memories' in tables:
            for r in conn.execute('SELECT id, created, type, bucket, is_tombstone, is_deleted FROM memories'):
                if r['is_tombstone'] or r['is_deleted']:
                    continue
                is_archived = (r['bucket'] == 'archive')
                if is_archived:
                    summary['total_archived'] += 1
                else:
                    summary['total_active'] += 1
                t = r['type'] or 'memory'
                b = r['bucket'] or 'dynamic'
                summary['by_type'][t] = summary['by_type'].get(t, 0) + 1
                summary['by_bucket'][b] = summary['by_bucket'].get(b, 0) + 1

                cdt = _parse_dt(r['created'])
                if cdt:
                    cd = cdt.date()
                    dk = cd.strftime('%Y-%m-%d')
                    if cd < d_from:
                        if not is_archived or r['id'] in archived_in_window_mids:
                            before_count += 1
                    elif dk in day_map:
                        if not is_archived or r['id'] in archived_in_window_mids:
                            day_map[dk]['added'] += 1
                            day_map[dk]['by_type'][t] = day_map[dk]['by_type'].get(t, 0) + 1
                            summary['actual_added'] += 1

        if 'versions' in tables:
            for vr in conn.execute('SELECT time, operation_reason FROM versions'):
                vdt = _parse_dt(vr['time'])
                if vdt:
                    dk = vdt.date().strftime('%Y-%m-%d')
                    if dk in day_map:
                        day_map[dk]['updated'] += 1
                        summary['actual_updated'] += 1

        if 'events' in tables:
            for er in conn.execute('SELECT event_id, payload_json FROM events'):
                summary['total_events'] += 1
                p = _json_load(er['payload_json'], {})
                cdt = _parse_dt(p.get('created_at') or p.get('occurred_at') or '')
                if cdt:
                    dk = cdt.date().strftime('%Y-%m-%d')
                    if dk in day_map:
                        day_map[dk]['events_created'] += 1
                        summary['events_created'] += 1

        if 'event_ledger' in tables:
            for lr in conn.execute("SELECT at, kind FROM event_ledger WHERE kind IN ('event-op', 'event-update')"):
                ldt = _parse_dt(lr['at'])
                if ldt:
                    dk = ldt.date().strftime('%Y-%m-%d')
                    if dk in day_map:
                        day_map[dk]['events_updated'] += 1
                        summary['events_updated'] += 1

        running = before_count
        for d in days:
            d['net_growth'] = d['added'] - d['archived']
            running += d['net_growth']
            d['cumulative'] = max(0, running)
        summary['net_growth'] = summary['actual_added'] - summary['actual_archived']
    finally:
        conn.close()

    return {'summary': summary, 'days': days}


def day_changes(hub, date_str):
    """Return real memory & event changes for a single date YYYY-MM-DD, separating maintenance logs."""
    db = _brain_db_path(hub)
    target = (date_str or '')[:10]
    changes = []
    maintenance_logs = []
    if not target or db is None or not db.is_file():
        return {'date': target, 'changes': changes, 'maintenance_logs': maintenance_logs}

    conn = _ro_connect(db)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'memories' in tables:
            m_cols = {c[1] for c in conn.execute('PRAGMA table_info(memories)')}
            m_txt_col = 'body' if 'body' in m_cols else 'content'
            for mr in conn.execute(f'SELECT id, title, type, bucket, created, {m_txt_col} AS content FROM memories WHERE substr(created, 1, 10) = ?', (target,)):
                changes.append({
                    'kind': 'added',
                    'kind_label': '新增记忆',
                    'time': mr['created'] or target,
                    'memory_id': mr['id'],
                    'title': mr['title'] or mr['id'],
                    'type': mr['type'] or 'memory',
                    'bucket': mr['bucket'] or 'dynamic',
                    'summary': (mr['content'] or '')[:120],
                    'content': mr['content'] or '',
                })
        if 'versions' in tables:
            v_cols = {c[1] for c in conn.execute('PRAGMA table_info(versions)')}
            v_id_col = 'version_id' if 'version_id' in v_cols else 'seq'
            v_txt_col = 'content' if 'content' in v_cols else 'old_content'
            for vr in conn.execute(f'SELECT {v_id_col} AS version_id, memory_id, time, operation_reason, {v_txt_col} AS content FROM versions WHERE substr(time, 1, 10) = ?', (target,)):
                changes.append({
                    'kind': 'updated',
                    'kind_label': '修订记忆',
                    'time': vr['time'] or target,
                    'memory_id': vr['memory_id'],
                    'version_id': vr['version_id'],
                    'title': f"版本修订 · {vr['memory_id']}",
                    'summary': vr['operation_reason'] or '内容修订',
                    'content': vr['content'] or '',
                    'reason': vr['operation_reason'] or '',
                })
        if 'write_journal' in tables:
            for jr in conn.execute('SELECT seq, time, operation, memory_id, detail_json, status FROM write_journal WHERE substr(time, 1, 10) = ?', (target,)):
                op = (jr['operation'] or '').lower()
                detail = _json_load(jr['detail_json'], {})
                if op in ('maintenance', 'dream', 'noop', 'heartbeat', 'index_sync'):
                    maintenance_logs.append({
                        'time': jr['time'] or target,
                        'operation': jr['operation'],
                        'status': jr['status'],
                        'detail': detail.get('reason') or jr['operation'],
                    })
                elif 'archive' in op or op in ('restore', 'delete', 'hard_delete'):
                    changes.append({
                        'kind': op,
                        'kind_label': '归档' if 'archive' in op else op,
                        'time': jr['time'] or target,
                        'memory_id': jr['memory_id'] or '',
                        'title': detail.get('title') or jr['memory_id'] or jr['operation'],
                        'summary': detail.get('reason') or f"状态变更 ({jr['operation']})",
                        'reason': detail.get('reason') or '',
                    })
        changes.sort(key=lambda x: x.get('time', ''), reverse=True)
    finally:
        conn.close()

    return {'date': target, 'changes': changes, 'maintenance_logs': maintenance_logs}


def generate_growth_report(hub, from_date='', to_date='', include_content=True, include_health=False, fmt='full'):
    """Deterministic local Markdown report for memory growth & changes (no paid LLM calls)."""
    range_key = 'custom' if (from_date and to_date) else '7d'
    data = growth_analysis(hub, range_key=range_key, from_date=from_date, to_date=to_date)
    s = data['summary']
    lines = [
        '# VEIL Brain · 记忆增长与变更报告',
        f"- 统计范围：{s['from_date']} 至 {s['to_date']}",
        f"- 生成时间：{s['last_updated_at']}",
        '- 生成方式：本地确定性统计（未调用付费模型，已排除凭据与敏感配置）',
        '',
        '## 1. 存量与增长统计',
        f"- 当前活跃记忆：{s['total_active']} 条（已归档 {s['total_archived']} 条，共同经历 {s['total_events']} 个）",
        f"- 所选范围实际新增：{s['actual_added']} 条",
        f"- 所选范围修订次数：{s['actual_updated']} 次",
        f"- 所选范围归档数量：{s['actual_archived']} 条",
        f"- 所选范围净增长：{s['net_growth']:+d} 条",
        f"- 事件故事：新建 {s['events_created']} 个 · 延续更新 {s['events_updated']} 次",
    ]

    if fmt != 'summary':
        lines.extend(['', '## 2. 逐日变更明细'])
        for d in data['days']:
            if d['added'] or d['updated'] or d['archived'] or d['events_created'] or d['events_updated']:
                lines.append(
                    f"- {d['date']}：新增 {d['added']} / 修订 {d['updated']} / 归档 {d['archived']} / "
                    f"事件新建 {d['events_created']} / 事件更新 {d['events_updated']}（净增长 {d['net_growth']:+d}）"
                )
        if include_content:
            lines.extend(['', '## 3. 记忆变更详情与技术证据'])
            has_detail = False
            for d in data['days']:
                if not (d['added'] or d['updated'] or d['archived']):
                    continue
                dc = day_changes(hub, d['date'])
                for c in dc['changes'][:20]:
                    has_detail = True
                    lines.append(f"### [{c['kind_label']}] {c.get('title') or c.get('memory_id')} ({c.get('time')})")
                    lines.append(f"- ID: `{c.get('memory_id') or 'N/A'}`")
                    if c.get('reason'):
                        lines.append(f"- 写入/变更原因: {c['reason']}")
                    else:
                        lines.append('- 写入/变更原因: 账本未记录具体原因（缺失原因：历史写入未附带 reason 字段）')
                    if c.get('content'):
                        lines.append(f"- 正文内容:\n```text\n{c['content'][:600]}\n```")
            if not has_detail:
                lines.append('- 所选时间范围内无具体记忆正文变更记录。')

    if include_health:
        chat = getattr(hub, 'chat', None)
        cfg = getattr(chat, 'cfg', None)
        store = getattr(chat, 'store', None)
        data_root = getattr(hub, 'data_root', None) or (getattr(store, 'root', None) if store is not None else None)
        latest = load_latest_report(data_root, cfg.id, from_date=s['from_date'], to_date=s['to_date']) if (data_root and cfg) else None
        lines.extend(['', '## 4. 关联健康巡检摘要'])
        if latest:
            hs = latest.get('summary') or {}
            lines.append(f"- 巡检状态：{hs.get('status', 'unknown')} · 异常数：{hs.get('issue_count', 0)} · 扫描时间：{latest.get('scanned_at', '')}")
        else:
            lines.append('- 暂无对应范围的健康巡检报告（未执行巡检）。')

    return {
        'ok': True,
        'markdown': '\n'.join(lines),
        'summary': s,
    }


def recall_turns(hub, page=1, limit=20, date='', purpose='', model='', status=''):
    """Paginated per-turn recall, injection, token & cost observability + historical trends."""
    chat = getattr(hub, 'chat', None)
    cfg = getattr(chat, 'cfg', None)
    store = getattr(chat, 'store', None)
    data_root = getattr(hub, 'data_root', None) or (getattr(store, 'root', None) if store is not None else None)
    if data_root is None or cfg is None:
        return {'items': [], 'total': 0, 'page': 1, 'limit': limit, 'trends': {}}

    from veil.system import diagnostics as diag
    snaps = diag.snapshots(data_root, cfg.id, '', limit=200)
    # Also read request_snapshots.jsonl if available for richer turn list
    snap_path = diag.snapshot_path(data_root, cfg.id)
    raw_snaps = diag._rows(snap_path, 200) if snap_path.is_file() else []
    source_rows = raw_snaps if raw_snaps else snaps

    turns = []
    trend_days = {}
    for row in reversed(source_rows):
        if not isinstance(row, dict):
            continue
        rid = str(row.get('request_id') or row.get('id') or '')
        ts = str(row.get('time') or row.get('created_at') or '')
        mdl = str(row.get('model') or '')
        purp = str(row.get('purpose') or row.get('kind') or 'chat')
        if date and not ts.startswith(date):
            continue
        if purpose and purpose != purp:
            continue
        if model and model.lower() not in mdl.lower():
            continue

        tr = (diag.trace(data_root, cfg.id, request_id=rid) or {}) if rid else {}
        prov_list = tr.get('provider') or tr.get('context') or []
        prov = prov_list[-1] if prov_list else {}
        row_usage = row.get('usage') or {}
        costs = tr.get('costs') or []
        if costs:
            total_cost = sum(float(c.get('cost_cny') or 0) for c in costs if isinstance(c, dict))
        elif row.get('cost_cny') is not None:
            total_cost = float(row.get('cost_cny'))
            costs = [{'cost_cny': total_cost}]
        else:
            total_cost = 0.0
        est_tokens = row.get('estimated_tokens') or prov.get('estimated_input_tokens') or row_usage.get('estimated_input_tokens')
        act_in = prov.get('actual_input_tokens') if prov.get('actual_input_tokens') is not None else row_usage.get('actual_input_tokens')
        act_out = prov.get('completion_tokens') if prov.get('completion_tokens') is not None else row_usage.get('completion_tokens')
        cache_hit = prov.get('cached_tokens') or prov.get('cache_read_input_tokens') or row_usage.get('cached_tokens')

        # Deduplicate memory IDs across stages & extract full chain, candidates vs injected, prompt sections
        recall_info = row.get('recall') or tr.get('recall') or {}
        if not isinstance(recall_info, dict):
            recall_info = {}
        mem_tokens = recall_info.get('memory_tokens')
        has_recall = bool(recall_info)
        if status == 'hit' and not has_recall:
            continue
        if status == 'miss' and has_recall:
            continue

        # 6-stage recall chain
        def _stage_count(val):
            if isinstance(val, list):
                return len(val)
            if isinstance(val, dict):
                if isinstance(val.get('items'), list):
                    return len(val['items'])
                return int(val.get('count') or 0)
            if isinstance(val, (int, float)):
                return int(val)
            return 1 if val else 0

        recall_chain = [
            {'key': 'relationship_base', 'label': '关系底座', 'count': _stage_count(recall_info.get('relationship_base')), 'detail': recall_info.get('relationship_base')},
            {'key': 'opening', 'label': '开场记忆', 'count': _stage_count(recall_info.get('opening')), 'detail': recall_info.get('opening')},
            {'key': 'relevant_recall', 'label': '相关召回', 'count': _stage_count(recall_info.get('relevant_recall')), 'detail': recall_info.get('relevant_recall')},
            {'key': 'natural_association', 'label': '自然联想', 'count': _stage_count(recall_info.get('natural_association')), 'detail': recall_info.get('natural_association')},
            {'key': 'carried', 'label': '延续记忆', 'count': _stage_count(recall_info.get('carried')), 'detail': recall_info.get('carried')},
            {'key': 'vector', 'label': '向量召回', 'count': _stage_count(recall_info.get('vector')), 'detail': recall_info.get('vector')},
        ]

        candidates = recall_info.get('candidates') or row.get('recall_candidates') or []
        injected = recall_info.get('injected') or recall_info.get('items') or recall_info.get('memories') or []
        injected_ids = {str(it.get('memory_id') or it.get('id') or '') for it in injected if isinstance(it, dict)}
        dropped_candidates = recall_info.get('dropped') or [
            c for c in candidates
            if isinstance(c, dict) and str(c.get('memory_id') or c.get('id') or '') not in injected_ids
        ]

        prompt_sections = row.get('prompt_sections') or row.get('sections') or tr.get('sections') or []
        cache_hit_rate = None
        if isinstance(cache_hit, (int, float)) and isinstance(act_in, (int, float)) and act_in > 0:
            cache_hit_rate = round((cache_hit / act_in) * 100, 1)

        turns.append({
            'request_id': rid,
            'time': ts,
            'model': mdl or '未指定模型',
            'purpose': purp,
            'purpose_label': {'chat': '日常对话', 'experience': '经历整理', 'judge': '想起判官'}.get(purp, purp),
            'summary': str(row.get('user_preview') or row.get('summary') or row.get('prompt_preview') or '对话轮次'),
            'tokens': {
                'estimated': est_tokens,
                'actual_input': act_in,
                'actual_output': act_out,
                'cache_hit': cache_hit,
                'cache_hit_rate': cache_hit_rate,
                'memory_tokens': mem_tokens,
                'is_estimated_only': act_in is None,
            },
            'cost_cny': round(total_cost, 6) if costs else None,
            'recall': recall_info,
            'recall_chain': recall_chain,
            'candidates': candidates,
            'injected': injected,
            'dropped_candidates': dropped_candidates,
            'prompt_sections': prompt_sections,
            'raw_available': bool(row),
        })

        dk = ts[:10]
        if dk:
            td = trend_days.setdefault(dk, {'date': dk, 'turns': 0, 'base_hits': 0, 'assoc_hits': 0, 'mem_tokens_sum': 0})
            td['turns'] += 1
            if recall_info.get('relationship_base'):
                td['base_hits'] += 1
            if recall_info.get('natural_association'):
                td['assoc_hits'] += 1
            if isinstance(mem_tokens, (int, float)):
                td['mem_tokens_sum'] += mem_tokens

    turns.sort(key=lambda x: x.get('time', ''), reverse=True)
    total = len(turns)
    p = max(1, int(page))
    lim = max(1, min(100, int(limit)))
    start = (p - 1) * lim
    return {
        'items': turns[start:start + lim],
        'total': total,
        'page': p,
        'limit': lim,
        'trends': sorted(trend_days.values(), key=lambda x: x['date']),
    }


def recall_turn_report(hub, request_id):
    """Deterministic single-turn recall & token/cost diagnostic report."""
    res = recall_turns(hub, page=1, limit=100)
    match = next((t for t in res['items'] if t['request_id'] == request_id), None)
    if not match:
        return {'ok': False, 'error': f'未找到对应的轮次记录'}
    tk = match['tokens']
    lines = [
        '# VEIL Brain · 单轮召回与注入诊断报告',
        f"- 时间：{match['time']}",
        f"- 模型：{match['model']} · 用途：{match.get('purpose_label') or match['purpose']}",
        f"- 请求标识：`{match['request_id']}`",
        f"- 对话摘要：{match['summary']}",
        '',
        '## 1. Token、缓存与成本证据',
        f"- 快照预估 Token：{tk['estimated'] if tk['estimated'] is not None else '未记录'}",
        f"- 实际输入 Token：{tk['actual_input'] if tk['actual_input'] is not None else '未计量（上游未返回 usage）'}",
        f"- 实际输出 Token：{tk['actual_output'] if tk['actual_output'] is not None else '未计量'}",
        f"- 缓存命中 Token：{tk['cache_hit'] if tk['cache_hit'] is not None else 0}" + (f"（命中率 {tk['cache_hit_rate']}%）" if tk.get('cache_hit_rate') is not None else ''),
        f"- 记忆注入 Token：{tk['memory_tokens'] if tk['memory_tokens'] is not None else '待核实'}",
        f"- 结算费用：{('¥' + str(match['cost_cny'])) if match['cost_cny'] is not None else '暂无结算记录'}",
    ]

    p_secs = match.get('prompt_sections') or []
    if p_secs:
        lines.extend(['', '## 2. Prompt 分区 Token 分布'])
        for sec in p_secs:
            if isinstance(sec, dict):
                lines.append(f"- {sec.get('label') or sec.get('name')}: {sec.get('tokens', 0)} tokens")

    chain = match.get('recall_chain') or []
    if chain:
        lines.extend(['', '## 3. 六层召回链路'])
        for st in chain:
            lines.append(f"- {st['label']}: 命中 {st['count']} 项")

    cands = match.get('candidates') or []
    inj = match.get('injected') or []
    dropped = match.get('dropped_candidates') or []
    lines.extend([
        '',
        '## 4. 候选检索与最终注入对比',
        f"- 候选召回：{len(cands)} 条 · 最终注入：{len(inj)} 条 · 未入选：{len(dropped)} 条",
    ])
    if inj:
        lines.append('- 最终注入记忆：' + '、'.join(str(x.get('title') or x.get('memory_id') or '') for x in inj if isinstance(x, dict)))
    if dropped:
        lines.append('- 未入选候选：' + '、'.join(
            f"{x.get('title') or x.get('memory_id') or ''}({x.get('drop_reason') or '预算/相关性筛选'})"
            for x in dropped if isinstance(x, dict)
        ))

    return {'ok': True, 'markdown': '\n'.join(lines), 'turn': match}
