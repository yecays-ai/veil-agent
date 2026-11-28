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


def _as_number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
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


def _weights_for(hub):
    """The live MemoryWeights object, or one built from the role store when Memory is absent."""
    chat = getattr(hub, 'chat', None)
    memory = getattr(chat, 'memory', None)
    live = getattr(memory, 'weights', None)
    if live is not None:
        return live
    cfg = getattr(chat, 'cfg', None)
    store = getattr(memory, 'store', None) or getattr(chat, 'store', None)
    if cfg is None or store is None:
        return None
    try:
        from veil.brain.memory_weights import MemoryWeights
        return MemoryWeights(cfg, store, brain_authority=getattr(memory, 'brain_authority', None))
    except Exception:
        return None


def _proposal_map(db, memory_ids):
    """One bounded query for the newest proposal of every memory shown on this page."""
    ids = [mid for mid in memory_ids if mid]
    if not ids:
        return {}
    try:
        conn = _ro_connect(db)
    except sqlite3.Error:
        return {}
    marks = ','.join('?' * len(ids))
    try:
        # Bounded by the page: the newest row per memory, any pending row, and status counts.
        latest = conn.execute(
            'SELECT p.* FROM memory_importance_proposals p WHERE p.memory_id IN (%s) '
            'AND p.rowid IN (SELECT MAX(rowid) FROM memory_importance_proposals '
            'WHERE memory_id IN (%s) GROUP BY memory_id)' % (marks, marks),
            [*ids, *ids]).fetchall()
        pending = conn.execute(
            "SELECT p.* FROM memory_importance_proposals p WHERE p.memory_id IN (%s) "
            "AND p.status='pending'" % marks, ids).fetchall()
        counts = {}
        for row in conn.execute(
                'SELECT memory_id, status, COUNT(*) AS n FROM memory_importance_proposals '
                'WHERE memory_id IN (%s) GROUP BY memory_id, status' % marks, ids):
            counts.setdefault(row['memory_id'], {})[row['status']] = row['n']
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    mapping = {}
    for row in latest:
        mapping[row['memory_id']] = {
            'pending': None, 'latest': _proposal_item(row),
            'applied': counts.get(row['memory_id'], {}).get('applied', 0),
            'rejected': counts.get(row['memory_id'], {}).get('rejected', 0),
            'reverted': counts.get(row['memory_id'], {}).get('reverted', 0),
        }
    for row in pending:
        entry = mapping.setdefault(row['memory_id'], {
            'pending': None, 'latest': None,
            'applied': counts.get(row['memory_id'], {}).get('applied', 0),
            'rejected': counts.get(row['memory_id'], {}).get('rejected', 0),
            'reverted': counts.get(row['memory_id'], {}).get('reverted', 0),
        })
        entry['pending'] = _proposal_item(row)
    return mapping


def _proposal_item(row):
    return {
        'proposal_id': row['proposal_id'], 'memory_id': row['memory_id'],
        'created_at': row['created_at'], 'status': row['status'],
        'current_importance': row['current_importance'],
        'proposed_importance': row['proposed_importance'], 'delta': row['delta'],
        'confidence': row['confidence'], 'trigger': row['trigger'],
        'policy_version': row['policy_version'],
        'reasons': _json_load(row['reasons_json'], []),
        'evidence': _json_load(row['evidence_json'], []),
        'metrics': _json_load(row['metrics_json'], {}),
        'auto_applied': bool(row['auto_applied']), 'applied_at': row['applied_at'] or '',
        'version_seq': row['version_seq'], 'decided_at': row['decided_at'] or '',
        'decided_by': row['decided_by'] or '', 'note': row['note'] or '',
    }


def memory_weights_status(hub):
    weights = _weights_for(hub)
    if weights is None:
        return {'available': False, 'error': '记忆权重模块未就绪'}
    try:
        return weights.status()
    except Exception as exc:
        return {'available': False, 'error': type(exc).__name__}


def set_memory_weights(hub, values):
    weights = _weights_for(hub)
    if weights is None:
        return {'ok': False, 'error': '记忆权重模块未就绪', 'status_code': 503}
    try:
        result = weights.update_settings(values if isinstance(values, dict) else {})
    except Exception as exc:
        return {'ok': False, 'error': type(exc).__name__, 'status_code': 500}
    return {**result, 'status': weights.status()}


def memory_proposals(hub, *, memory_id='', status='', limit=50):
    weights = _weights_for(hub)
    if weights is None:
        return {'items': [], 'source': 'unavailable'}
    try:
        items = weights.proposals(memory_id=memory_id, status=status, limit=limit)
    except Exception:
        items = []
    return {'items': items, 'source': 'brain.db', 'limit': limit}


def decide_memory_proposal(hub, proposal_id, action, note=''):
    weights = _weights_for(hub)
    if weights is None:
        return {'ok': False, 'error': '记忆权重模块未就绪', 'status_code': 503}
    return weights.decide(proposal_id, action, note=note)


def reevaluate_memory(hub, memory_id):
    weights = _weights_for(hub)
    if weights is None:
        return {'ok': False, 'error': '记忆权重模块未就绪', 'status_code': 503}
    return weights.reevaluate(str(memory_id or ''), trigger='manual', force=True)


def _consolidator_for(hub):
    """The live CognitiveConsolidator, or one built from the role store when absent."""
    chat = getattr(hub, 'chat', None)
    live = getattr(chat, 'consolidation', None)
    if live is not None:
        return live
    cfg = getattr(chat, 'cfg', None)
    store = getattr(chat, 'store', None)
    if cfg is None or store is None:
        return None
    try:
        from veil.brain.cognitive_consolidation import CognitiveConsolidator
        return CognitiveConsolidator(
            cfg, store, getattr(chat, 'budget', None), None,
            memory=getattr(chat, 'memory', None), profile=getattr(chat, 'profile', None),
        )
    except Exception:
        return None


def consolidation_status(hub, *, runs_limit=12):
    cons = _consolidator_for(hub)
    if cons is None:
        return {'available': False, 'error': '认知巩固模块未就绪'}
    try:
        return cons.status(runs_limit=runs_limit)
    except Exception as exc:
        return {'available': False, 'error': type(exc).__name__}


def set_consolidation_settings(hub, values, *, expected_revision=None):
    cons = _consolidator_for(hub)
    if cons is None:
        return {'ok': False, 'error': '认知巩固模块未就绪', 'status_code': 503}
    try:
        result = cons.update_settings(
            values if isinstance(values, dict) else {},
            expected_revision=expected_revision,
        )
    except Exception as exc:
        return {'ok': False, 'error': type(exc).__name__, 'status_code': 500}
    return {**result, 'status': cons.status()}


def run_consolidation(hub, *, force=False):
    cons = _consolidator_for(hub)
    if cons is None:
        return {'ok': False, 'error': '认知巩固模块未就绪', 'status_code': 503}
    try:
        result = cons.run_once(trigger='manual', force=bool(force))
    except Exception as exc:
        return {'ok': False, 'error': type(exc).__name__, 'status_code': 500}
    return {**result, 'consolidation': cons.status()}


def decide_consolidation_experience(hub, candidate_id, action, note=''):
    cons = _consolidator_for(hub)
    if cons is None:
        return {'ok': False, 'error': '认知巩固模块未就绪', 'status_code': 503}
    try:
        return cons.decide_experience_candidate(candidate_id, action, note=note)
    except Exception as exc:
        return {'ok': False, 'error': type(exc).__name__, 'status_code': 500}


