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
    p = Path(path)
    if not p.is_file():
        return []
    rows = []
    try:
        with p.open(encoding='utf-8', errors='replace') as fh:
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
    return rows[-limit:] if limit and len(rows) > limit else rows


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
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


def run_health_scan(root, role_id, db_path, *, from_date='', to_date='',
                    experience_status=None, judge_status=None,
                    learned_status=None, timeout_s=5.0, vector_sample_limit=300,
                    persist=True):
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
        db = Path(db_path)
        checks = []
        timed_out = False
        # Defined before the guarded block so a timeout before loading can still build a report.
        event_total = 0
        event_rows = []
        events_sampled = False
        stats = {
            'active_memories': 0,
            'pinned_memories': 0,
            'archived_memories': 0,
            'tombstones': 0,
            'events_total': 0,
            'event_stages_total': 0,
            'versions_total': 0,
            'vectors_total': 0,
        }

        if not db.is_file():
            report = {
                'schema_version': 1,
                'scanned_at': _now_iso(),
                'duration_ms': 0,
                'read_only': True,
                'from_date': from_date,
                'to_date': to_date,
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
            sidecar_rows = conn.execute('SELECT memory_id, locked, links_json, merged_into FROM sidecar_state').fetchall() if 'sidecar_state' in tables else []
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
                'SELECT memory_id, vector FROM vectors WHERE vector IS NOT NULL LIMIT ?',
                (max(1, int(vector_sample_limit)) * 2,)).fetchall() if 'vectors' in tables else []
            tomb_rows = conn.execute('SELECT memory_id, deleted_at FROM tombstones').fetchall() if 'tombstones' in tables else []

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
            evt_mem_ids = {row[0] for row in conn.execute(
                'SELECT memory_id FROM events WHERE memory_id IS NOT NULL')} if 'events' in tables else set()
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
                    # A malformed BLOB used to abort the whole scan; record it for H-7 and keep going.
                    if len(blob) != 1024 * 4:
                        vector_decode_issues.append({
                            'memId': mid,
                            'label': _safe_title(active_title_map.get(mid, mid), mid),
                            'note': f'向量字节数 {len(blob)}，与 1024 维（4096 字节）不符',
                        })
                        continue
                    arr = array.array('f')
                    arr.frombytes(blob)
                    sample_vecs.append((mid, arr))
                    if len(sample_vecs) >= vector_sample_limit:
                        break
                for i in range(len(sample_vecs)):
                    if time.monotonic() >= deadline:
                        break
                    id_a, va = sample_vecs[i]
                    for j in range(i + 1, len(sample_vecs)):
                        id_b, vb = sample_vecs[j]
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

            # H-3: Orphan & dangling links
            _guard(deadline)
            orphan_evidences = []
            for r in sidecar_rows:
                mid = r['memory_id']
                if mid not in live_ids:
                    continue
                links = _json_load(r['links_json'], [])
                if isinstance(links, list):
                    for target in links:
                        if target not in live_ids:
                            orphan_evidences.append({'memId': mid, 'label': f"{mid} → {target}", 'note': '关联目标不存在或已删除'})
                merged_into = r['merged_into']
                if merged_into and (merged_into == mid or merged_into not in live_ids):
                    orphan_evidences.append({'memId': mid, 'label': f"{mid} (merged_into={merged_into})", 'note': '合并目标无效或自环'})
            checks.append({
                'id': 'H-3',
                'title': '孤立记录与失效关联检查',
                'severity': 'warn' if orphan_evidences else 'good',
                'status_label': f"{len(orphan_evidences)} 处悬空关联" if orphan_evidences else '引用完整',
                'impact': f"检测到 {len(orphan_evidences)} 条 sidecar 关联或合并指向不存在/已删除的记忆 ID。" if orphan_evidences else '所有 sidecar 链接与合并指针均指向有效记忆。',
                'object': 'sidecar_state.links_json / merged_into vs memories.id',
                'basis': '精确外键与拓扑环路校验',
                'source': 'brain.db (mode=ro)',
                'confidence': '高可信（精确引用核验）',
                'evidences': orphan_evidences[:15],
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

            # H-7: Retrieval index consistency
            _guard(deadline)
            vec_by_id = {vr['memory_id']: vr for vr in vector_rows}
            vec_issues = list(vector_decode_issues)
            for i, r in enumerate(active_mems):
                if i % 500 == 0:
                    _guard(deadline)
                mid = r['id']
                vr = vec_by_id.get(mid)
                vstate = r['vector_state'] or 'ready'
                if vstate != 'ready':
                    vec_issues.append({'memId': mid, 'label': _safe_title(r['title'], mid), 'note': f"vector_state={vstate}（关键词检索可用）"})
                elif vr is None:
                    vec_issues.append({'memId': mid, 'label': _safe_title(r['title'], mid), 'note': '状态为 ready 但缺少向量行'})
                else:
                    if vr['dim'] != 1024:
                        vec_issues.append({'memId': mid, 'label': _safe_title(r['title'], mid), 'note': f"向量维度 {vr['dim']} ≠ 1024"})
                    elif vr['bytes'] is not None and vr['bytes'] != vr['dim'] * 4:
                        vec_issues.append({'memId': mid, 'label': _safe_title(r['title'], mid),
                                           'note': f"向量字节数 {vr['bytes']} 与维度 {vr['dim']} 不符"})
                    if vr['content_hash'] and r['body_sha256'] and vr['content_hash'] != r['body_sha256']:
                        vec_issues.append({'memId': mid, 'label': _safe_title(r['title'], mid), 'note': '正文修改后向量 content_hash 未同步'})
            residual_tomb_vecs = [vr['memory_id'] for vr in vector_rows if vr['memory_id'] not in live_ids]
            for tid in residual_tomb_vecs[:10]:
                vec_issues.append({'label': f"残留向量 {tid}", 'note': '对应记忆已删除或成为墓碑'})
            checks.append({
                'id': 'H-7',
                'title': '检索索引与记忆数据一致性',
                'severity': 'warn' if vec_issues else 'good',
                'status_label': f"{len(vec_issues)} 条索引待同步" if vec_issues else '索引 100% 一致',
                'impact': (f"发现 {len(vec_issues)} 项向量索引待补齐、哈希滞后或墓碑残留（仅提示，不自动重算付费向量）。"
                           if vec_issues else f"全部 {len(active_mems)} 条活跃记忆向量维度与内容哈希一致，无墓碑残留。"),
                'object': 'memories(vector_state, body_sha256) vs vectors(dim, content_hash)',
                'basis': '逐条外键与哈希对账',
                'source': 'brain.db (mode=ro)',
                'confidence': '高可信（确定性对账）',
                'evidences': vec_issues[:20],
            })
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
            })

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

        report = {
            'schema_version': 1,
            'scanned_at': _now_iso(),
            'duration_ms': elapsed_ms,
            'read_only': True,
            'from_date': from_date,
            'to_date': to_date,
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
    lines = [
        '# VEIL Brain 记忆健康检查报告（严格只读脱敏摘要）',
        f"- 检查时间：{report.get('scanned_at', '未知')}",
        f"- 耗时：{report.get('duration_ms', 0)} ms（零写操作，零外部 Token 消耗）",
        f"- 整体状态：{s.get('status_label', '未知')}（正常 {s.get('good_count', 0)} / 关注 {s.get('warn_count', 0)}）",
        '',
        '## 检查明细',
    ]
    for chk in report.get('checks', []):
        lines.append(f"### [{chk.get('id')}] {chk.get('title')} — {chk.get('status_label')}")
        lines.append(f"- 影响范围：{chk.get('impact')}")
        lines.append(f"- 检查对象：{chk.get('object')} | 可信度：{chk.get('confidence')}")
        for ev in chk.get('evidences', [])[:10]:
            lines.append(f"  - {ev.get('label')}: {ev.get('note')}")
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
                run_health_scan(
                    data_root, cfg.id, db_path,
                    experience_status=experience.status() if experience else None,
                    judge_status=judge.status() if judge else None,
                    timeout_s=5.0,
                )
                last_run_at = time.time()
            except Exception:
                continue

    thread = threading.Thread(target=_loop, name='veil-p5-health', daemon=True)
    thread.start()
    return thread

