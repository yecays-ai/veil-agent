"""P5: Strictly read-only Brain health inspection, periodic scheduling, and sanitized reporting.

Never deletes, merges, archives, or mutates memories, events, versions, or tombstones in brain.db.
Opens brain.db strictly with ?mode=ro URI and enforces bounded sampling and execution time.
"""
from __future__ import annotations

import array
import hashlib
import json
import math
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_HEALTH_SETTINGS = {
    'enabled': True,
    'schedule': 'daily_night',  # daily_night | every_6h | manual_only
    'idle_only': True,
}
VALID_SCHEDULES = ('daily_night', 'every_6h', 'manual_only')
_SCAN_LOCK = threading.Lock()
_ACTIVE_SCAN = False
_SETTINGS_LOCK = threading.Lock()
ALL_CHECK_IDS = ('H-1', 'H-2', 'H-3', 'H-4', 'H-5', 'H-6', 'H-7', 'H-8', 'H-9')
# The health scan must not assume one embedding size: the model identity and the dimensions the
# database actually stores are the source of truth (same rule as P6-S1 G-A14 in recall).
VECTOR_MODEL_META = 'embedding_model'
# Sanitized report sample limits; totals are always counted separately and never implied by a sample.
ORPHAN_SAMPLE_LIMIT = 15
PERIOD_EVIDENCE_LIMIT = 30
# Ticket 456 review: the daily-night slot follows the same 01:00-06:00 window the repository
# already uses for autonomy quiet hours, unless the role configures its own.
DEFAULT_NIGHT_WINDOW = ((1, 0), (6, 0))
# H-5 samples the newest-event scan; totals always come from COUNT(*) so a bound never lies.
EVENT_ROW_LIMIT = 20000
STAGE_EVIDENCE_LIMIT = 20000


class _ScanTimeout(Exception):
    """Raised internally when the whole scan has used up its time budget."""


def _now_iso():
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def _today_str():
    return time.strftime('%Y-%m-%d')


def _ro_connect(db_path):
    # Path.as_uri() percent-encodes '#', '?' and '%'; a raw path would otherwise make SQLite
    # open a different (or read-write) file than the one the caller asked for.
    uri = Path(db_path).resolve().as_uri()
    return sqlite3.connect(f'{uri}?mode=ro', uri=True, timeout=5.0)


def _json_load(text, default):
    try:
        val = json.loads(text) if isinstance(text, str) else text
        return val if val is not None else default
    except (ValueError, TypeError):
        return default


def _deadline_reached(deadline):
    return time.monotonic() >= deadline


def _guard(deadline):
    if _deadline_reached(deadline):
        raise _ScanTimeout


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


def _in_range(value, dt_from, dt_to):
    if dt_from is None and dt_to is None:
        return True
    parsed = _parse_dt(value)
    if parsed is None:
        return False
    if dt_from is not None and parsed < dt_from:
        return False
    if dt_to is not None and parsed >= dt_to:
        return False
    return True


def _range_bounds(from_date='', to_date=''):
    dt_from = None
    dt_to = None
    if isinstance(from_date, str) and from_date.strip():
        dt_from = datetime.strptime(from_date.strip()[:10], '%Y-%m-%d')
    if isinstance(to_date, str) and to_date.strip():
        dt_to = datetime.strptime(to_date.strip()[:10], '%Y-%m-%d') + timedelta(days=1)
    return dt_from, dt_to


def _norm_text(text):
    return ''.join(ch for ch in (text or '').casefold() if ch.isalnum())


def _safe_title(title, memory_id):
    text = (title or '').strip()
    if not text:
        return str(memory_id)
    return text[:40] + ('…' if len(text) > 40 else '')


def _read_jsonl(path, limit=2000):
    rows, _truncated = _read_jsonl_scan(path, limit=limit)
    return rows