def decide_consolidation_cognition(hub, pending_id, action, note=''):
    cons = _consolidator_for(hub)
    if cons is None:
        return {'ok': False, 'error': '认知巩固模块未就绪', 'status_code': 503}
    try:
        return cons.decide_pending_cognition(pending_id, action, note=note)
    except Exception as exc:
        return {'ok': False, 'error': type(exc).__name__, 'status_code': 500}


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
        conn.execute('BEGIN DEFERRED')
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

        weights = _weights_for(hub)
        activation = {}
        if weights is not None:
            try:
                activation = weights.activation_map([r['id'] for r in rows])
            except Exception:
                activation = {}
        proposals = _proposal_map(db, [r['id'] for r in rows])
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
            # P6-S6-1: 'last_active' is now an activity timestamp, not an edit. Keeping it out of
            # updated_at preserves the existing "recently updated" meaning; activity has its own
            # field and sort order.
            updated_at = max((t for t in (r['created'] or '', last_ver_time or '', last_src_time or '')
                              if t), default='')
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
            if kind == 'proposal' and not (proposals.get(mid, {}).get('pending')):
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
            activation_row = activation.get(mid)
            items.append({
                'memory_id': mid,
                'title': title,
                'created': r['created'] or '',
                # memories.last_active starts as the creation timestamp; only an activation row
                # proves the memory was actually delivered.
                'last_active': (activation_row or {}).get('last_active') or '',
                'activation': activation_row or {
                    'last_active': '', 'last_delivery_at': '',
                    'first_delivery_at': '', 'deliveries': 0, 'distinct_days': 0,
                    'distinct_sessions': 0, 'score': 0.0, 'display': 0, 'stored_score': 0.0,
                    'protected': bool(pinned) or (_as_number(r['importance']) or 5) >= 7,
                    'decayed': False, 'last_request_id': '',
                },
                'reeval': proposals.get(mid) or {'pending': None, 'latest': None,
                                                 'applied': 0, 'rejected': 0, 'reverted': 0},
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
        elif sort == 'activity':
            items.sort(key=lambda x: ((x.get('activation') or {}).get('score', 0.0) or 0.0,
                                      x['updated_at']), reverse=True)
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
                    dk = cdt.strftime('%Y-%m-%d')
                    if dk in day_added:
                        day_added[dk] += 1
                        counts['week_added'] += 1
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
                        dk = vdt.strftime('%Y-%m-%d')
                        if dk in day_updated:
                            day_updated[dk] += 1
                            counts['week_updated'] += 1
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
                if 'version_id' in v_cols and 'content' in v_cols:
                    v_sql = 'SELECT version_id AS vid, memory_id, time, operation_reason, content AS content FROM versions ORDER BY time ASC'
                elif 'version_id' in v_cols:
                    v_sql = 'SELECT version_id AS vid, memory_id, time, operation_reason, old_content AS content FROM versions ORDER BY time ASC'
                elif 'content' in v_cols:
                    v_sql = 'SELECT seq AS vid, memory_id, time, operation_reason, content AS content FROM versions ORDER BY time ASC'
                else:
                    v_sql = 'SELECT seq AS vid, memory_id, time, operation_reason, old_content AS content FROM versions ORDER BY time ASC'
                for vr in conn.execute(v_sql):
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
    saved_switches = _load_layer_switches(store, cfg)
    short_notes_cfg = getattr(cfg, 'short_term_notes', None)
    if 'short_notes' in saved_switches:
        short_notes_enabled = bool(saved_switches['short_notes'])
    else:
        short_notes_enabled = bool(getattr(short_notes_cfg, 'enabled', False)) if short_notes_cfg else False
    if 'judge' in saved_switches and not (judge is not None and hasattr(judge, 'set_enabled')):
        judge_status = dict(judge_status)
        judge_status['enabled'] = bool(saved_switches['judge'])

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
            'ready': bool(learned_status.get('available', True)) and not learned_status.get('capacity_degraded'),
            'running': True,
            'protected': True,
            'protected_reason': '每轮人格与关系上下文底座，受保护不可关闭',
            'tone': 'warn' if learned_status.get('capacity_degraded') else 'good',
            'status_text': '已启用 · 就绪 · 每轮注入',
            'detail': f"四区独立预算 {cognition_budget['used_bytes']} / {cognition_budget['max_bytes']} B",
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

    mem = getattr(chat, 'memory', None)
    if mem is not None and hasattr(mem, 'association_status'):
        assoc_status = mem.association_status()
    else:
        from veil.brain.recall.recall_rotation import settings as load_rot_settings
        mem_cfg = getattr(cfg, 'memory', None) if cfg is not None else {}
        rot_base = load_rot_settings(mem_cfg)
        raw_recall = (mem_cfg or {}).get('recall') if isinstance((mem_cfg or {}).get('recall'), dict) else {}
        recall_enabled = bool((mem_cfg or {}).get('enabled', True)) and raw_recall.get('enabled') is not False
        # Read the same persistence file (`recall-association.json`) used by Memory._save_association_switches.
        persisted_assoc = {}
        if store is not None and cfg is not None:
            try:
                p_path = Path(store.root) / f'{cfg.id}/recall-association.json'
                if p_path.is_file():
                    raw_p = json.loads(p_path.read_text(encoding='utf-8'))
                    if isinstance(raw_p, dict):
                        persisted_assoc = raw_p
            except (OSError, ValueError):
                persisted_assoc = {}
        a_on = bool(persisted_assoc.get('association_enabled',
                                        saved_switches.get('association', rot_base.get('association_enabled', True))))
        a_ctx = bool(persisted_assoc.get('association_contextual_enabled',
                                         saved_switches.get('association_contextual', rot_base.get('association_contextual_enabled', False))))
        a_leisure = bool(persisted_assoc.get('leisure_association_enabled',
                                             saved_switches.get('association_leisure', rot_base.get('leisure_association_enabled', False))))
        assoc_status = {
            'available': True,
            'recall_enabled': recall_enabled,
            'enabled': a_on,
            'contextual_enabled': a_ctx,
            'leisure_enabled': a_leisure,
            'leisure_effective': bool(recall_enabled and a_on and a_leisure),
            'effective_mode': 'disabled' if not (recall_enabled and a_on) else ('contextual' if a_ctx else 'legacy'),
            'min_similarity': float(rot_base.get('association_min_similarity', 0.52)),
            'pool_size': int(rot_base.get('association_pool_size', 32)),
            'leisure_min_similarity': float(rot_base.get('leisure_association_min_similarity', 0.38)),
            'leisure_min_importance': int(rot_base.get('leisure_association_min_importance', 5)),
            'leisure_cooldown_turns': int(rot_base.get('leisure_association_cooldown_turns', 12)),
            'leisure_pool_size': int(rot_base.get('leisure_association_pool_size', 36)),
        }
    recall_on = bool(assoc_status.get('recall_enabled', True))
    leisure_on = bool(assoc_status.get('leisure_enabled', False))
    leisure_eff = bool(assoc_status.get('leisure_effective', recall_on and assoc_status['enabled'] and leisure_on))
    leisure_diag = {
        'recent_wakes': 0,
        'leisure_wakes': 0,
        'task_blocked': 0,
        'hits': 0,
        'audit_flagged': 0,
        'last_wake_at': '',
        'last_wake_mode': '',
        'last_reason': '',
        'last_picked': [],
        'last_similarity': None,
        'last_audit_clean': None,
    }
    data_root_for_diag = getattr(hub, 'data_root', None) or (getattr(store, 'root', None) if store is not None else None)
    if data_root_for_diag is not None and cfg is not None:
        auto_rows = _read_all_jsonl(Path(data_root_for_diag) / cfg.id / 'autonomy.jsonl', max_rows=50)
        for arow in auto_rows:
            if not isinstance(arow, dict):
                continue
            la = arow.get('leisure_association')
            audit = arow.get('output_audit')
            if isinstance(audit, dict) and audit.get('clean') is False:
                leisure_diag['audit_flagged'] += 1
            if isinstance(la, dict):
                leisure_diag['recent_wakes'] += 1
                if la.get('is_leisure'):
                    leisure_diag['leisure_wakes'] += 1
                elif str(la.get('reason') or '').startswith('task_wake_suppressed'):
                    leisure_diag['task_blocked'] += 1
                picked = la.get('picked') or []
                if picked:
                    leisure_diag['hits'] += 1
                leisure_diag['last_wake_at'] = str(arow.get('started_at') or '')
                leisure_diag['last_wake_mode'] = str(la.get('wake_mode') or '')
                leisure_diag['last_reason'] = str(la.get('reason') or '')
                leisure_diag['last_picked'] = list(picked)
                leisure_diag['last_similarity'] = la.get('similarity')
                leisure_diag['last_audit_clean'] = (
                    bool(audit.get('clean')) if isinstance(audit, dict) and 'clean' in audit else None
                )
    assoc_mode_label = {
        'disabled': '已关闭',
        'legacy': '已启用 · 现行联想机制',
        'contextual': '已启用 · 情境相关机制',
    }.get(assoc_status['effective_mode'], '已启用')
    ctx_status_text = (
        '已启用 · 下一轮生效' if (assoc_status['enabled'] and assoc_status['contextual_enabled'])
        else ('已保存开启（受总开关关闭抑制）' if assoc_status['contextual_enabled']
              else '已关闭 · 恢复现行联想机制')
    )
    leisure_status_text = (
        '已启用 · 下次自主闲暇唤醒生效' if leisure_eff
        else ('已保存开启（受记忆召回总开关关闭抑制）' if (leisure_on and not recall_on)
              else ('已保存开启（受自然联想总开关关闭抑制）' if leisure_on
                    else '已关闭 · 自主唤醒不触发联想'))
    )
    leisure_detail = (
        f"独立灰度开关（默认关闭）· 仅在自主闲暇态触发，任务态零干扰"
        + (f" · 近 {leisure_diag['recent_wakes']} 次唤醒：闲暇 {leisure_diag['leisure_wakes']} / 命中 {leisure_diag['hits']} / 任务拦截 {leisure_diag['task_blocked']}"
           if leisure_diag['recent_wakes'] > 0 else " · 暂无自主联想运行记录")
    )
    assoc_status = {
        **assoc_status,
        'status_text': assoc_mode_label,
        'contextual_status_text': ctx_status_text,
        'leisure_status_text': leisure_status_text,
        'leisure_diagnostics': leisure_diag,
        'controls': [
            {
                'id': 'association',
                'name': '自然联想总开关',
                'enabled': bool(assoc_status['enabled']),
                'tone': 'good' if assoc_status['enabled'] else '',
                'status_text': assoc_mode_label,
                'detail': f"当前生效：{assoc_mode_label} · 修改后下一轮聊天生效并持久化",
                'toggle_key': 'association',
                'effective_mode': assoc_status['effective_mode'],
            },
            {
                'id': 'association_contextual',
                'name': '情境相关自然联想',
                'enabled': bool(assoc_status['contextual_enabled']),
                'tone': 'good' if (assoc_status['enabled'] and assoc_status['contextual_enabled']) else ('warn' if assoc_status['contextual_enabled'] else ''),
                'status_text': ctx_status_text,
                'detail': f"候选机制（默认关闭）· 关闭后恢复现行联想机制",
                'toggle_key': 'association_contextual',
                'effective_mode': assoc_status['effective_mode'],
            },
            {
                'id': 'association_leisure',
                'name': '自主闲暇自然联想',
                'enabled': leisure_on,
                'effective': leisure_eff,
                'tone': 'good' if leisure_eff else ('warn' if leisure_on else ''),
                'status_text': leisure_status_text,
                'detail': leisure_detail,
                'toggle_key': 'association_leisure',
                'effective_mode': 'leisure' if leisure_eff else 'disabled',
            },
        ],
    }

    latest_health = None
    snapshots = []
    data_root = getattr(hub, 'data_root', None) or (getattr(store, 'root', None) if store is not None else None)
    if data_root is not None and cfg is not None:
        latest_health = load_latest_report(data_root, cfg.id)
        snapshots = load_snapshots(data_root, cfg.id, limit=14)

    recall_config = None
    if cfg is not None:
        try:
            from veil.core.config_registry import recall_config_overview
            recall_eff = (mem.effective_recall_settings()
                          if mem is not None and hasattr(mem, 'effective_recall_settings')
                          else getattr(mem, 'recall_settings', None))
            recall_config = recall_config_overview(
                role_id=cfg.id,
                memory_cfg=getattr(cfg, 'memory', None),
                retrieval_runtime=getattr(mem, 'retrieval_settings', None),
                recall_runtime=recall_eff,
                judge_enabled=judge.enabled() if judge is not None else None,
                judge_timeout=judge.settings.get('timeout_seconds') if judge is not None else None,
            )
        except Exception:
            recall_config = None

    cons_summary = None
    try:
        cs = consolidation_status(hub, runs_limit=3)
        if isinstance(cs, dict) and cs.get('available'):
            cons_summary = {
                'available': True,
                'enabled': bool((cs.get('settings') or {}).get('enabled', False)),
                'effective': cs.get('effective') or {},
                'budget': cs.get('budget') or {},
                'totals': cs.get('totals') or {},
                'p7_interface': cs.get('p7_interface') or {},
            }
    except Exception:
        cons_summary = None

    return {
        'schema_version': 1,
        'source': 'brain.db',
        'counts': counts,
        'trends': {'7d': trend_7d, '30d': trend_30d},
        'layers': layers,
        'activities': activities,
        'experience': exp_status,
        'recall_judge': judge_status,
        'recall_association': assoc_status,
        'recall_config': recall_config,
        'consolidation': cons_summary,
        'health_summary': latest_health.get('summary') if latest_health else None,
        'health_scanned_at': latest_health.get('scanned_at') if latest_health else None,
        'health_snapshots': snapshots,
    }


def _format_learned_snapshot(raw):
    """Turn a learned-context JSON snapshot string into readable sectioned text for diffing."""
    if not raw:
        return ''
    if isinstance(raw, str):
        parsed = _json_load(raw, None)
    elif isinstance(raw, dict):
        parsed = raw
    else:
        parsed = None
    if not isinstance(parsed, dict):
        return str(raw)
    parts = []
    facts_text = ((parsed.get('facts') or {}).get('text') or '').strip() if isinstance(parsed.get('facts'), dict) else ''
    if facts_text:
        parts.append('[习得认知]\n' + facts_text)
    growth_text = ((parsed.get('growth') or {}).get('text') or '').strip() if isinstance(parsed.get('growth'), dict) else ''
    if growth_text:
        parts.append('[成长记录]\n' + growth_text)
    rel = parsed.get('relationship')
    rel_items = rel.get('items') if isinstance(rel, dict) else None
    if isinstance(rel_items, list) and rel_items:
        lines = [f"- ({it.get('status') or 'active'}) {it.get('text') or ''}" for it in rel_items if isinstance(it, dict)]
        if lines:
            parts.append('[关系底座]\n' + '\n'.join(lines))
    ong = parsed.get('ongoing')
    ong_items = ong.get('items') if isinstance(ong, dict) else None
    if isinstance(ong_items, list) and ong_items:
        lines = [f"- ({it.get('status') or 'active'}) {it.get('text') or ''}" for it in ong_items if isinstance(it, dict)]
        if lines:
            parts.append('[进行中事项]\n' + '\n'.join(lines))
    return '\n\n'.join(parts)


_LEARNED_TOOL_LABELS = {
    'save_facts': '更新习得认知',
    'save_growth': '更新成长记录',
    'save_relationship': '更新关系底座',
    'save_ongoing': '更新进行中事项',
    'confirm_relationship': '确认关系底座',
    'veto_relationship': '否决关系底座',
    'restore_relationship': '恢复关系底座',
    'lan_edit': '人工编辑',
}


def learned_versions(hub):
    profile = getattr(getattr(hub, 'chat', None), 'profile', None)
    raw_items = profile.read_version_history() if hasattr(profile, 'read_version_history') else []
    if not isinstance(raw_items, list):
        return []
    enriched = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        tool = str(row.get('tool') or row.get('action') or '')
        if not row.get('reason'):
            row['reason'] = _LEARNED_TOOL_LABELS.get(tool) or (tool if tool else '自动更新')
        if not row.get('diff') and ('before' in row or 'after' in row):
            before_txt = _format_learned_snapshot(row.get('before'))
            after_txt = _format_learned_snapshot(row.get('after'))
            row['before_text'] = before_txt
            row['after_text'] = after_txt
            if before_txt != after_txt:
                row['diff'] = compute_line_diff(before_txt, after_txt)
        enriched.append(row)
    return enriched


def _utf8_bytes(text):
    return len(str(text or '').encode('utf-8'))


def _compute_cognition_budget(learned_status):
    """Use the same stored section usage as admission and the Peter editor."""
    if not isinstance(learned_status, dict):
        return {'used_bytes': 0, 'max_bytes': 4000, 'by_section': {}}
    budget = learned_status.get('budgets') or {}
    by_section = budget.get('sections')
    if by_section:
        return {
            'used_bytes': sum(row['used_bytes'] for row in by_section.values()),
            'max_bytes': sum(row['max_bytes'] for row in by_section.values()),
            'by_section': {key: row['used_bytes'] for key, row in by_section.items()},
            'sections': by_section,
        }
    max_bytes = int(learned_status.get('max_bytes') or 4000)
    sections = learned_status.get('sections') or {}
    growth_text = (sections.get('growth') or {}).get('text') or ''
    facts_text = (sections.get('facts') or {}).get('text') or ''
    ongoing_items = (learned_status.get('ongoing') or {}).get('items') or []
    rel_items = (learned_status.get('relationship') or {}).get('items') or []

    growth_b = _utf8_bytes(growth_text)
    facts_b = _utf8_bytes(facts_text)
    ongoing_b = sum(
        _utf8_bytes(it.get('text') or '')
        for it in ongoing_items
        if isinstance(it, dict) and (it.get('status') or 'active') not in ('done', 'completed', 'closed', 'archived')
    )
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


def _load_layer_switches(store, cfg):
    if store is None or cfg is None:
        return {}
    root = getattr(store, 'root', None)
    if not root:
        return {}
    path = Path(root) / cfg.id / 'brain-layer-switches.json'
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_layer_switch(store, cfg, layer_id, enabled):
    if store is None or cfg is None:
        return False, None
    root = getattr(store, 'root', None)
    if not root:
        return False, None
    rel = f'{cfg.id}/brain-layer-switches.json'
    data = _load_layer_switches(store, cfg)
    data[layer_id] = bool(enabled)
    if hasattr(store, 'replace'):
        store.replace(rel, data)
        return True, rel
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return True, rel


def toggle_layer(hub, layer_id, enabled):
    """Persistently toggle a safe, controllable memory layer and read back verified state."""
    chat = getattr(hub, 'chat', None)
    cfg = getattr(chat, 'cfg', None)
    store = getattr(chat, 'store', None)
    if cfg is None:
        return {'ok': False, 'error': '未找到角色配置', 'status_code': 400}

    enabled = bool(enabled)
    if layer_id in ('core', 'vector', 'cognition'):
        return {'ok': False, 'error': '该层属于核心受保护机制，不支持独立关闭', 'status_code': 400}

    try:
        if layer_id == 'experience':
            exp = getattr(chat, 'experience', None)
            if exp is None or not hasattr(exp, 'set_enabled'):
                return {'ok': False, 'error': '自动经历整理模块未就绪或不支持持久化开关', 'status_code': 503}
            exp.set_enabled(enabled)
            if getattr(cfg, 'experience_memory', None) is not None:
                cfg.experience_memory.enabled = enabled
            actual = bool(exp.status().get('enabled'))
            return {'ok': True, 'layer_id': layer_id, 'enabled': actual, 'persisted': True}

        if layer_id == 'judge':
            judge = getattr(getattr(chat, 'memory', None), 'judge', None)
            if judge is not None and hasattr(judge, 'set_enabled'):
                judge.set_enabled(enabled)
                actual = bool(judge.status().get('enabled'))
                return {'ok': True, 'layer_id': layer_id, 'enabled': actual, 'persisted': True}
            persisted, rel = _save_layer_switch(store, cfg, 'judge', enabled)
            if not persisted:
                return {'ok': False, 'error': '想起判官开关无法持久化到磁盘，未修改配置', 'status_code': 503}
            if getattr(cfg, 'recall_judge', None) is not None:
                cfg.recall_judge.enabled = enabled
            actual = bool(_load_layer_switches(store, cfg).get('judge', enabled))
            return {'ok': True, 'layer_id': layer_id, 'enabled': actual, 'persisted': True, 'persistence_path': rel}

        if layer_id == 'short_notes':
            st = getattr(cfg, 'short_term_notes', None)
            if st is None:
                return {'ok': False, 'error': '当前角色未配置短期便签模块', 'status_code': 400}
            persisted, rel = _save_layer_switch(store, cfg, 'short_notes', enabled)
            if not persisted:
                return {'ok': False, 'error': '短期便签开关无法持久化到磁盘，未修改配置', 'status_code': 503}
            st.enabled = enabled
            live_notes = getattr(chat, 'short_term_notes', None)
            if enabled:
                if live_notes is not None and hasattr(live_notes, 'start'):
                    live_notes.start()
            else:
                if live_notes is not None and hasattr(live_notes, 'stop'):
                    live_notes.stop()
            actual = bool(_load_layer_switches(store, cfg).get('short_notes', enabled))
            return {'ok': True, 'layer_id': layer_id, 'enabled': actual, 'persisted': True, 'persistence_path': rel}

        if layer_id in ('association', 'association_contextual', 'association_leisure'):
            mem = getattr(chat, 'memory', None)
            if (mem is not None and hasattr(mem, 'set_association_enabled')
                    and hasattr(mem, 'set_association_contextual_enabled')):
                if layer_id == 'association':
                    status = mem.set_association_enabled(enabled)
                    actual = bool(status['enabled'])
                elif layer_id == 'association_contextual':
                    status = mem.set_association_contextual_enabled(enabled)
                    actual = bool(status['contextual_enabled'])
                else:
                    if hasattr(mem, 'set_leisure_association_enabled'):
                        status = mem.set_leisure_association_enabled(enabled)
                    else:
                        persisted, rel = _save_layer_switch(store, cfg, layer_id, enabled)
                        if not persisted:
                            return {'ok': False, 'error': '自主闲暇联想开关无法持久化到磁盘，未修改配置', 'status_code': 503}
                        actual = bool(_load_layer_switches(store, cfg).get(layer_id, enabled))
                        return {'ok': True, 'layer_id': layer_id, 'enabled': actual, 'persisted': True, 'persistence_path': rel}
                    actual = bool(status.get('leisure_enabled', enabled))
                return {
                    'ok': True,
                    'layer_id': layer_id,
                    'enabled': actual,
                    'leisure_effective': bool(status.get('leisure_effective', False)),
                    'effective_mode': status['effective_mode'],
                    'recall_association': status,
                    'persisted': True,
                    'persistence_path': status.get('persistence_path', ''),
                }
            persisted, rel = _save_layer_switch(store, cfg, layer_id, enabled)
            if not persisted:
                return {'ok': False, 'error': '自然联想开关无法持久化到磁盘，未修改配置', 'status_code': 503}
            actual = bool(_load_layer_switches(store, cfg).get(layer_id, enabled))
            return {'ok': True, 'layer_id': layer_id, 'enabled': actual, 'persisted': True, 'persistence_path': rel}

        return {'ok': False, 'error': f'未知的记忆层: {layer_id}', 'status_code': 404}
    except Exception as exc:
        return {'ok': False, 'error': f'切换失败: {exc}', 'status_code': 500}


MAX_DATE_RANGE_DAYS = 366


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
        except ValueError as exc:
            raise ValueError('日期格式无效，应为 YYYY-MM-DD。') from exc
        start_d, end_d = (d1, d2) if d1 <= d2 else (d2, d1)
        if (end_d - start_d).days + 1 > MAX_DATE_RANGE_DAYS:
            raise ValueError(f'自定义日期范围不能超过 {MAX_DATE_RANGE_DAYS} 天。')
        return start_d, end_d
    return today - timedelta(days=6), today


_INVALID_TX_STATUSES = {'failed', 'error', 'aborted', 'rolled_back', 'pending', 'rejected'}


def _is_valid_journal_status(status):
    st = str(status or '').strip().lower()
    if not st:
        return True
    return st not in _INVALID_TX_STATUSES


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
            'restored': 0,
            'deleted': 0,
            'other_state': 0,
            'events_created': 0,
            'events_updated': 0,
            'net_growth': 0,
            'cumulative': 0,
            'cumulative_created': 0,
            'by_type': {},
        }
        days.append(row)
        day_map[dk] = row
        cur += timedelta(days=1)

    summary = {
        'from_date': d_from.strftime('%Y-%m-%d'),
        'to_date': d_to.strftime('%Y-%m-%d'),
        'total_created': 0,
        'total_active': 0,
        'total_archived': 0,
        'total_deleted': 0,
        'total_events': 0,
        'actual_added': 0,
        'actual_updated': 0,
        'actual_archived': 0,
        'actual_restored': 0,
        'actual_deleted': 0,
        'events_created': 0,
        'events_updated': 0,
        'net_growth': 0,
        'history_incomplete': False,
        'history_incomplete_reason': '',
        'by_type': {},
        'by_bucket': {},
        'last_updated_at': datetime.now().isoformat(timespec='seconds'),
    }

    if db is None or not db.is_file():
        return {'summary': summary, 'days': days}

    conn = _ro_connect(db)
    try:
        conn.execute('BEGIN DEFERRED')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        before_created = 0
        before_active_delta = 0
        journal_mids_with_state = set()

        if 'write_journal' in tables:
            for jr in conn.execute('SELECT time, operation, memory_id, status FROM write_journal'):
                if not _is_valid_journal_status(jr['status']):
                    continue
                jdt = _parse_dt(jr['time'])
                if not jdt:
                    continue
                jd = jdt.date()
                dk = jd.strftime('%Y-%m-%d')
                op = (jr['operation'] or '').strip().lower()
                mid = jr['memory_id'] or ''
                if 'archive' in op and 'unarchive' not in op:
                    if mid:
                        journal_mids_with_state.add(mid)
                    if jd < d_from:
                        before_active_delta -= 1
                    elif dk in day_map:
                        day_map[dk]['archived'] += 1
                        summary['actual_archived'] += 1
                elif op in ('restore', 'unarchive'):
                    if mid:
                        journal_mids_with_state.add(mid)
                    if jd < d_from:
                        before_active_delta += 1
                    elif dk in day_map:
                        day_map[dk]['restored'] += 1
                        summary['actual_restored'] += 1
                elif op in ('delete', 'hard_delete', 'tombstone'):
                    if mid:
                        journal_mids_with_state.add(mid)
                    if jd < d_from:
                        before_active_delta -= 1
                    elif dk in day_map:
                        day_map[dk]['deleted'] += 1
                        summary['actual_deleted'] += 1
                elif op == 'state':
                    if dk in day_map:
                        day_map[dk]['other_state'] += 1

        unlogged_inactive = 0
        if 'memories' in tables:
            for r in conn.execute('SELECT id, created, type, bucket, is_tombstone, is_deleted FROM memories'):
                is_del = bool(r['is_tombstone'] or r['is_deleted'])
                is_arch = (not is_del) and (r['bucket'] == 'archive')
                summary['total_created'] += 1
                if is_del:
                    summary['total_deleted'] += 1
                    if r['id'] not in journal_mids_with_state:
                        unlogged_inactive += 1
                elif is_arch:
                    summary['total_archived'] += 1
                    if r['id'] not in journal_mids_with_state:
                        unlogged_inactive += 1
                else:
                    summary['total_active'] += 1
                    t = r['type'] or 'memory'
                    b = r['bucket'] or 'dynamic'
                    summary['by_type'][t] = summary['by_type'].get(t, 0) + 1
                    summary['by_bucket'][b] = summary['by_bucket'].get(b, 0) + 1

                # Historical creation must not disappear even if later archived or deleted
                cdt = _parse_dt(r['created'])
                if cdt:
                    cd = cdt.date()
                    dk = cd.strftime('%Y-%m-%d')
                    t = r['type'] or 'memory'
                    if cd < d_from:
                        before_created += 1
                    elif dk in day_map:
                        day_map[dk]['added'] += 1
                        day_map[dk]['by_type'][t] = day_map[dk]['by_type'].get(t, 0) + 1
                        summary['actual_added'] += 1

        if unlogged_inactive > 0:
            summary['history_incomplete'] = True
            summary['history_incomplete_reason'] = (
                f'有 {unlogged_inactive} 条历史归档或删除记忆缺少写入账本流水，无法精确重建其状态变更时间'
            )

        if 'versions' in tables:
            for vr in conn.execute('SELECT time, operation_reason FROM versions'):
                vdt = _parse_dt(vr['time'])
                if vdt:
                    dk = vdt.date().strftime('%Y-%m-%d')
                    if dk in day_map:
                        day_map[dk]['updated'] += 1
                        summary['actual_updated'] += 1

        if 'events' in tables:
            for er in conn.execute('SELECT event_id, first_at, payload_json FROM events'):
                summary['total_events'] += 1
                p = _json_load(er['payload_json'], {})
                cdt = _parse_dt(
                    er['first_at'] or p.get('first_saved_at') or p.get('first_at')
                    or p.get('created_at') or p.get('occurred_at') or ''
                )
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

        running_created = before_created
        # Reconstruct active stock from valid ledger deltas; adjust for pre-window unlogged inactive rows
        running_active = before_created + before_active_delta - unlogged_inactive
        for d in days:
            d['net_growth'] = d['added'] + d['restored'] - d['archived'] - d['deleted']
            running_created += d['added']
            running_active += d['net_growth']
            d['cumulative_created'] = max(0, running_created)
            d['cumulative'] = max(0, running_active)
        summary['net_growth'] = (
            summary['actual_added'] + summary['actual_restored']
            - summary['actual_archived'] - summary['actual_deleted']
        )
    finally:
        conn.close()

    return {'summary': summary, 'days': days}


def day_changes(hub, date_str):
    """Return all real memory & event changes for a single date YYYY-MM-DD, separating maintenance logs."""
    db = _brain_db_path(hub)
    target = (date_str or '')[:10]
    changes = []
    maintenance_logs = []
    if not target or db is None or not db.is_file():
        return {'date': target, 'changes': changes, 'maintenance_logs': maintenance_logs}

    conn = _ro_connect(db)
    try:
        conn.execute('BEGIN DEFERRED')
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        mem_current = {}
        if 'memories' in tables:
            m_cols = {c[1] for c in conn.execute('PRAGMA table_info(memories)')}
            if 'body' in m_cols:
                m_all_sql = 'SELECT id, title, type, bucket, created, body AS content FROM memories'
                m_day_sql = 'SELECT id, title, type, bucket, created, body AS content FROM memories WHERE substr(created, 1, 10) = ?'
            else:
                m_all_sql = 'SELECT id, title, type, bucket, created, content AS content FROM memories'
                m_day_sql = 'SELECT id, title, type, bucket, created, content AS content FROM memories WHERE substr(created, 1, 10) = ?'

            for ar in conn.execute(m_all_sql):
                mem_current[ar['id']] = {
                    'title': ar['title'] or ar['id'],
                    'content': ar['content'] or '',
                }
            for mr in conn.execute(m_day_sql, (target,)):
                changes.append({
                    'kind': 'added',
                    'kind_label': '新增记忆',
                    'time': mr['created'] or target,
                    'memory_id': mr['id'],
                    'title': mr['title'] or mr['id'],
                    'type': mr['type'] or 'memory',
                    'bucket': mr['bucket'] or 'dynamic',
                    'source': '日常对话',
                    'summary': (mr['content'] or '')[:160],
                    'content': mr['content'] or '',
                })
        if 'versions' in tables:
            v_cols = {c[1] for c in conn.execute('PRAGMA table_info(versions)')}
            if 'version_id' in v_cols and 'content' in v_cols:
                v_day_sql = 'SELECT version_id AS version_id, memory_id, time, operation_reason, content AS content FROM versions WHERE substr(time, 1, 10) = ?'
            elif 'version_id' in v_cols:
                v_day_sql = 'SELECT version_id AS version_id, memory_id, time, operation_reason, old_content AS content FROM versions WHERE substr(time, 1, 10) = ?'
            elif 'content' in v_cols:
                v_day_sql = 'SELECT seq AS version_id, memory_id, time, operation_reason, content AS content FROM versions WHERE substr(time, 1, 10) = ?'
            else:
                v_day_sql = 'SELECT seq AS version_id, memory_id, time, operation_reason, old_content AS content FROM versions WHERE substr(time, 1, 10) = ?'

            for vr in conn.execute(v_day_sql, (target,)):
                mid = vr['memory_id']
                curr_info = mem_current.get(mid, {})
                old_txt = vr['content'] or ''
                new_txt = curr_info.get('content') or ''
                diff_lines = compute_line_diff(old_txt, new_txt) if (old_txt or new_txt) else []
                changes.append({
                    'kind': 'updated',
                    'kind_label': '修订记忆',
                    'time': vr['time'] or target,
                    'memory_id': mid,
                    'version_id': vr['version_id'],
                    'title': f"版本修订 · {curr_info.get('title') or mid}",
                    'source': '版本修订',
                    'summary': vr['operation_reason'] or '内容修订',
                    'old_content': old_txt,
                    'content': new_txt,
                    'diff': diff_lines,
                    'reason': vr['operation_reason'] or '',
                })
        if 'write_journal' in tables:
            for jr in conn.execute('SELECT seq, time, operation, memory_id, detail_json, status FROM write_journal WHERE substr(time, 1, 10) = ?', (target,)):
                op = (jr['operation'] or '').lower()
                detail = _json_load(jr['detail_json'], {})
                if op in ('maintenance', 'dream', 'noop', 'heartbeat', 'index_sync') or not _is_valid_journal_status(jr['status']):
                    maintenance_logs.append({
                        'time': jr['time'] or target,
                        'operation': jr['operation'],
                        'status': jr['status'],
                        'detail': detail.get('reason') or jr['operation'],
                    })
                elif 'archive' in op or op in ('restore', 'unarchive', 'delete', 'hard_delete', 'tombstone'):
                    k_label = '恢复活跃' if op in ('restore', 'unarchive') else ('归档' if 'archive' in op else '删除')
                    changes.append({
                        'kind': op,
                        'kind_label': k_label,
                        'time': jr['time'] or target,
                        'memory_id': jr['memory_id'] or '',
                        'title': detail.get('title') or jr['memory_id'] or jr['operation'],
                        'source': detail.get('source') or '写入账本',
                        'summary': detail.get('reason') or f"状态变更 ({k_label})",
                        'reason': detail.get('reason') or '',
                    })
        if 'events' in tables:
            for er in conn.execute('SELECT event_id, title, first_at, payload_json FROM events'):
                p = _json_load(er['payload_json'], {})
                cat = str(
                    er['first_at'] or p.get('first_saved_at') or p.get('first_at')
                    or p.get('created_at') or p.get('occurred_at') or ''
                )
                if cat.startswith(target):
                    changes.append({
                        'kind': 'event_created',
                        'kind_label': '新建事件',
                        'time': cat,
                        'event_id': er['event_id'],
                        'title': er['title'] or er['event_id'],
                        'source': '共同经历',
                        'summary': p.get('summary') or '新建事件记录',
                        'content': p.get('story') or p.get('summary') or '',
                        'reason': '事件创建',
                    })
        if 'event_ledger' in tables:
            for lr in conn.execute("SELECT at, kind, event_id, stage_id, payload_json FROM event_ledger WHERE substr(at, 1, 10) = ? AND kind IN ('event-op', 'event-update')", (target,)):
                p = _json_load(lr['payload_json'], {})
                changes.append({
                    'kind': 'event_updated',
                    'kind_label': '事件更新',
                    'time': lr['at'] or target,
                    'event_id': lr['event_id'] or '',
                    'stage_id': lr['stage_id'] or '',
                    'title': p.get('title') or lr['event_id'] or '共同经历更新',
                    'source': '事件账本',
                    'summary': p.get('summary') or p.get('note') or '事件阶段或故事更新',
                    'content': p.get('story') or p.get('summary') or '',
                    'reason': p.get('action') or lr['kind'],
                })
        changes.sort(key=lambda x: x.get('time', ''), reverse=True)
    finally:
        conn.close()

    return {'date': target, 'changes': changes, 'maintenance_logs': maintenance_logs}


def generate_growth_report(hub, from_date='', to_date='', include_content=True, include_health=False, fmt='full'):
    """Deterministic local Markdown report for memory growth & changes (no paid LLM calls, zero silent truncation in full mode)."""
    range_key = 'custom' if (from_date and to_date) else '7d'
    data = growth_analysis(hub, range_key=range_key, from_date=from_date, to_date=to_date)
    s = data['summary']
    lines = [
        '# VEIL Brain · 记忆增长与变更报告',
        f"- 统计范围：{s['from_date']} 至 {s['to_date']}",
        f"- 生成时间：{s['last_updated_at']}",
        f"- 报告模式：{'完整开发诊断报告（无静默截断）' if fmt != 'summary' else '摘要报告'}",
        '- 生成方式：本地确定性统计（未调用付费模型，已排除凭据与敏感配置）',
        '',
        '## 1. 存量与增长统计',
        f"- 累计创建记忆：{s['total_created']} 条（当前活跃 {s['total_active']} 条，已归档 {s['total_archived']} 条，已删除 {s['total_deleted']} 条，共同经历 {s['total_events']} 个）",
        f"- 所选范围实际新增：{s['actual_added']} 条",
        f"- 所选范围修订次数：{s['actual_updated']} 次",
        f"- 所选范围归档 / 恢复 / 删除：{s['actual_archived']} / {s['actual_restored']} / {s['actual_deleted']} 条",
        f"- 所选范围活跃净增长：{s['net_growth']:+d} 条",
        f"- 事件故事：新建 {s['events_created']} 个 · 延续更新 {s['events_updated']} 次",
    ]
    if s.get('history_incomplete'):
        lines.append(f"- 历史完整性提示：{s['history_incomplete_reason']}")

    if fmt != 'summary':
        lines.extend(['', '## 2. 逐日变更明细'])
        for d in data['days']:
            if d['added'] or d['updated'] or d['archived'] or d['restored'] or d['deleted'] or d['events_created'] or d['events_updated']:
                lines.append(
                    f"- {d['date']}：新增 {d['added']} / 修订 {d['updated']} / 归档 {d['archived']} / 恢复 {d['restored']} / 删除 {d['deleted']} / "
                    f"事件新建 {d['events_created']} / 事件更新 {d['events_updated']}（净增长 {d['net_growth']:+d}）"
                )
        lines.extend(['', '## 3. 记忆与事件变更详情及技术证据'])
        has_detail = False
        for d in data['days']:
            if not (d['added'] or d['updated'] or d['archived'] or d['restored'] or d['deleted'] or d['events_created'] or d['events_updated']):
                continue
            dc = day_changes(hub, d['date'])
            for c in dc['changes']:
                has_detail = True
                lines.append(f"### [{c['kind_label']}] {c.get('title') or c.get('memory_id') or c.get('event_id')} ({c.get('time')})")
                if c.get('memory_id'):
                    lines.append(f"- 记忆 ID: `{c['memory_id']}`")
                if c.get('event_id'):
                    lines.append(f"- 事件 ID: `{c['event_id']}`" + (f" · 阶段 ID: `{c['stage_id']}`" if c.get('stage_id') else ''))
                lines.append(f"- 来源证据: {c.get('source') or '账本未记录'}")
                lines.append(f"- 写入/变更原因: {c['reason'] if c.get('reason') else '账本未记录具体原因'}")
                if c.get('diff'):
                    lines.append(f"- 版本差异（{len(c['diff'])} 行）:")
                    if include_content:
                        lines.append('```diff\n' + '\n'.join(c['diff']) + '\n```')
                if include_content:
                    if c.get('old_content'):
                        lines.append(f"- 修改前内容:\n```text\n{c['old_content']}\n```")
                    if c.get('content'):
                        lines.append(f"- 正文内容:\n```text\n{c['content']}\n```")
        if not has_detail:
            lines.append('- 所选时间范围内无具体记忆或事件变更记录。')

    if include_health:
        chat = getattr(hub, 'chat', None)
        cfg = getattr(chat, 'cfg', None)
        store = getattr(chat, 'store', None)
        data_root = getattr(hub, 'data_root', None) or (getattr(store, 'root', None) if store is not None else None)
        latest = load_latest_report(data_root, cfg.id) if (data_root and cfg) else None
        lines.extend(['', '## 4. 关联健康巡检摘要'])
        if latest:
            l_from = latest.get('from_date') or ''
            l_to = latest.get('to_date') or ''
            range_matched = (l_from == s['from_date'] and l_to == s['to_date'])
            hs = latest.get('summary') or {}
            warn_cnt = int(hs.get('warn_count') or 0)
            err_cnt = int(hs.get('error_count') or 0)
            incomplete = bool(latest.get('incomplete') or hs.get('incomplete'))
            if range_matched:
                lines.append(f"- 日期范围匹配：{l_from} 至 {l_to}")
            else:
                lines.append(
                    f"- 日期范围不匹配：所选范围为 {s['from_date']} 至 {s['to_date']}，"
                    f"最近一次巡检覆盖范围为 {l_from or '全部'} 至 {l_to or '最新'}（不能替代所选范围巡检）"
                )
            lines.append(
                f"- 巡检状态：{hs.get('status_label') or hs.get('status', 'unknown')} · "
                f"错误 {err_cnt} / 关注 {warn_cnt} · 巡检时间：{latest.get('scanned_at', '未知')}"
            )
            if incomplete:
                lines.append('- 缺失检查提示：该次巡检因超时未完成全部检查项，未执行项不等同通过。')
            period = latest.get('period') or {}
            if period.get('requested'):
                if period.get('status') in ('incomplete', 'unavailable'):
                    lines.append('- 指定期间检查未完成，计数未统计，不能把缺失当成 0。')
                else:
                    growth = period.get('growth') or {}
                    lines.append(
                        f"- 指定期间（{latest.get('from_date') or '最早'} 至 {latest.get('to_date') or '最新'}）："
                        f"新增记忆 {growth.get('memories_created', 0)} 条 / 事件 {growth.get('events_created', 0)} 个 / "
                        f"重复 {period.get('duplicates', {}).get('count', 0)} 组 / 碎片 {period.get('fragments', {}).get('count', 0)} 条 / "
                        f"悬空引用 {period.get('dangling', {}).get('count', 0)} 处 / 索引异常 {period.get('index_anomalies', {}).get('count', 0)} 条")
                hist = period.get('historical_state') or {}
                if period.get('status') in ('incomplete', 'unavailable'):
                    lines.append('- 期间历史证据：本轮未完成，不判断有没有快照。')
                elif hist.get('available') and hist.get('delta_known') is False:
                    lines.append('- 期间历史证据：完整快照不足两条，变化未知，不写成 0。')
                else:
                    lines.append(
                        '- 期间历史证据：' + ('有巡检快照可对照'
                                            if hist.get('available') else
                                            '缺少该时间段的历史快照；只报告有库内时间字段支持的期间变化'))
        else:
            lines.append('- 暂无健康巡检报告（尚未执行过只读巡检）。')

    return {
        'ok': True,
        'markdown': '\n'.join(lines),
        'summary': s,
    }


def _read_all_jsonl(path, max_rows=5000):
    if not path or not Path(path).is_file():
        return []
    rows = []
    try:
        with Path(path).open(encoding='utf-8', errors='replace') as fh:
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
    if max_rows and len(rows) > max_rows:
        return rows[-max_rows:]
    return rows


def _build_turn_record(row, ctx_by_rid, recall_by_rid, costs_by_rid):
    rid = str(row.get('request_id') or row.get('id') or '')
    ts = str(row.get('time') or row.get('created_at') or '')
    mdl = str(row.get('model') or '')
    purp = str(row.get('purpose') or row.get('kind') or 'chat')

    ctx_rows = ctx_by_rid.get(rid) or []
    prov = ctx_rows[-1] if ctx_rows else {}
    row_usage = row.get('usage') if isinstance(row.get('usage'), dict) else {}

    # Sum settled costs from costs.jsonl (uses 'amount' for type=='settle', or 'cost_cny')
    c_rows = costs_by_rid.get(rid) or []
    settled_sum = 0.0
    has_cost = False
    for c in c_rows:
        ctype = str(c.get('type') or '').lower()
        if ctype == 'settle' and c.get('amount') is not None:
            settled_sum += float(c['amount'])
            has_cost = True
        elif c.get('cost_cny') is not None:
            settled_sum += float(c['cost_cny'])
            has_cost = True
    if not has_cost and row.get('cost_cny') is not None:
        settled_sum = float(row['cost_cny'])
        has_cost = True

    est_tokens = row.get('estimated_tokens')
    if est_tokens is None:
        est_tokens = prov.get('estimated_input_tokens')
    if est_tokens is None:
        est_tokens = row_usage.get('estimated_input_tokens')

    act_in = prov.get('actual_input_tokens') if prov.get('actual_input_tokens') is not None else row_usage.get('actual_input_tokens')
    act_out = prov.get('completion_tokens') if prov.get('completion_tokens') is not None else row_usage.get('completion_tokens')

    cache_hit = prov.get('cached_tokens')
    if cache_hit is None:
        cache_hit = prov.get('cache_read_input_tokens')
    if cache_hit is None:
        cache_hit = row_usage.get('cached_tokens')
    if cache_hit is None:
        cache_hit = row_usage.get('cache_read_input_tokens')

    # Recall evidence: prefer explicit recall dict on row or recall-diagnostics.jsonl
    raw_recall = row.get('recall') if isinstance(row.get('recall'), dict) else None
    diag_recall = recall_by_rid.get(rid)
    recall_info = raw_recall or diag_recall
    has_recall_evidence = isinstance(recall_info, dict) and len(recall_info) > 0
    r_dict = recall_info if isinstance(recall_info, dict) else {}

    def _stage_info(key, label):
        if not has_recall_evidence:
            return {'key': key, 'label': label, 'count': None, 'state': 'missing', 'detail': None}
        val = r_dict.get(key)
        if val is None:
            return {'key': key, 'label': label, 'count': None, 'state': 'not_run', 'detail': None}
        if isinstance(val, dict) and str(val.get('reason') or '').startswith('skipped'):
            return {'key': key, 'label': label, 'count': 0, 'state': 'not_run', 'detail': val}
        if isinstance(val, list):
            cnt = len(val)
        elif isinstance(val, dict):
            if isinstance(val.get('items'), list):
                cnt = len(val['items'])
            elif isinstance(val.get('picked'), list):
                cnt = len(val['picked'])
            else:
                cnt = int(val.get('count') or 0)
        elif isinstance(val, (int, float)):
            cnt = int(val)
        else:
            cnt = 1 if val else 0
        return {'key': key, 'label': label, 'count': cnt, 'state': 'hit' if cnt > 0 else 'not_hit', 'detail': val}

    recall_chain = [
        _stage_info('relationship_base', '关系底座'),
        _stage_info('opening', '开场记忆'),
        _stage_info('relevant_recall', '相关召回'),
        _stage_info('natural_association', '自然联想'),
        _stage_info('carried', '延续记忆'),
        _stage_info('vector', '向量召回'),
    ]

    candidates = r_dict.get('candidates') or row.get('recall_candidates') or []
    passed_candidates = r_dict.get('passed_candidates') or r_dict.get('picked') or []
    injected = r_dict.get('injected') or r_dict.get('items') or r_dict.get('memories') or []
    injected_ids = {str(it.get('memory_id') or it.get('id') or '') for it in injected if isinstance(it, dict)}
    dropped_candidates = r_dict.get('dropped') or [
        c for c in candidates
        if isinstance(c, dict) and str(c.get('memory_id') or c.get('id') or '') not in injected_ids
    ]

    mem_tokens = r_dict.get('memory_tokens')
    has_actual_hit = bool(injected) or any((st['count'] or 0) > 0 for st in recall_chain)

    prompt_sections = row.get('prompt_sections') or row.get('sections') or []
    prompt_sections_source = row.get('prompt_sections_source') or ('snapshot_estimate' if prompt_sections else 'missing')

    cache_hit_rate = None
    if isinstance(cache_hit, (int, float)) and isinstance(act_in, (int, float)) and act_in > 0:
        cache_hit_rate = round((cache_hit / act_in) * 100, 1)

    return {
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
        'cost_cny': round(settled_sum, 6) if has_cost else None,
        'recall': r_dict,
        'leisure_association': (r_dict.get('leisure_association')
                                if isinstance(r_dict.get('leisure_association'), dict) else None),
        'recall_evidence_state': 'present' if has_recall_evidence else 'missing',
        'has_actual_hit': has_actual_hit,
        'recall_chain': recall_chain,
        'candidates': candidates,
        'passed_candidates': passed_candidates,
        'injected': injected,
        'dropped_candidates': dropped_candidates,
        'prompt_sections': prompt_sections,
        'prompt_sections_source': prompt_sections_source,
        'raw_available': bool(row),
    }


def _load_recall_indexes(data_root, role_id):
    from veil.system import diagnostics as diag
    role_dir = Path(data_root) / role_id
    snap_path = diag.snapshot_path(data_root, role_id)
    raw_snaps = _read_all_jsonl(snap_path, max_rows=5000)

    # Also read per-request JSON files under request-snapshots/ (actual runtime storage format).
    snap_dir = role_dir / 'request-snapshots'
    if snap_dir.is_dir():
        try:
            snap_entries = []
            for p in snap_dir.glob('*.json'):
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    continue
                snap_entries.append((mtime, p))
            snap_entries.sort(key=lambda item: item[0], reverse=True)
            for _, sp in reversed(snap_entries[:1000]):
                try:
                    rec = json.loads(sp.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    continue
                if isinstance(rec, dict):
                    if not rec.get('request_id'):
                        rec['request_id'] = sp.name.split('.', 1)[0]
                    raw_snaps.append(rec)
        except OSError:
            pass

    if not raw_snaps:
        raw_snaps = diag.snapshots(data_root, role_id, '', limit=5000) or []

    # Deduplicate multi-step snapshots by request_id (keep latest step per request_id)
    dedup_map = {}
    ordered_snaps = []
    for row in raw_snaps:
        if not isinstance(row, dict):
            continue
        rid = str(row.get('request_id') or row.get('id') or '')
        if rid:
            dedup_map[rid] = row
        else:
            ordered_snaps.append(row)

    ctx_by_rid = {}
    for cr in _read_all_jsonl(role_dir / 'context-log.jsonl', max_rows=5000):
        rid = str(cr.get('request_id') or '')
        if rid:
            ctx_by_rid.setdefault(rid, []).append(cr)

    recall_by_rid = {}
    for rr in _read_all_jsonl(role_dir / 'recall-diagnostics.jsonl', max_rows=5000):
        rid = str(rr.get('request_id') or '')
        if rid:
            recall_by_rid[rid] = rr

    costs_by_rid = {}
    for cor in _read_all_jsonl(Path(data_root) / 'costs.jsonl', max_rows=5000):
        rid = str(cor.get('id') or cor.get('request_id') or '')
        if rid:
            costs_by_rid.setdefault(rid, []).append(cor)

    # Synthesize base turn rows for historical context-log / recall-diagnostics turns
    # that do not have a request-snapshot file on disk. Skip turns where context-log
    # explicitly records sent=False (never actually sent to the model).
    unsent_rids = set()
    for rid, ctx_list in ctx_by_rid.items():
        sent_rows = [c for c in ctx_list if c.get('sent') is not False]
        if not sent_rows:
            unsent_rids.add(rid)
            continue
        if rid not in dedup_map:
            latest = sent_rows[-1]
            dedup_map[rid] = {
                'request_id': rid,
                'time': latest.get('time') or '',
                'model': latest.get('model') or '',
                'purpose': latest.get('kind') or 'chat',
                'step': latest.get('step'),
            }
    for rid, rr in recall_by_rid.items():
        if rid not in dedup_map and rid not in unsent_rids:
            dedup_map[rid] = {
                'request_id': rid,
                'time': rr.get('time') or '',
                'model': rr.get('model') or '',
                'purpose': rr.get('purpose') or 'chat',
            }

    ordered_snaps.extend(dedup_map.values())
    return ordered_snaps, ctx_by_rid, recall_by_rid, costs_by_rid


def recall_turns(hub, page=1, limit=20, date='', purpose='', model='', status=''):
    """Paginated per-turn recall, injection, token & cost observability + historical trends (single-pass indexed)."""
    chat = getattr(hub, 'chat', None)
    cfg = getattr(chat, 'cfg', None)
    store = getattr(chat, 'store', None)
    data_root = getattr(hub, 'data_root', None) or (getattr(store, 'root', None) if store is not None else None)
    if data_root is None or cfg is None:
        return {'items': [], 'total': 0, 'page': 1, 'limit': limit, 'trends': [], 'coverage_note': '无可用数据目录'}

    ordered_snaps, ctx_by_rid, recall_by_rid, costs_by_rid = _load_recall_indexes(data_root, cfg.id)

    # Filter & build trends in a single pass without O(N) trace calls
    filtered_rows = []
    for row in ordered_snaps:
        ts = str(row.get('time') or row.get('created_at') or '')
        mdl = str(row.get('model') or '')
        purp = str(row.get('purpose') or row.get('kind') or 'chat')
        if date and not ts.startswith(date):
            continue
        if purpose and purpose != purp:
            continue
        if model and model.lower() not in mdl.lower():
            continue
        filtered_rows.append(row)

    turns = []
    trend_days = {}
    for row in filtered_rows:
        turn = _build_turn_record(row, ctx_by_rid, recall_by_rid, costs_by_rid)
        if status == 'hit' and not turn['has_actual_hit']:
            continue
        if status == 'miss' and turn['has_actual_hit']:
            continue
        turns.append(turn)

        dk = (turn['time'] or '')[:10]
        if dk:
            td = trend_days.setdefault(dk, {'date': dk, 'turns': 0, 'base_hits': 0, 'assoc_hits': 0, 'mem_tokens_sum': 0})
            td['turns'] += 1
            for st in turn['recall_chain']:
                if st['key'] == 'relationship_base' and (st['count'] or 0) > 0:
                    td['base_hits'] += 1
                if st['key'] == 'natural_association' and (st['count'] or 0) > 0:
                    td['assoc_hits'] += 1
            mt = turn['tokens'].get('memory_tokens')
            if isinstance(mt, (int, float)):
                td['mem_tokens_sum'] += mt

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
        'coverage_note': f'已索引最近 {len(ordered_snaps)} 条去重请求记录（支持全量分页）',
        'trends': sorted(trend_days.values(), key=lambda x: x['date']),
    }


def recall_turn_report(hub, request_id):
    """Deterministic single-turn recall & token/cost diagnostic report (direct request_id lookup, not limited by pagination)."""
    chat = getattr(hub, 'chat', None)
    cfg = getattr(chat, 'cfg', None)
    store = getattr(chat, 'store', None)
    data_root = getattr(hub, 'data_root', None) or (getattr(store, 'root', None) if store is not None else None)
    if data_root is None or cfg is None or not request_id:
        return {'ok': False, 'error': '未找到对应的轮次记录'}

    ordered_snaps, ctx_by_rid, recall_by_rid, costs_by_rid = _load_recall_indexes(data_root, cfg.id)
    raw_match = next((r for r in ordered_snaps if str(r.get('request_id') or r.get('id') or '') == str(request_id)), None)
    if raw_match is None:
        return {'ok': False, 'error': '未找到对应的轮次记录'}

    match = _build_turn_record(raw_match, ctx_by_rid, recall_by_rid, costs_by_rid)
    tk = match['tokens']
    cache_str = (
        f"{tk['cache_hit']}" + (f"（命中率 {tk['cache_hit_rate']}%）" if tk.get('cache_hit_rate') is not None else '')
        if tk['cache_hit'] is not None else '未记录（上游未返回缓存计量）'
    )
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
        f"- 缓存命中 Token：{cache_str}",
        f"- 记忆注入 Token：{tk['memory_tokens'] if tk['memory_tokens'] is not None else '未记录'}",
        f"- 结算费用：{('¥' + str(match['cost_cny'])) if match['cost_cny'] is not None else '暂无结算记录'}",
    ]

    p_secs = match.get('prompt_sections') or []
    src_label = '发送前快照估算' if match.get('prompt_sections_source') == 'snapshot_estimate' else '上游计量'
    if p_secs:
        lines.extend(['', f'## 2. Prompt 分区 Token 分布（{src_label}）'])
        for sec in p_secs:
            if isinstance(sec, dict):
                lines.append(f"- {sec.get('label') or sec.get('name')}: {sec.get('tokens', 0)} tokens")

    chain = match.get('recall_chain') or []
    if chain:
        lines.extend(['', '## 3. 六层召回链路'])
        if match.get('recall_evidence_state') == 'missing':
            lines.append('- 召回追踪证据缺失（该轮未记录召回链路快照）')
        else:
            for st in chain:
                if st['state'] == 'missing':
                    st_txt = '证据缺失'
                elif st['state'] == 'not_run':
                    st_txt = '未执行'
                elif st['state'] == 'hit':
                    st_txt = f"命中 {st['count']} 项"
                else:
                    st_txt = '未命中'
                lines.append(f"- {st['label']}: {st_txt}")

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
            f"{x.get('title') or x.get('memory_id') or ''}({x.get('drop_reason') or '未记录未入选原因'})"
            for x in dropped if isinstance(x, dict)
        ))

    return {'ok': True, 'markdown': '\n'.join(lines), 'turn': match}