def _read_jsonl_scan(path, limit=None, predicate=None):
    """Read a jsonl file in order.

    ``limit`` keeps only the newest rows and reports truncation. ``predicate`` scans the whole
    file and keeps every match, so an old date range is not dropped just because newer rows
    filled a tail window.
    """
    p = Path(path)
    if not p.is_file():
        return [], False
    rows = []
    truncated = False
    try:
        with p.open(encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
    except OSError:
        return [], True
    if predicate is not None:
        return [row for row in rows if predicate(row)], False
    if limit and len(rows) > limit:
        return rows[-limit:], True
    return rows, truncated


def _role_dir(data_root, role_id):
    return Path(data_root) / str(role_id)


def health_reports_dir(root, role_id):
    return _role_dir(root, role_id) / 'health-reports'


def load_health_settings(root, role_id):
    path = _role_dir(root, role_id) / 'health-settings.json'
    if not path.is_file():
        return dict(DEFAULT_HEALTH_SETTINGS)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            return dict(DEFAULT_HEALTH_SETTINGS)
    except (OSError, ValueError):
        return dict(DEFAULT_HEALTH_SETTINGS)
    schedule = data.get('schedule') if data.get('schedule') in VALID_SCHEDULES else DEFAULT_HEALTH_SETTINGS['schedule']
    return {
        'enabled': bool(data.get('enabled', DEFAULT_HEALTH_SETTINGS['enabled'])),
        'schedule': schedule,
        'idle_only': bool(data.get('idle_only', DEFAULT_HEALTH_SETTINGS['idle_only'])),
    }


def save_health_settings(root, role_id, payload):
    if not isinstance(payload, dict):
        raise ValueError('无效的健康检查配置。')
    # Read, merge and write in one critical section: two saves must not restore each other's fields.
    with _SETTINGS_LOCK:
        current = load_health_settings(root, role_id)
        if 'enabled' in payload:
            if type(payload['enabled']) is not bool:
                raise ValueError('enabled 必须是布尔值。')
            current['enabled'] = payload['enabled']
        if 'schedule' in payload:
            if payload['schedule'] not in VALID_SCHEDULES:
                raise ValueError('无效的检查周期。')
            current['schedule'] = payload['schedule']
        if 'idle_only' in payload:
            if type(payload['idle_only']) is not bool:
                raise ValueError('idle_only 必须是布尔值。')
            current['idle_only'] = payload['idle_only']
        rdir = _role_dir(root, role_id)
        rdir.mkdir(parents=True, exist_ok=True)
        target = rdir / 'health-settings.json'
        tmp = rdir / '.health-settings.tmp'
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(target)
        return current


def load_latest_report(root, role_id, from_date='', to_date=''):
    # The endpoint can only serve the last report, so a requested range never relabels it.
    if from_date or to_date:
        _range_bounds(from_date, to_date)  # malformed dates raise instead of being echoed back
    rdir = health_reports_dir(root, role_id)
    latest = rdir / 'latest.json'
    if not latest.is_file():
        return None
    try:
        report = json.loads(latest.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return report if isinstance(report, dict) else None


def load_snapshots(root, role_id, limit=30):
    snap_path = health_reports_dir(root, role_id) / 'health-snapshots.jsonl'
    rows = _read_jsonl(snap_path, limit=limit)
    return rows


def _save_report(root, role_id, report):
    """Persist the snapshot and the latest report; either failure means the scan did not finish.

    The snapshot is appended first: the scheduler decides when to scan again from the persisted
    ``scanned_at``, so a half-saved report must never look complete to the next poll.
    """
    rdir = health_reports_dir(root, role_id)
    rdir.mkdir(parents=True, exist_ok=True)
    snap = {
        'day': _today_str(),
        'scanned_at': report.get('scanned_at'),
        'active_memories': report.get('stats', {}).get('active_memories', 0),
        'events_total': report.get('stats', {}).get('events_total', 0),
        'warn_count': report.get('summary', {}).get('warn_count', 0),
        'error_count': report.get('summary', {}).get('error_count', 0),
        'incomplete': bool(report.get('incomplete')),
    }
    snap_path = rdir / 'health-snapshots.jsonl'
    with snap_path.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(snap, ensure_ascii=False) + '\n')
        fh.flush()
        os.fsync(fh.fileno())

    latest = rdir / 'latest.json'
    tmp = rdir / '.latest.tmp'
    with tmp.open('w', encoding='utf-8') as fh:
        fh.write(json.dumps(report, ensure_ascii=False, indent=2))
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(latest)


def _cosine(a, b):
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


def run_health_scan(root, role_id, db_path, *, from_date='', to_date='',
                    experience_status=None, judge_status=None,
                    learned_status=None, timeout_s=5.0, vector_sample_limit=300,
                    configured_embedding_model='', persist=True):
    """Execute deterministic, strictly read-only P5 inspection against brain.db and logs."""
    global _ACTIVE_SCAN
    with _SCAN_LOCK:
        if _ACTIVE_SCAN:
            raise RuntimeError('health_scan_in_progress')
        _ACTIVE_SCAN = True

    started = time.monotonic()
    deadline = started + max(1.0, float(timeout_s))
    try:
        dt_from, dt_to = _range_bounds(from_date, to_date)
        range_requested = bool(dt_from or dt_to)
        db = Path(db_path)
        checks = []
        timed_out = False
        # Defined before the guarded block so a timeout before loading can still build a report.
        event_total = 0
        event_rows = []
        events_sampled = False
        period = {
            'requested': range_requested,
            'from_date': from_date,
            'to_date': to_date,
            'status': 'unavailable' if range_requested else 'not_requested',
            'growth': {},
            'duplicates': {'count': 0, 'evidences': []},
            'fragments': {'count': 0, 'evidences': []},
            'dangling': {'count': 0, 'evidences': []},
            'index_anomalies': {'count': 0, 'evidences': []},
            'coverage': {},
            'historical_state': {'available': False, 'reason': 'not_requested'},
        }
        stats = {
            'active_memories': 0,
            'pinned_memories': 0,
            'archived_memories': 0,
            'tombstones': 0,
            'events_total': 0,
            'event_stages_total': 0,
            'versions_total': 0,
            'vectors_total': 0,
            'embedding_model': str(configured_embedding_model or ''),
            'vector_dimensions': {},
        }

        if not db.is_file():
            report = {
                'schema_version': 2,
                'scanned_at': _now_iso(),
                'duration_ms': 0,
                'read_only': True,
                'from_date': from_date,
                'to_date': to_date,
                'scope': {'current_state': True, 'requested_range': range_requested,
                          'from_date': from_date, 'to_date': to_date,
                          'label': '全库当前 + 指定期间' if range_requested else '全库当前'},
                'period': period,
                'stats': stats,
                'summary': {'status': 'attention', 'status_label': '尚未生成 brain.db', 'total_checks': 1, 'good_count': 0, 'warn_count': 1, 'error_count': 0},
                'checks': [{
                    'id': 'H-0',
                    'title': 'Brain 数据库就绪检查',
                    'severity': 'warn',
                    'status_label': '数据库不存在',
                    'impact': '当前角色目录尚未初始化 brain.db。',
                    'object': str(db),
                    'basis': '文件存在性只读核验',
                    'source': 'filesystem',
                    'confidence': '高可信（精确事实）',
                    'evidences': [],
                }],
                'incomplete': False,
            }
            if persist:
                _save_report(root, role_id, report)
            return report

        conn = _ro_connect(db)
        conn.row_factory = sqlite3.Row
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            _guard(deadline)
            mem_rows = conn.execute(
                'SELECT id, title, name, created, bucket, type, importance, pinned, '
                'vector_state, is_tombstone, is_deleted, body, body_sha256, raw_markdown, why_remembered '
                'FROM memories'
            ).fetchall() if 'memories' in tables else []

            _guard(deadline)
            sidecar_columns = set()
            if 'sidecar_state' in tables:
                sidecar_columns = {row[1] for row in conn.execute('PRAGMA table_info(sidecar_state)')}
            if 'sidecar_state' not in tables:
                sidecar_rows = []
            elif 'sources_json' in sidecar_columns:
                sidecar_rows = conn.execute(
                    'SELECT memory_id, locked, links_json, sources_json, merged_into FROM sidecar_state'
                ).fetchall()
            else:
                sidecar_rows = conn.execute(
                    'SELECT memory_id, locked, links_json, merged_into FROM sidecar_state'
                ).fetchall()
            sidecar_map = {r['memory_id']: r for r in sidecar_rows}

            # Counts are exact; only bounded detail rows are loaded for the per-check samples.
            _guard(deadline)
            event_total = conn.execute('SELECT COUNT(*) FROM events').fetchone()[0] if 'events' in tables else 0
            stage_total = conn.execute('SELECT COUNT(*) FROM event_stages').fetchone()[0] if 'event_stages' in tables else 0
            version_total = 0
            empty_versions = 0
            if 'versions' in tables:
                version_total, empty_versions = conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(CASE WHEN TRIM(COALESCE(old_content, '')) = '' THEN 1 ELSE 0 END), 0) "
                    'FROM versions').fetchone()
            event_rows = conn.execute('SELECT * FROM events LIMIT ?', (EVENT_ROW_LIMIT,)).fetchall() if 'events' in tables else []
            events_sampled = event_total > len(event_rows)
            _guard(deadline)
            vector_rows = conn.execute(
                'SELECT memory_id, dim, content_hash, length(vector) AS bytes FROM vectors').fetchall() if 'vectors' in tables else []
            _guard(deadline)
            sample_vector_rows = conn.execute(
                'SELECT memory_id, dim, vector FROM vectors WHERE vector IS NOT NULL LIMIT ?',
                (max(1, int(vector_sample_limit)) * 2,)).fetchall() if 'vectors' in tables else []
            tomb_rows = conn.execute('SELECT memory_id, deleted_at FROM tombstones').fetchall() if 'tombstones' in tables else []

            # Vector identity: never assume a dimension. `meta.embedding_model` is what the writer
            # recorded; the configured model comes from the live role settings. Dimensions are
            # reported as stored, and a dimension only counts as unexpected relative to the rest of
            # this database (or the configured model identity), not against a constant.
            stored_embedding_model = ''
            if 'meta' in tables:
                try:
                    meta_row = conn.execute(
                        'SELECT value FROM meta WHERE key=?', (VECTOR_MODEL_META,)).fetchone()
                    stored_embedding_model = str((meta_row[0] if meta_row else '') or '').strip()
                except sqlite3.Error:
                    stored_embedding_model = ''
            configured_model = str(configured_embedding_model or '').strip()
            dim_counts = {}
            for vr in vector_rows:
                try:
                    dim = int(vr['dim'] or 0)
                except (TypeError, ValueError):
                    dim = 0
                dim_counts[dim] = dim_counts.get(dim, 0) + 1
            dominant_dim = (max(dim_counts, key=lambda key: (dim_counts[key], key)) if dim_counts else 0)
            mixed_dims = len([dim for dim, count in dim_counts.items() if dim > 0 and count > 0]) > 1
            vector_identity = {
                'configured_model': configured_model,
                'stored_model': stored_embedding_model,
                'model_mismatch': bool(configured_model and stored_embedding_model
                                       and configured_model != stored_embedding_model),
                'model_recorded': bool(stored_embedding_model),
                'dimensions': {str(dim): count for dim, count in sorted(dim_counts.items())},
                'dominant_dim': dominant_dim,
                'mixed_dimensions': mixed_dims,
            }
            stats['embedding_model'] = stored_embedding_model or configured_model
            stats['vector_dimensions'] = vector_identity['dimensions']

            active_mems = [r for r in mem_rows if not r['is_tombstone'] and not r['is_deleted'] and r['bucket'] != 'archive']
            archived_mems = [r for r in mem_rows if not r['is_tombstone'] and not r['is_deleted'] and r['bucket'] == 'archive']
            live_ids = {r['id'] for r in mem_rows if not r['is_tombstone'] and not r['is_deleted']}
            active_ids = {r['id'] for r in active_mems}

            stats.update({
                'active_memories': len(active_mems),
                'pinned_memories': sum(1 for r in active_mems if r['pinned']),
                'archived_memories': len(archived_mems),
                'tombstones': len(tomb_rows),
                'events_total': event_total,
                'event_stages_total': stage_total,
                'versions_total': version_total,
                'vectors_total': len(vector_rows),
            })

            # H-1: Memory growth & distribution
            by_bucket = {}
            total_chars = 0
            for r in mem_rows:
                if r['is_tombstone'] or r['is_deleted']:
                    continue
                b = r['bucket'] or 'unknown'
                by_bucket[b] = by_bucket.get(b, 0) + 1
                total_chars += len(r['body'] or '')
            checks.append({
                'id': 'H-1',
                'title': '记忆增长与各层数据分布',
                'severity': 'good',
                'status_label': '分布正常',
                'impact': f"活跃记忆 {len(active_mems)} 条（置顶 {stats['pinned_memories']} 条），已归档 {len(archived_mems)} 条，事件故事 {event_total} 段，累计正文 {total_chars:,} 字。",
                'object': 'memories / events / versions / learned-context',
                'basis': f"分桶分布：{', '.join(f'{k}={v}' for k, v in sorted(by_bucket.items())) or '无'}",
                'source': 'brain.db (mode=ro)',
                'confidence': '高可信（精确 SQLite 统计）',
                'evidences': [],
            })

            # H-2: Duplicates & Fragment candidates
            sha_groups = {}
            norm_groups = {}
            frag_evidences = []
            evt_mem_ids = set()
            if 'events' in tables:
                evt_mem_ids.update(row[0] for row in conn.execute(
                    "SELECT memory_id FROM events WHERE memory_id IS NOT NULL AND memory_id != ''"))
            if 'event_sources' in tables:
                evt_mem_ids.update(row[0] for row in conn.execute(
                    "SELECT memory_id FROM event_sources WHERE memory_id IS NOT NULL AND memory_id != ''"))
            for r in active_mems:
                body = r['body'] or ''
                sha = r['body_sha256'] or hashlib.sha256(body.encode('utf-8')).hexdigest()
                sha_groups.setdefault(sha, []).append(r)
                norm = _norm_text(body)
                if len(norm) >= 12:
                    norm_groups.setdefault(norm, []).append(r)
                if len(body.strip()) < 40 and not r['pinned'] and not (r['why_remembered'] or '').strip() and r['id'] not in evt_mem_ids:
                    frag_evidences.append({
                        'memId': r['id'],
                        'label': _safe_title(r['title'] or r['name'], r['id']),
                        'note': f"正文仅 {len(body.strip())} 字且无关联事件/原因（待人工确认）",
                    })

            dup_evidences = []
            seen_dup_pairs = set()
            for grp in (*sha_groups.values(), *norm_groups.values()):
                if len(grp) > 1:
                    ids = tuple(sorted(x['id'] for x in grp))
                    if ids not in seen_dup_pairs:
                        seen_dup_pairs.add(ids)
                        dup_evidences.append({
                            'memId': grp[0]['id'],
                            'label': f"{_safe_title(grp[0]['title'], grp[0]['id'])} 等 {len(grp)} 条",
                            'note': '正文内容完全一致或规范化相同',
                        })

            # Offline cosine similarity on existing vectors (bounded sample, zero paid calls)
            near_dups = []
            vector_decode_issues = []
            active_title_map = {r['id']: _safe_title(r['title'] or r['name'], r['id']) for r in active_mems}
            if not _deadline_reached(deadline) and sample_vector_rows:
                sample_vecs = []
                for idx, vr in enumerate(sample_vector_rows):
                    if idx % 50 == 0:
                        _guard(deadline)
                    mid = vr['memory_id']
                    if mid not in active_ids or not vr['vector']:
                        continue
                    blob = vr['vector']
                    try:
                        dim = int(vr['dim'] or 0)
                    except (TypeError, ValueError):
                        dim = 0
                    # A malformed BLOB used to abort the whole scan; record it for H-7 and keep going.
                    # The recorded `dim` is authoritative; float32 means dim * 4 bytes.
                    if dim <= 0 or len(blob) != dim * 4:
                        vector_decode_issues.append({
                            'memId': mid,
                            'label': _safe_title(active_title_map.get(mid, mid), mid),
                            'note': (f'向量字节数 {len(blob)}，与记录的 {dim or "未知"} 维'
                                     f'（{dim * 4 if dim > 0 else "未知"} 字节）不符'),
                        })
                        continue
                    arr = array.array('f')
                    arr.frombytes(blob)
                    if len(arr) != dim:
                        vector_decode_issues.append({
                            'memId': mid,
                            'label': _safe_title(active_title_map.get(mid, mid), mid),
                            'note': f'向量元素数 {len(arr)} 与记录维度 {dim} 不符',
                        })
                        continue
                    sample_vecs.append((mid, arr))
                    if len(sample_vecs) >= vector_sample_limit:
                        break
                for i in range(len(sample_vecs)):
                    if time.monotonic() >= deadline:
                        break
                    id_a, va = sample_vecs[i]
                    for j in range(i + 1, len(sample_vecs)):
                        id_b, vb = sample_vecs[j]
                        if len(va) != len(vb):
                            continue
                        sim = _cosine(va, vb)
                        if sim >= 0.90:
                            pair = tuple(sorted((id_a, id_b)))
                            if pair not in seen_dup_pairs:
                                near_dups.append((id_a, id_b, sim))
                                dup_evidences.append({
                                    'memId': id_a,
                                    'label': f"{active_title_map.get(id_a, id_a)} ↔ {active_title_map.get(id_b, id_b)}",
                                    'note': f"已有向量余弦相似度 {sim:.2f}（待人工确认，合法短记忆不判错）",
                                })
                        if len(near_dups) >= 15:
                            break

            h2_evidences = (dup_evidences + frag_evidences[:10])[:20]
            h2_warn = len(dup_evidences) > 0 or len(frag_evidences) > 0
            checks.append({
                'id': 'H-2',
                'title': '重复记忆与碎片化候选检查',
                'severity': 'warn' if h2_warn else 'good',
                'status_label': f"发现 {len(dup_evidences)} 组重复 / {len(frag_evidences)} 条碎片候选" if h2_warn else '未见重复与碎片积压',
                'impact': (f"发现 {len(dup_evidences)} 组正文重复或高相似记录，及 {len(frag_evidences)} 条无事件关联且无原因的极短记录（仅提示供人工查看，不自动合并删除）。"
                           if h2_warn else '当前未检测到精确重复、近似重复或无归属碎片积压。'),
                'object': 'memories (body_sha256, body) + vectors (offline cosine)',
                'basis': f"精确哈希+规范化全量比对；向量余弦>=0.90 采样上限 {vector_sample_limit} 条",
                'source': 'brain.db (mode=ro)',
                'confidence': '精确重复=高可信；向量相似/碎片=提示级（需人工确认）',
                'evidences': h2_evidences,
            })

            # H-3: Dangling references vs genuinely unlinked records.
            # Dangling means a pointer to a missing/deleted object. Unlinked means no event, source,
            # quote, sidecar link, merge pointer or relation at all. The second kind is information,
            # not an error: it is reported with its basis and never deleted, merged or archived here.
            _guard(deadline)
            dangling_evidences = []
            sidecar_linked_ids = set()
            for r in sidecar_rows:
                mid = r['memory_id']
                if mid not in live_ids:
                    continue
                links = _json_load(r['links_json'], [])
                if isinstance(links, list):
                    for target in links:
                        if not isinstance(target, str):
                            continue
                        sidecar_linked_ids.update((mid, target))
                        if target not in live_ids:
                            dangling_evidences.append({
                                'category': 'dangling', 'memId': mid,
                                'label': f"{mid} → {target}",
                                'note': 'sidecar 关联目标不存在或已删除（悬空引用，仅报告）',
                                'basis': 'sidecar_state.links_json vs memories.id',
                            })
                sources = _json_load(r['sources_json'], []) if 'sources_json' in r.keys() else []
                if isinstance(sources, list) and any(isinstance(item, str) and item.strip() for item in sources):
                    sidecar_linked_ids.add(mid)
                elif isinstance(sources, dict) and sources:
                    sidecar_linked_ids.add(mid)
                merged_into = r['merged_into']
                if merged_into:
                    sidecar_linked_ids.add(mid)
                    if merged_into in live_ids:
                        sidecar_linked_ids.add(merged_into)
                    if merged_into == mid or merged_into not in live_ids:
                        dangling_evidences.append({
                            'category': 'dangling', 'memId': mid,
                            'label': f"{mid} (merged_into={merged_into})",
                            'note': '合并目标无效或自环（悬空引用，仅报告）',
                            'basis': 'sidecar_state.merged_into vs memories.id',
                        })
            relation_linked_ids = set()
            relations_complete = True
            if 'relations' in tables:
                cursor = None
                while True:
                    if _deadline_reached(deadline):
                        relations_complete = False
                        break
                    if cursor is None:
                        relation_rows = conn.execute(
                            'SELECT memory_id, ord, target_id FROM relations '
                            'ORDER BY memory_id, ord LIMIT 1000').fetchall()
                    else:
                        relation_rows = conn.execute(
                            'SELECT memory_id, ord, target_id FROM relations '
                            'WHERE memory_id > ? OR (memory_id = ? AND ord > ?) '
                            'ORDER BY memory_id, ord LIMIT 1000',
                            (cursor[0], cursor[0], cursor[1])).fetchall()
                    if not relation_rows:
                        break
                    for rel in relation_rows:
                        src, target = rel['memory_id'], rel['target_id']
                        if isinstance(src, str) and src in live_ids:
                            relation_linked_ids.add(src)
                        if isinstance(target, str):
                            if target in live_ids:
                                relation_linked_ids.add(target)
                            elif src in live_ids:
                                dangling_evidences.append({
                                    'category': 'dangling', 'memId': src,
                                    'label': f"{src} → {target}",
                                    'note': 'relations 目标不存在或已删除（悬空引用，仅报告）',
                                    'basis': 'relations.target_id vs memories.id',
                                })
                    if len(relation_rows) < 1000:
                        break
                    last = relation_rows[-1]
                    cursor = (last['memory_id'], last['ord'])
            _guard(deadline)
            linked_memory_ids = set(sidecar_linked_ids) | set(relation_linked_ids)
            if 'events' in tables:
                linked_memory_ids.update(
                    row[0] for row in conn.execute(
                        "SELECT memory_id FROM events WHERE memory_id IS NOT NULL AND memory_id != ''"))
            if 'event_sources' in tables:
                linked_memory_ids.update(
                    row[0] for row in conn.execute(
                        "SELECT memory_id FROM event_sources WHERE memory_id IS NOT NULL AND memory_id != ''"))
            if 'sources' in tables:
                linked_memory_ids.update(
                    row[0] for row in conn.execute(
                        "SELECT memory_id FROM sources WHERE memory_id IS NOT NULL AND memory_id != ''"))
            if 'quotes' in tables:
                linked_memory_ids.update(
                    row[0] for row in conn.execute(
                        "SELECT memory_id FROM quotes WHERE memory_id IS NOT NULL AND memory_id != ''"))
            unlinked_memes = []
            unlinked_mem_total = 0
            unlinked_mem_truncated = False
            if relations_complete and ('events' in tables or 'sources' in tables or 'quotes' in tables
                                       or 'event_sources' in tables):
                candidates = [row for row in active_mems if row['id'] not in linked_memory_ids]
                candidates.sort(key=lambda row: row['created'] or '', reverse=True)
                unlinked_mem_total = len(candidates)
                unlinked_mem_truncated = unlinked_mem_total > 500
                for row in candidates[:ORPHAN_SAMPLE_LIMIT]:
                    mid = row['id']
                    unlinked_memes.append({
                        'category': 'unlinked', 'memId': mid,
                        'label': _safe_title(row['title'] or row['name'], mid),
                        'note': (f"创建于 {row['created'] or '未知时间'}；无事件、无来源、无引述、"
                                 f"无 sidecar 关联或关系链接（仅提示，不代表无价值）"),
                        'basis': 'memories × events/event_sources/sources/quotes/sidecar_state/relations 反连接',
                    })
            elif not relations_complete:
                unlinked_mem_truncated = True

            unlinked_events = []
            unlinked_event_total = 0
            unlinked_event_truncated = False
            if 'events' in tables:
                event_candidates = conn.execute(
                    "SELECT event_id, title, first_at, memory_id FROM events "
                    "WHERE memory_id IS NULL OR memory_id=''"
                ).fetchall()
                sourced = set()
                if 'event_sources' in tables:
                    sourced.update(row[0] for row in conn.execute('SELECT event_id FROM event_sources'))
                if 'event_stages' in tables:
                    sourced.update(row[0] for row in conn.execute('SELECT event_id FROM event_stages'))
                if 'event_ledger' in tables:
                    sourced.update(row[0] for row in conn.execute(
                        "SELECT event_id FROM event_ledger WHERE kind='stage-evidence'"))
                event_candidates = [row for row in event_candidates if row['event_id'] not in sourced]
                event_candidates.sort(key=lambda row: row['first_at'] or '', reverse=True)
                unlinked_event_total = len(event_candidates)
                unlinked_event_truncated = unlinked_event_total > 500
                for row in event_candidates[:ORPHAN_SAMPLE_LIMIT]:
                    unlinked_events.append({
                        'category': 'unlinked', 'eventId': row['event_id'],
                        'label': _safe_title(row['title'], row['event_id']),
                        'note': (f"起始 {row['first_at'] or '未知时间'}；无关联记忆、无来源、无阶段、"
                                 f"无用户原话证据（仅提示，不代表无价值）"),
                        'basis': 'events × event_sources/event_stages/event_ledger/memory_id 反连接',
                    })

            h3_evidences = (dangling_evidences[:ORPHAN_SAMPLE_LIMIT]
                            + unlinked_memes + unlinked_events)
            h3_dangling_count = len(dangling_evidences)
            link_notes = []
            if unlinked_mem_total:
                link_notes.append(f"{unlinked_mem_total} 条活跃记忆无任何关联")
            if unlinked_event_total:
                link_notes.append(f"{unlinked_event_total} 个事件无任何关联")
            if not relations_complete:
                link_notes.append('关系表未在时限内读完；无关联记忆本次不判定，避免把未扫描的关联报成孤立')
            elif unlinked_mem_truncated or unlinked_event_truncated:
                link_notes.append('无关联明细只列出前 15 条；计数是全量')
            checks.append({
                'id': 'H-3',
                'title': '孤立记录、悬空引用与无关联检查',
                'severity': 'warn' if dangling_evidences else 'good',
                'status_label': (f"{h3_dangling_count} 处悬空引用" if dangling_evidences
                                 else ('引用完整' if not link_notes else '无悬空引用；存在无关联记录（仅提示）')),
                'impact': ((f"发现 {h3_dangling_count} 处悬空引用（指向不存在/已删除对象）；"
                            if dangling_evidences else '未发现悬空引用；')
                           + ('；'.join(link_notes) + '。' if link_notes else '所有可解析引用均指向有效对象。')
                           + ' 无关联只表示当前没有可追溯连接，不等于无价值；本检查只报告，不自动删除、合并或归档。'),
                'object': ('sidecar_state.links_json/merged_into、relations、events.memory_id；'
                           'memories × events/sources/quotes/sidecar_state/relations'),
                'basis': ('悬空引用=精确外键核验；无关联=反连接核验（不看正文质量，不做价值判断）'
                          + ('；事件取样上限 500' if unlinked_event_truncated else '')),
                'source': 'brain.db (mode=ro)',
                'confidence': '高可信（精确引用/反连接核验）；无关联为提示级',
                'evidences': h3_evidences,
                'scope': 'current',
                'counts': {'dangling': h3_dangling_count,
                           'unlinked_memories': unlinked_mem_total,
                           'unlinked_events': unlinked_event_total},
            })

            # H-4: Event source broken links
            _guard(deadline)
            ev_link_issues = []
            for i, ev in enumerate(event_rows):
                if i % 500 == 0:
                    _guard(deadline)
                mid = ev['memory_id']
                if mid and mid not in live_ids:
                    ev_link_issues.append({
                        'eventId': ev['event_id'],
                        'label': _safe_title(ev['title'], ev['event_id']),
                        'note': f"关联记忆 {mid} 不在有效记忆库中",
                    })
            stage_ev_total = conn.execute(
                "SELECT COUNT(*) FROM event_ledger WHERE kind='stage-evidence'").fetchone()[0] if 'event_ledger' in tables else 0
            _guard(deadline)
            stage_ev_rows = conn.execute(
                "SELECT event_id, payload_json FROM event_ledger WHERE kind='stage-evidence' LIMIT ?",
                (STAGE_EVIDENCE_LIMIT,)).fetchall() if 'event_ledger' in tables else []
            chat_ev_count = 0
            for sr in stage_ev_rows:
                payload = _json_load(sr['payload_json'], {})
                if payload.get('kind') == 'chat':
                    chat_ev_count += 1
            checks.append({
                'id': 'H-4',
                'title': '事件来源与记忆链接完整性',
                'severity': 'warn' if (ev_link_issues or events_sampled) else 'good',
                'status_label': (f"已核验 {len(event_rows)}/{event_total} 个事件（未全量）" if events_sampled
                                 else (f"{len(ev_link_issues)} 条事件链接断开" if ev_link_issues else '事件关联完整')),
                'impact': (f"发现 {len(ev_link_issues)} 个事件的 memory_id 指向已失效记忆；stage-evidence 共 {stage_ev_total} 条（其中 chat 原话证据 {chat_ev_count} 条）。"
                           if ev_link_issues else (f"已核验 {len(event_rows)}/{event_total} 个事件的链接，剩余事件未在本轮时限内检查；stage-evidence 共 {stage_ev_total} 条（chat 证据 {chat_ev_count} 条）。"
                                               if events_sampled else f"全部 {event_total} 个事件链接正常；stage-evidence 共 {stage_ev_total} 条（chat 证据 {chat_ev_count} 条）。")),
                'object': 'events.memory_id / event_ledger(stage-evidence)',
                'basis': '精确引用集合核对',
                'source': 'brain.db (mode=ro)',
                'confidence': '高可信（精确引用核验）',
                'evidences': ev_link_issues[:15],
            })

            # H-5: Event timeline anomalies
            _guard(deadline)
            ev_time_issues = []
            for i, ev in enumerate(event_rows):
                if i % 500 == 0:
                    _guard(deadline)
                eid = ev['event_id']
                fa = _parse_dt(ev['first_at'])
                la = _parse_dt(ev['last_at'])
                if fa and la and fa > la:
                    ev_time_issues.append({'eventId': eid, 'label': _safe_title(ev['title'], eid), 'note': '事件起止时间逆序 (first_at > last_at)'})
                payload = _json_load(ev['payload_json'], {})
                if payload.get('story_pending') or payload.get('dirty'):
                    ev_time_issues.append({'eventId': eid, 'label': _safe_title(ev['title'], eid), 'note': '故事待重整或投影脏标记'})
                if payload.get('pending_review'):
                    ev_time_issues.append({'eventId': eid, 'label': _safe_title(ev['title'], eid), 'note': '有待人工审核的冲突更新草稿'})
            checks.append({
                'id': 'H-5',
                'title': '事件时间线与待重整状态',
                'severity': 'warn' if (ev_time_issues or events_sampled) else 'good',
                'status_label': (f"已核验 {len(event_rows)}/{event_total} 个事件（未全量）" if events_sampled
                                 else (f"{len(ev_time_issues)} 项待重整或时间异常" if ev_time_issues else '时间线有序')),
                'impact': (f"共有 {len(ev_time_issues)} 个事件存在待重整、待复核草稿或时间顺序提示。"
                           if ev_time_issues else (f"已核验 {len(event_rows)}/{event_total} 个事件的时间线，剩余事件未在本轮时限内检查。"
                                               if events_sampled else '所有事件时间线顺序与状态标记正常。')),
                'object': 'events(first_at, last_at, payload_json) / event_stages',
                'basis': '确定性时间边界与待处理标志位核对',
                'source': 'brain.db (mode=ro)',
                'confidence': '高可信（确定性校验）',
                'evidences': ev_time_issues[:15],
            })

            # H-6: Data integrity & versions
            _guard(deadline)
            integrity_issues = []
            try:
                qc = conn.execute('PRAGMA quick_check').fetchone()
                if not qc or qc[0] != 'ok':
                    integrity_issues.append({'label': 'SQLite quick_check', 'note': str(qc[0] if qc else 'failed')})
            except sqlite3.Error as exc:
                integrity_issues.append({'label': 'SQLite quick_check', 'note': str(exc)})

            crlf_count = 0
            sha_mismatch = []
            for i, r in enumerate(mem_rows):
                if i % 500 == 0:
                    _guard(deadline)
                if r['is_tombstone'] or r['is_deleted']:
                    continue
                if isinstance(r['raw_markdown'], str) and '\r\n' in r['raw_markdown']:
                    crlf_count += 1
                calc = hashlib.sha256((r['body'] or '').encode('utf-8')).hexdigest()
                if r['body_sha256'] and calc != r['body_sha256']:
                    sha_mismatch.append({'memId': r['id'], 'label': _safe_title(r['title'], r['id']), 'note': 'body_sha256 与正文实际哈希不一致'})

            all_h6 = integrity_issues + sha_mismatch
            h6_warn = bool(all_h6 or empty_versions > 0)
            checks.append({
                'id': 'H-6',
                'title': '数据库完整性与版本账本校验',
                'severity': 'warn' if h6_warn else 'good',
                'status_label': '发现校验异常' if all_h6 else (f"{crlf_count} 条含 CRLF 换行" if crlf_count else '完整性良好'),
                'impact': (f"PRAGMA quick_check 与版本核验发现 {len(all_h6)} 项异常，{empty_versions} 条空版本；另有 {crlf_count} 条卡片使用 CRLF 换行（已知历史格式特征）。"
                           if (h6_warn or crlf_count) else 'SQLite quick_check 通过，正文哈希与版本记录全部一致。'),
                'object': 'PRAGMA quick_check / body_sha256 / versions / raw_markdown',
                'basis': f"SQLite quick_check + UTF-8 SHA-256 + CRLF 统计 ({crlf_count} 条)",
                'source': 'brain.db (mode=ro)',
                'confidence': '高可信（确定性密码学/结构校验）',
                'evidences': all_h6[:15],
            })

            # H-7: Retrieval index consistency, relative to the stored/configured embedding identity.
            _guard(deadline)
            vec_by_id = {vr['memory_id']: vr for vr in vector_rows}
            vec_issues = list(vector_decode_issues)
            if vector_identity['model_mismatch']:
                vec_issues.append({
                    'label': 'Embedding 模型身份不一致',
                    'note': (f"Embedding 模型身份不一致：角色配置为 {configured_model}，"
                             f"库内记录为 {stored_embedding_model}；"
                             f"旧向量不可与当前查询向量混用，需显式重建索引"),
                    'basis': 'cfg.memory.model vs meta.embedding_model',
                })
            elif configured_model and not stored_embedding_model:
                vec_issues.append({
                    'label': '库内未记录 Embedding 模型身份',
                    'note': (f"角色配置为 {configured_model}，但 meta.embedding_model 为空；"
                             f"无法据此判断现存向量属于哪个模型"),
                    'basis': 'meta.embedding_model 缺失',
                })
            if vector_identity['mixed_dimensions']:
                vec_issues.append({
                    'label': '库内向量维度不统一',
                    'note': ('已存维度分布：' + ', '.join(
                        f"{dim}维×{count}" for dim, count in sorted(dim_counts.items())) +
                        f"；主导维度 {dominant_dim} 维（未假定固定维度）"),
                    'basis': 'vectors.dim GROUP BY',
                })
            for i, r in enumerate(active_mems):
                if i % 500 == 0:
                    _guard(deadline)
                mid = r['id']
                vr = vec_by_id.get(mid)
                vstate = r['vector_state'] or 'ready'
                if vstate != 'ready':
                    vec_issues.append({'memId': mid, 'label': _safe_title(r['title'], mid),
                                       'note': f"vector_state={vstate}（关键词检索可用）",
                                       'basis': 'memories.vector_state'})
                elif vr is None:
                    vec_issues.append({'memId': mid, 'label': _safe_title(r['title'], mid),
                                       'note': '状态为 ready 但缺少向量行',
                                       'basis': 'memories vs vectors 外键'})
                else:
                    dim = vr['dim']
                    if dim <= 0:
                        vec_issues.append({'memId': mid, 'label': _safe_title(r['title'], mid),
                                           'note': f"向量记录维度无效：{dim}",
                                           'basis': 'vectors.dim'})
                    elif dominant_dim and dim != dominant_dim:
                        vec_issues.append({
                            'memId': mid, 'label': _safe_title(r['title'], mid),
                            'note': (f"向量维度 {dim} 与库内主导维度 {dominant_dim} 不一致"
                                     f"（可能是换模型后尚未重建，不按固定 1024 维判断）"),
                            'basis': 'vectors.dim vs 库内维度分布',
                        })
                    elif vr['bytes'] is not None and vr['bytes'] != dim * 4:
                        vec_issues.append({'memId': mid, 'label': _safe_title(r['title'], mid),
                                           'note': f"向量字节数 {vr['bytes']} 与记录维度 {dim} 不符",
                                           'basis': 'length(vector) vs vectors.dim × 4'})
                    if vr['content_hash'] and r['body_sha256'] and vr['content_hash'] != r['body_sha256']:
                        vec_issues.append({'memId': mid, 'label': _safe_title(r['title'], mid),
                                           'note': '正文修改后向量 content_hash 未同步',
                                           'basis': 'vectors.content_hash vs memories.body_sha256'})
            residual_tomb_vecs = [vr['memory_id'] for vr in vector_rows if vr['memory_id'] not in live_ids]
            for tid in residual_tomb_vecs[:10]:
                vec_issues.append({'label': f"残留向量 {tid}", 'note': '对应记忆已删除或成为墓碑',
                                   'basis': 'vectors vs memories 活跃集合'})
            parts = []
            if stored_embedding_model or configured_model:
                parts.append(f"Embedding 模型：库内 {stored_embedding_model or '未记录'}"
                             f" / 配置 {configured_model or '未配置'}")
            if dim_counts:
                parts.append('维度分布：' + ', '.join(
                    f"{dim}维×{count}" for dim, count in sorted(dim_counts.items())))
            checks.append({
                'id': 'H-7',
                'title': '检索索引与记忆数据一致性',
                'severity': 'warn' if vec_issues else 'good',
                'status_label': f"{len(vec_issues)} 条索引待同步" if vec_issues else '索引一致',
                'impact': ((f"发现 {len(vec_issues)} 项向量索引待补齐、维度/模型不一致、哈希滞后或墓碑残留"
                            f"（仅提示，不自动重算付费向量）。" if vec_issues else
                            f"全部 {len(active_mems)} 条活跃记忆向量维度自洽、模型身份一致、内容哈希一致，"
                            f"无墓碑残留。")
                           + (' ' + '；'.join(parts) + '。' if parts else '')),
                'object': 'memories(vector_state, body_sha256) vs vectors(dim, content_hash, length) / meta.embedding_model',
                'basis': '逐条外键、哈希与长度对账；维度以库内分布和记录值为准，不使用固定维度',
                'source': 'brain.db (mode=ro)',
                'confidence': '高可信（确定性对账）',
                'evidences': vec_issues[:20],
                'vector_identity': vector_identity,
            })
            # Period slice: only facts that are actually dated by the database are claimed.
            # Whole-database health above stays labelled "current"; this block answers what changed
            # inside [from_date, to_date] without pretending to reconstruct an unrecorded past state.
            if range_requested:
                _guard(deadline)
                created_in_range = [r for r in mem_rows if _in_range(r['created'], dt_from, dt_to)]
                period_mems = [r for r in created_in_range
                               if not r['is_tombstone'] and not r['is_deleted']]
                period_ids = {r['id'] for r in period_mems}
                period_events = [ev for ev in event_rows
                                 if _in_range((ev['created_at'] or ev['first_at']) if 'created_at' in ev.keys()
                                              else ev['first_at'], dt_from, dt_to)]
                period_event_ids = {ev['event_id'] for ev in period_events}
                stage_times = conn.execute(
                    'SELECT at, event_id FROM event_stages LIMIT 20000').fetchall() if 'event_stages' in tables else []
                period_stages = [row for row in stage_times if _in_range(row['at'], dt_from, dt_to)]
                version_times = conn.execute(
                    'SELECT time FROM versions ORDER BY seq DESC LIMIT 20000').fetchall() if 'versions' in tables else []
                period_versions = [row for row in version_times if _in_range(row['time'], dt_from, dt_to)]

                # Duplicates/fragments inside the period only, using the same deterministic rules.
                p_sha = {}
                p_norm = {}
                for r in period_mems:
                    body = r['body'] or ''
                    sha = r['body_sha256'] or hashlib.sha256(body.encode('utf-8')).hexdigest()
                    p_sha.setdefault(sha, []).append(r)
                    norm = _norm_text(body)
                    if len(norm) >= 12:
                        p_norm.setdefault(norm, []).append(r)
                p_dup_ids = set()
                p_dups = []
                for grp in (*p_sha.values(), *p_norm.values()):
                    if len(grp) > 1:
                        ids = tuple(sorted(x['id'] for x in grp))
                        if ids in p_dup_ids:
                            continue
                        p_dup_ids.add(ids)
                        p_dups.append(ids)
                p_frags = [ev for ev in frag_evidences if ev.get('memId') in period_ids]
                p_dangling = [ev for ev in dangling_evidences if ev.get('memId') in period_ids]
                p_dangling.extend(ev for ev in ev_link_issues if ev.get('eventId') in period_event_ids)
                p_index = []
                for r in period_mems:
                    mid = r['id']
                    vr = vec_by_id.get(mid)
                    if (r['vector_state'] or 'ready') != 'ready':
                        p_index.append({'memId': mid, 'label': _safe_title(r['title'], mid),
                                        'note': f"vector_state={r['vector_state']}（关键词检索可用）"})
                    elif vr is None:
                        p_index.append({'memId': mid, 'label': _safe_title(r['title'], mid),
                                        'note': '状态为 ready 但缺少向量行'})
                    else:
                        dim = vr['dim'] or 0
                        if dim <= 0 or (dominant_dim and dim != dominant_dim):
                            p_index.append({'memId': mid, 'label': _safe_title(r['title'], mid),
                                            'note': (f"向量维度 {dim or '未知'} 与库内主导维度 "
                                                     f"{dominant_dim or '未知'} 不一致")})
                        elif vr['bytes'] is not None and vr['bytes'] != dim * 4:
                            p_index.append({'memId': mid, 'label': _safe_title(r['title'], mid),
                                            'note': f"向量字节数 {vr['bytes']} 与记录维度 {dim} 不符"})
                        if vr['content_hash'] and r['body_sha256'] and vr['content_hash'] != r['body_sha256']:
                            p_index.append({'memId': mid, 'label': _safe_title(r['title'], mid),
                                            'note': '正文修改后向量 content_hash 未同步'})

                missing_mem_created = sum(
                    1 for r in mem_rows
                    if not r['is_tombstone'] and not r['is_deleted'] and _parse_dt(r['created']) is None)
                missing_event_time = sum(
                    1 for ev in event_rows if _parse_dt(ev['first_at'] or ev['created_at'] if 'created_at' in ev.keys()
                                                        else ev['first_at']) is None)
                period.update({
                    'status': 'available',
                    'growth': {
                        'memories_created': len(created_in_range),
                        'events_created': len(period_events),
                        'event_stages': len(period_stages),
                        'versions': len(period_versions),
                        'event_stages_total': stage_total,
                        'versions_total': version_total,
                    },
                    'duplicates': {'count': len(p_dups),
                                   'evidences': [{'label': ' / '.join(ids), 'note': '期间创建的记忆正文完全一致或规范化相同'}
                                                 for ids in p_dups[:10]]},
                    'fragments': {'count': len(p_frags), 'evidences': p_frags[:10]},
                    'dangling': {'count': len(p_dangling), 'evidences': p_dangling[:10]},
                    'index_anomalies': {'count': len(p_index), 'evidences': p_index[:10]},
                    'coverage': {
                        'memory_created_missing': missing_mem_created,
                        'event_time_missing_in_sample': missing_event_time,
                        'events_checked': len(event_rows),
                        'events_total': event_total,
                        'events_sampled': events_sampled,
                        'event_stages_checked': len(stage_times),
                        'event_stages_total': stage_total,
                        'versions_checked': len(version_times),
                        'versions_total': version_total,
                        'time_basis': 'memories.created / events.created_at|first_at / event_stages.at / versions.time',
                    },
                })
                if events_sampled or len(stage_times) < stage_total or len(version_times) < version_total:
                    period['coverage']['partial'] = True
                    period['coverage']['note'] = ('事件/阶段/版本超过单次取样上限，期间计数只覆盖已读取部分；'
                                                  '未读取部分没有被当成“不存在”。')
                # Attach period slices to the checks they qualify, so the page can show both scopes.
                check_period = {c['id']: c for c in checks}
                if 'H-1' in check_period:
                    check_period['H-1']['period'] = {
                        'memories_created': len(created_in_range),
                        'events_created': len(period_events),
                        'event_stages': len(period_stages),
                        'versions': len(period_versions),
                    }
                    check_period['H-1']['scope'] = 'current+range'
                if 'H-2' in check_period:
                    check_period['H-2']['period'] = {'duplicate_groups': len(p_dups), 'fragments': len(p_frags)}
                    check_period['H-2']['scope'] = 'current+range'
                if 'H-3' in check_period:
                    check_period['H-3']['period'] = {'dangling': len(p_dangling)}
                    check_period['H-3']['scope'] = 'current+range'
                if 'H-4' in check_period:
                    check_period['H-4']['period'] = {'dangling': sum(1 for ev in ev_link_issues
                                                                     if ev.get('eventId') in period_event_ids)}
                    check_period['H-4']['scope'] = 'current+range'
                if 'H-7' in check_period:
                    check_period['H-7']['period'] = {'index_anomalies': len(p_index)}
                    check_period['H-7']['scope'] = 'current+range'
            else:
                for check in checks:
                    check.setdefault('scope', 'current')
        except _ScanTimeout:
            timed_out = True
        finally:
            conn.close()

        if not timed_out and _deadline_reached(deadline):
            timed_out = True
        # H-8/H-9 still run when the database stage finished inside the budget; once the
        # budget is gone they stay unexecuted and the report says so explicitly.
        rdir = _role_dir(root, role_id)
        if not timed_out:
            recall_rows = [row for row in _read_jsonl(rdir / 'recall-diagnostics.jsonl', 600)
                           if _in_range(row.get('time'), dt_from, dt_to)]
            degraded_count = 0
            judge_fail_count = 0
            budget_drop_count = 0
            for row in recall_rows:
                if row.get('failure') or 'keyword' in str(row.get('retrieval_mode', '')):
                    degraded_count += 1
                j = row.get('judge')
                if isinstance(j, dict) and j.get('status') == 'failed':
                    judge_fail_count += 1
                if row.get('budget_dropped'):
                    budget_drop_count += 1

            exp_fail_rows = [row for row in _read_jsonl(rdir / 'experience-memory-failures.jsonl', 200)
                             if _in_range(row.get('time'), dt_from, dt_to)]
            page_err_rows = [row for row in _read_jsonl(rdir / 'peter-page-errors.jsonl', 200)
                             if _in_range(row.get('time'), dt_from, dt_to)]

            runtime_warns = degraded_count + judge_fail_count + len(exp_fail_rows) + len(page_err_rows)
            checks.append({
                'id': 'H-8',
                'title': '运行期召回、整理与注入异常统计',
                'severity': 'warn' if runtime_warns else 'good',
                'status_label': f"{runtime_warns} 次运行降级/异常" if runtime_warns else '运行平稳',
                'impact': (f"窗口内检索样本 {len(recall_rows)} 轮：检索降级 {degraded_count} 次，判官超时/失败 {judge_fail_count} 次，预算裁剪 {budget_drop_count} 次，经历整理失败 {len(exp_fail_rows)} 次，认知页写入错误 {len(page_err_rows)} 次。"),
                'object': 'recall-diagnostics.jsonl / experience-memory-failures.jsonl / peter-page-errors.jsonl',
                'basis': f"时间窗 [{from_date or '全部'}, {to_date or '今天'}] 日志统计",
                'source': 'role runtime jsonl',
                'confidence': '高可信（真实日志计数）',
                'evidences': [],
                'scope': 'range' if range_requested else 'range (all logs)',
                'period': {'from_date': from_date, 'to_date': to_date} if range_requested else None,
            })

        # H-9: Background maintenance & freeze status
        if not timed_out:
            frozen = (rdir / 'brain-write-freeze').exists()
            exp_avail = bool(experience_status and experience_status.get('available'))
            exp_enabled = bool(experience_status and experience_status.get('enabled'))
            exp_kept = int((experience_status or {}).get('failures_kept') or 0)
            judge_breaker = bool(judge_status and judge_status.get('breaker_open'))
            bg_warn = frozen or exp_kept > 0 or judge_breaker
            checks.append({
                'id': 'H-9',
                'title': '后台整理与写入保护机制状态',
                'severity': 'warn' if bg_warn else 'good',
                'status_label': '写入已冻结' if frozen else ('存在告警态' if bg_warn else '护栏正常'),
                'impact': (f"Brain 写入冻结：{'是' if frozen else '否'}；自动经历整理：{'已开启' if exp_enabled else '已关闭'}（可用={exp_avail}，留存失败草稿={exp_kept}）；判官熔断：{'已熔断' if judge_breaker else '未熔断'}。"),
                'object': 'brain-write-freeze / ExperienceMemory / RecallJudge',
                'basis': '直接读取运行组件 status() 与锁标志文件',
                'source': 'live runtime components',
                'confidence': '高可信（实时状态读数）',
                'evidences': [],
                'scope': 'current',
            })

        if range_requested:
            # Historical evidence is read from the existing daily snapshots. When it is absent the
            # report says so explicitly instead of presenting today's numbers as that period's state.
            snapshot_rows, snap_failed = _read_jsonl_scan(
                health_reports_dir(root, role_id) / 'health-snapshots.jsonl',
                predicate=lambda row: _in_range(row.get('scanned_at') or row.get('day'), dt_from, dt_to))
            learned_rows, learned_failed = _read_jsonl_scan(
                rdir / 'learned-context.versions.jsonl',
                predicate=lambda row: _in_range(row.get('time'), dt_from, dt_to))
            period['growth']['learned_context_changes'] = len(learned_rows)
            if snap_failed or learned_failed:
                period.setdefault('coverage', {})['history_read_failed'] = True
                period['coverage']['note'] = '历史快照或认知版本文件读不完整，期间覆盖不能当成完整。'
            complete_snaps = [row for row in snapshot_rows if not row.get('incomplete')]
            dropped_incomplete = len(snapshot_rows) - len(complete_snaps)
            if len(complete_snaps) >= 2:
                first_snap, last_snap = complete_snaps[0], complete_snaps[-1]
                delta = {}
                for key in ('active_memories', 'events_total', 'warn_count', 'error_count'):
                    try:
                        delta[key] = int(last_snap.get(key) or 0) - int(first_snap.get(key) or 0)
                    except (TypeError, ValueError):
                        delta[key] = None
                period['historical_state'] = {
                    'available': True,
                    'delta_known': True,
                    'observations': len(complete_snaps),
                    'incomplete_snapshots_ignored': dropped_incomplete,
                    'first': {'at': first_snap.get('scanned_at') or first_snap.get('day'),
                              'active_memories': first_snap.get('active_memories'),
                              'events_total': first_snap.get('events_total')},
                    'last': {'at': last_snap.get('scanned_at') or last_snap.get('day'),
                             'active_memories': last_snap.get('active_memories'),
                             'events_total': last_snap.get('events_total')},
                    'delta': delta,
                    'basis': 'health-snapshots.jsonl（每日只读快照，不含未完成扫描）',
                    'limit': '快照只记录当日总量与告警数，不能还原期间内部健康状态；'
                             '期间内部状态只能依据库内时间字段判断。',
                }
                first_at = _parse_dt(first_snap.get('scanned_at') or first_snap.get('day'))
                if dt_from is not None and (first_at is None or first_at.date() > dt_from.date()):
                    period['historical_state']['coverage'] = 'partial'
                    period['historical_state']['reason'] = 'range_starts_before_first_snapshot'
            elif len(complete_snaps) == 1:
                only = complete_snaps[0]
                period['historical_state'] = {
                    'available': True,
                    'delta_known': False,
                    'observations': 1,
                    'incomplete_snapshots_ignored': dropped_incomplete,
                    'first': {'at': only.get('scanned_at') or only.get('day'),
                              'active_memories': only.get('active_memories'),
                              'events_total': only.get('events_total')},
                    'last': {'at': only.get('scanned_at') or only.get('day'),
                             'active_memories': only.get('active_memories'),
                             'events_total': only.get('events_total')},
                    'delta': {'active_memories': None, 'events_total': None,
                              'warn_count': None, 'error_count': None},
                    'reason': 'single_complete_snapshot',
                    'basis': 'health-snapshots.jsonl',
                    'limit': '只有一条完整快照，没有第二个观测值，变化未知，不能写成 0。',
                }
            else:
                period['historical_state'] = {
                    'available': False,
                    'delta_known': False,
                    'reason': ('history_unreadable' if snap_failed else
                               'only_incomplete_snapshots' if snapshot_rows else 'no_snapshot_in_range'),
                    'note': '该时间段没有可用的完整巡检快照；仅能依据 memories.created / events / versions '
                            '等带时间字段的库内记录判断“期间变化”，不能虚构当时整体健康状态。',
                }
            if timed_out:
                period['status'] = 'incomplete'
                period['reason'] = 'scan_timeout_before_period_complete'

        skipped_checks = []
        if timed_out:
            done_ids = {c['id'] for c in checks}
            skipped_checks = [cid for cid in ALL_CHECK_IDS if cid not in done_ids]
            checks.append({
                'id': 'H-T',
                'title': '巡检执行时限与完成度',
                'severity': 'warn',
                'status_label': '本轮巡检未完成（超时）',
                'impact': (f'已达到 {max(1.0, float(timeout_s)):.1f} 秒执行时限，未执行：{"、".join(skipped_checks) or "无"}。'
                           '未执行的检查不会被标为通过，请稍后重试或缩小检查范围。'),
                'object': 'scan deadline / per-stage work budget',
                'basis': f'started={started:.3f}s, deadline={deadline:.3f}s',
                'source': 'brain_health.run_health_scan',
                'confidence': '确定（时间预算）',
                'evidences': [],
            })

        warn_count = sum(1 for c in checks if c['severity'] == 'warn')
        error_count = sum(1 for c in checks if c['severity'] == 'error')
        good_count = sum(1 for c in checks if c['severity'] == 'good')
        elapsed_ms = round((time.monotonic() - started) * 1000)

        if range_requested and not period.get('growth'):
            period['status'] = period.get('status') if period.get('status') == 'incomplete' else 'incomplete'
            period.setdefault('reason', 'scan_timeout_before_period_complete')
        for check in checks:
            check.setdefault('scope', 'current')
        report = {
            'schema_version': 2,
            'scanned_at': _now_iso(),
            'duration_ms': elapsed_ms,
            'read_only': True,
            'from_date': from_date,
            'to_date': to_date,
            'scope': {
                'current_state': True,
                'requested_range': range_requested,
                'from_date': from_date,
                'to_date': to_date,
                'label': ('全库当前 + 指定期间' if range_requested else '全库当前'),
            },
            'period': period,
            'stats': stats,
            'incomplete': timed_out,
            'sampled_events': ({'checked': len(event_rows), 'total': event_total}
                               if event_total and events_sampled else None),
            'timed_out': timed_out,
            'completed_checks': [c['id'] for c in checks if c['id'] != 'H-T'],
            'skipped_checks': skipped_checks,
            'timeout_s': max(1.0, float(timeout_s)),
            'summary': {
                'status': 'incomplete' if timed_out else ('error' if error_count else ('warn' if warn_count else 'good')),
                'status_label': ('本轮巡检未完成（超时），未执行项不等同通过' if timed_out
                                 else (f'发现 {warn_count + error_count} 项需关注' if (warn_count or error_count) else '系统健康，零异常')),
                'incomplete': timed_out,
                'total_checks': len(checks),
                'good_count': good_count,
                'warn_count': warn_count,
                'error_count': error_count,
            },
            'checks': checks,
        }
        if persist:
            _save_report(root, role_id, report)
        return report
    finally:
        with _SCAN_LOCK:
            _ACTIVE_SCAN = False


def export_report(report, fmt='markdown'):
    """Export strictly sanitized report text without full private memory contents."""
    if not isinstance(report, dict):
        return ''
    if fmt == 'json':
        return json.dumps(report, ensure_ascii=False, indent=2)
    s = report.get('summary', {})
    scope = report.get('scope') or {}
    period = report.get('period') or {}
    lines = [
        '# VEIL Brain 记忆健康检查报告（严格只读脱敏摘要）',
        f"- 检查时间：{report.get('scanned_at', '未知')}",
        f"- 耗时：{report.get('duration_ms', 0)} ms（零写操作，零外部 Token 消耗）",
        f"- 数据范围：{scope.get('label') or '全库当前'}",
        f"- 整体状态：{s.get('status_label', '未知')}（正常 {s.get('good_count', 0)} / 关注 {s.get('warn_count', 0)}）",
    ]
    if report.get('from_date') or report.get('to_date'):
        lines.append(f"- 指定期间：{report.get('from_date') or '最早'} 至 {report.get('to_date') or '最新'}")
    lines.append('')
    if period.get('requested'):
        lines.append('## 指定期间变化与异常（与全库当前状态分开）')
        if period.get('status') in ('incomplete', 'unavailable'):
            lines.append('- 指定期间检查未完成，计数未统计，不能把缺失当成 0。')
        else:
            growth = period.get('growth') or {}
            lines.append(f"- 期间新增记忆：{growth.get('memories_created', 0)} 条；"
                         f"事件：{growth.get('events_created', 0)} 个；阶段：{growth.get('event_stages', 0)} 个；"
                         f"版本：{growth.get('versions', 0)} 条；认知版本变更：{growth.get('learned_context_changes', 0)} 条")
            for key, label in (('duplicates', '重复'), ('fragments', '碎片'),
                               ('dangling', '断链/悬空引用'), ('index_anomalies', '索引异常')):
                section = period.get(key) or {}
                lines.append(f"- 期间{label}：{section.get('count', 0)} 项")
                for ev in (section.get('evidences') or [])[:5]:
                    lines.append(f"  - {ev.get('label')}: {ev.get('note')}")
            coverage = period.get('coverage') or {}
            if coverage.get('note'):
                lines.append(f"- 期间证据边界：{coverage['note']}")
        hist = period.get('historical_state') or {}
        if period.get('status') in ('incomplete', 'unavailable'):
            lines.append('- 历史快照：本轮未完成，不判断有没有快照。')
        elif hist.get('available') and hist.get('delta_known') is False:
            lines.append(f"- 历史快照：{hist.get('observations', 0)} 次完整记录，变化未知"
                         f"（{hist.get('reason') or '快照不足'}）。{hist.get('limit') or ''}")
        elif hist.get('available'):
            delta = hist.get('delta') or {}
            lines.append(f"- 历史快照：{hist.get('observations', 0)} 次；"
                         f"活跃记忆变化 {delta.get('active_memories')}，事件变化 {delta.get('events_total')}；"
                         f"{hist.get('limit', '')}")
            if hist.get('coverage') == 'partial':
                lines.append(f"- 历史快照覆盖不足：{hist.get('reason', '')}")
        else:
            lines.append(f"- 历史快照：缺失（{hist.get('reason', 'no_snapshot_in_range')}）；"
                         f"只报告有库内时间字段支持的期间变化，不推断当时整体健康状态。")
        lines.append('')
    lines.append('## 全库当前状态检查明细')
    for chk in report.get('checks', []):
        scope_label = {'current': '全库当前', 'current+range': '全库当前 + 指定期间',
                       'range': '指定期间', 'range (all logs)': '日志当前窗口'}.get(chk.get('scope'), '全库当前')
        lines.append(f"### [{chk.get('id')}] {chk.get('title')} — {chk.get('status_label')}")
        lines.append(f"- 数据范围：{scope_label}")
        lines.append(f"- 影响范围：{chk.get('impact')}")
        lines.append(f"- 检查对象：{chk.get('object')} | 可信度：{chk.get('confidence')}")
        for ev in chk.get('evidences', [])[:10]:
            basis = f"（依据：{ev['basis']}）" if ev.get('basis') else ''
            lines.append(f"  - {ev.get('label')}: {ev.get('note')}{basis}")
        lines.append('')
    return '\n'.join(lines)


def night_window_for(cfg):
    """The role's night quiet hours (already parsed by veil_agent), or the repository default."""
    autonomy = getattr(cfg, 'autonomy', None) or {}
    hours = autonomy.get('quiet_hours') if isinstance(autonomy, dict) else None
    if isinstance(hours, (list, tuple)) and len(hours) == 2:
        return hours
    return DEFAULT_NIGHT_WINDOW


def in_night_window(now, window):
    (start_h, start_m), (end_h, end_m) = window
    start = int(start_h) * 60 + int(start_m)
    end = int(end_h) * 60 + int(end_m)
    current = now.hour * 60 + now.minute
    if start == end:
        return True
    return start <= current < end if start < end else (current >= start or current < end)


def start_health_scheduler(hub, *, interval_seconds=600):
    """Start a low-overhead periodic read-only P5 scheduler daemon if enabled."""
    def _loop():
        last_run_at = 0.0
        while not getattr(hub, 'stopped', False):
            time.sleep(interval_seconds)
            if getattr(hub, 'stopped', False):
                continue
            chat = getattr(hub, 'chat', None)
            cfg = getattr(chat, 'cfg', None)
            store = getattr(chat, 'store', None)
            if chat is None or cfg is None or store is None:
                continue
            data_root = getattr(hub, 'data_root', None) or Path(store.root)
            try:
                settings = load_health_settings(data_root, cfg.id)
                schedule = settings.get('schedule')
                if not settings.get('enabled') or schedule == 'manual_only':
                    continue
                if settings.get('idle_only', True) and getattr(hub, 'busy', False):
                    continue
                if schedule == 'daily_night' and not in_night_window(datetime.now(), night_window_for(cfg)):
                    # daily_night means the night slot, not "whatever time the app happened to start".
                    continue
                now_ts = time.time()
                min_gap = 6 * 3600 if schedule == 'every_6h' else 20 * 3600
                latest = load_latest_report(data_root, cfg.id)
                if latest and latest.get('incomplete'):
                    # An unfinished report must not postpone the next attempt by a whole night.
                    min_gap = min(min_gap, 10 * 60)
                if latest and latest.get('scanned_at'):
                    dt = _parse_dt(latest['scanned_at'])
                    if dt and (datetime.now() - dt).total_seconds() < min_gap:
                        continue
                if now_ts - last_run_at < min_gap:
                    continue
                memory = getattr(chat, 'memory', None)
                auth = getattr(memory, 'brain_authority', None)
                db_path = Path(auth.db_path) if auth is not None and getattr(auth, 'db_path', None) else Path(data_root) / cfg.id / 'brain.db'
                experience = getattr(chat, 'experience', None)
                judge = getattr(memory, 'judge', None)
                configured_model = ''
                if auth is not None and hasattr(auth, 'embedding_model'):
                    try:
                        configured_model = auth.embedding_model() or ''
                    except Exception:
                        configured_model = ''
                run_health_scan(
                    data_root, cfg.id, db_path,
                    experience_status=experience.status() if experience else None,
                    judge_status=judge.status() if judge else None,
                    configured_embedding_model=configured_model,
                    timeout_s=5.0,
                )
                last_run_at = time.time()
            except Exception:
                continue

    thread = threading.Thread(target=_loop, name='veil-p5-health', daemon=True)
    thread.start()
    return thread

