"""Isolated S2 write-path verification for AB-A2 (relationship evidence chain) and AB-A3 (four-zone budget).

Runs latest production EventMemory (_capture_evidence / _stage_evidence_ledger) and LearnedContext
inside a temporary directory copy — never touches production data/peter or modifies production code.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import closing
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from veil.brain.event_memory import EventMemory
from veil.brain.learned_context import LearnedContext, _text_bytes


class _MiniStore:
    """Minimal read-only/isolated FileStore adapter for EventMemory._capture_evidence / _explicit_user_quotes."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def read(self, rel_path: str):
        path = self.root / rel_path
        if not path.is_file():
            return []
        rows = []
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows

    def read_tail(self, rel_path: str, n: int):
        return self.read(rel_path)[-n:]


class _NaiveEventLearnedContext(LearnedContext):
    """Deliberately unsafe comparison arm, never used by the product."""

    def _relationship_provenance(self, status, sources, claim_text='', data=None):
        result = super()._relationship_provenance(status, sources, claim_text, data)
        if not result['rebutted'] and any(ref.startswith('event:') for ref in sources):
            result.update(status=status, source='evidence', downgraded=False, evidence_state='valid')
        return result


def _seed_a2_evidence(db_path, *stage_ledgers):
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript("""
            CREATE TABLE events (event_id TEXT PRIMARY KEY, memory_id TEXT);
            CREATE TABLE event_ledger (id INTEGER PRIMARY KEY, ledger_seq INTEGER,
                                       kind TEXT, event_id TEXT, payload_json TEXT);
        """)
        for ledger, event_id in stage_ledgers:
            conn.execute('INSERT INTO events VALUES (?, NULL)', (event_id,))
            conn.executemany('INSERT INTO event_ledger VALUES (NULL, ?, ?, ?, ?)',
                             ((seq, kind, event_id, json.dumps(row))
                              for seq, (kind, row) in enumerate(ledger)))
        conn.commit()


class _FourZoneLearnedContext(LearnedContext):
    """Candidate AB-A3 rule: independent per-zone byte limits instead of one shared 4000B pool."""

    def __init__(self, data_root, character_id, settings, *, zone_caps):
        self.zone_caps = dict(zone_caps)
        # Set shared max_bytes to sum of zone caps so parent _validate/_content_bytes uses per-zone check.
        patched = dict(settings, max_bytes=sum(self.zone_caps.values()))
        super().__init__(data_root, character_id, patched)

    def _zone_bytes(self, data):
        rel = (data.get('relationship') or {}).get('items') or []
        ong = (data.get('ongoing') or {}).get('items') or []
        return {
            'facts': _text_bytes((data.get('facts') or {}).get('text') or ''),
            'growth': _text_bytes((data.get('growth') or {}).get('text') or ''),
            'relationship': sum(_text_bytes(i['text']) for i in rel
                                if isinstance(i, dict) and i.get('status') in ('confirmed', 'uncertain')),
            'ongoing': sum(_text_bytes(i['text']) for i in ong
                           if isinstance(i, dict) and i.get('status') in ('active', 'uncertain')),
        }

    def _check_budget(self, total, delta):
        # Checked per-zone in _validate and invoke wrappers below.
        return

    def _validate(self, data):
        super()._validate(data)
        self._assert_zone_caps(data)

    def _assert_zone_caps(self, data):
        sizes = self._zone_bytes(data)
        for zone, cap in self.zone_caps.items():
            if sizes.get(zone, 0) > cap:
                from veil.core.agent_types import UserError
                raise UserError(f'{zone} 超出独立上限 {sizes[zone]} / {cap} 字节。')

    def invoke(self, name, args):
        res, status, tool = super().invoke(name, args)
        if status == '成功':
            data = self._load()
            try:
                self._assert_zone_caps(data)
            except Exception as exc:
                return str(exc), '被拒绝', tool
        return res, status, tool


def verify_a2_s2_write_path():
    """Run latest S2 EventMemory evidence capture + LearnedContext promotion in an isolated temp dir.

    Covers all 5 required scenarios:
      1. User original quote explicitly supporting cognition.
      2. Multi-experience events backed by user chat quotes.
      3. User correction, override (Lan sovereignty) & delete.
      4. Peter's own guess / self-written story (no user chat evidence).
      5. Missing / unresolved / conflicting evidence.
    """
    with tempfile.TemporaryDirectory(prefix='s4-a2-') as tmp:
        root = Path(tmp)
        char_dir = root / 'peter'
        char_dir.mkdir(parents=True, exist_ok=True)
        now = datetime(2026, 11, 26, 12, 0, 0, tzinfo=timezone(timedelta(hours=8)))

        # Build isolated chat.jsonl & tools.jsonl with realistic S2 timestamps
        chat_rows = [
            {'role': 'user', 'message_id': 'msg-u1', 'time': (now - timedelta(minutes=10)).isoformat(),
             'content': '我加班累的时候只想安静待一会儿。'},
            {'role': 'assistant', 'message_id': 'msg-a1', 'time': (now - timedelta(minutes=9)).isoformat(),
             'content': '好，我记住了，你累的时候我只安静陪着你。'},
            {'role': 'user', 'message_id': 'msg-u2', 'time': (now - timedelta(minutes=5)).isoformat(),
             'content': '晚上十一点以后别催我复盘工作。'},
            {'role': 'assistant', 'message_id': 'msg-a2', 'time': (now - timedelta(minutes=4)).isoformat(),
             'content': '我猜她可能以后都不想聊工作了（这是我自己的猜测）。'},
        ]
        tool_rows = [
            {'id': f'tool-{i}', 'time': (now - timedelta(minutes=8 - i * 0.5)).isoformat(),
             'detail': f'run tool step {i}'}
            for i in range(10)  # 10 high-frequency tool rows that used to squeeze out chat pre-S2!
        ]
        (char_dir / 'chat.jsonl').write_text(
            '\n'.join(json.dumps(r, ensure_ascii=False) for r in chat_rows) + '\n', encoding='utf-8')
        (char_dir / 'tools.jsonl').write_text(
            '\n'.join(json.dumps(r, ensure_ascii=False) for r in tool_rows) + '\n', encoding='utf-8')

        # Instantiate real S2 EventMemory (lightweight adapter) to test _capture_evidence + _stage_evidence_ledger
        em = object.__new__(EventMemory)
        em.store = _MiniStore(root)
        em.cfg = SimpleNamespace(id='peter')
        em.settings = {'evidence_minutes': 30, 'evidence_items': 8, 'evidence_chars': 200}

        # Event 1: User quote explicitly backed (even with 10 high-freq tool calls!)
        stage1 = {'stage_id': 'st-1', 'at': now.isoformat(), 'source': {'message_ids': ['msg-u1']}}
        captured1 = em._capture_evidence(stage1, persist=False)
        quotes1 = em._explicit_user_quotes(stage1)
        ledger1, qrows1 = em._stage_evidence_ledger(stage1, 'e-user-quote', captured1, quotes1, now)

        # Event 2: Multi-experience event backed by msg-u2
        stage2 = {'stage_id': 'st-2', 'at': now.isoformat(), 'source': {'message_ids': ['msg-u1', 'msg-u2']}}
        captured2 = em._capture_evidence(stage2, persist=False)
        quotes2 = em._explicit_user_quotes(stage2)
        ledger2, qrows2 = em._stage_evidence_ledger(stage2, 'e-multi-exp', captured2, quotes2, now)

        # Event 3: Peter's self-written guess (only assistant message_id + tools, NO user quote)
        stage3 = {'stage_id': 'st-3', 'at': now.isoformat(), 'source': {'message_ids': ['msg-a2']}}
        quotes3 = em._explicit_user_quotes(stage3)  # S2 filters role=='user', so returns []!
        ledger3, qrows3 = em._stage_evidence_ledger(stage3, 'e-peter-guess', [], quotes3, now)

        # Event 4: Missing / unresolved message_id
        stage4 = {'stage_id': 'st-4', 'at': now.isoformat(), 'source': {'message_ids': ['msg-nonexistent']}}
        quotes4 = em._explicit_user_quotes(stage4)
        ledger4, qrows4 = em._stage_evidence_ledger(stage4, 'e-missing-ev', [], quotes4, now)

        # Verify S2 _capture_evidence preserved chat user messages despite 10 high-freq tool calls
        s2_chat_preserved = sum(1 for item in captured1 if item.get('kind') == 'chat' and item.get('role') == 'user')

        # Run all 3 arms across the 5 scenarios through real LearnedContext
        arm_outcomes = {}
        for arm_name in ('chat_only', 'strict_event_chat_evidence', 'event_pointer_naive'):
            arm_root = root / arm_name
            (arm_root / 'peter').mkdir(parents=True, exist_ok=True)
            shutil.copy2(char_dir / 'chat.jsonl', arm_root / 'peter' / 'chat.jsonl')
            settings = {'file': 'learned-context.json', 'max_bytes': 4000}
            if arm_name == 'chat_only':
                lc = LearnedContext(arm_root, 'peter', settings)
            elif arm_name == 'strict_event_chat_evidence':
                _seed_a2_evidence(arm_root / 'peter' / 'brain.db',
                                  (ledger1, 'e-user-quote'), (ledger2, 'e-multi-exp'),
                                  (ledger3, 'e-peter-guess'), (ledger4, 'e-missing-ev'))
                lc = LearnedContext(arm_root, 'peter', settings)
            else:
                lc = _NaiveEventLearnedContext(arm_root, 'peter', settings)

            cases = [
                ('S1_user_quote', '我加班累的时候只想安静待一会儿。', 'confirmed', ['event:e-user-quote'], True),
                ('S2_multi_exp', '晚上十一点以后别催我复盘工作。', 'confirmed', ['event:e-multi-exp'], True),
                ('S4_peter_guess', '猜测她以后完全不想聊工作', 'confirmed', ['event:e-peter-guess'], False),
                ('S5_missing_ev', '无有效证据的推断', 'confirmed', ['event:e-missing-ev', 'chat:nonexistent'], False),
            ]
            case_results = []
            valid_promoted, valid_total = 0, 0
            false_promoted, invalid_total = 0, 0
            for cid, text, req_status, srcs, should_promote in cases:
                msg, st, _ = lc.invoke('profile_append', {
                    'section': 'relationship', 'text': text, 'status': req_status, 'sources': srcs})
                items = lc.relationship_items()
                added = next((i for i in items if i['text'] == text), None)
                actual_status = added['status'] if added else 'missing'
                actual_source = added['source'] if added else ''
                if should_promote:
                    valid_total += 1
                    if actual_status == 'confirmed':
                        valid_promoted += 1
                else:
                    invalid_total += 1
                    if actual_status == 'confirmed':
                        false_promoted += 1
                case_results.append({
                    'case': cid, 'should_promote': should_promote,
                    'actual_status': actual_status, 'actual_source': actual_source,
                    'invoke_status': st,
                })

            # Scenario 3: User correction & deletion sovereignty (Lan edits/deletes via save_relationship)
            current = lc.relationship_items()
            target_item = next(i for i in current if i['text'] == '我加班累的时候只想安静待一会儿。')
            edited = [dict(i, text='加班累时先问我需不需要安静陪着', status='confirmed')
                      if i['id'] == target_item['id'] else i for i in current]
            # Also delete Peter's guess item
            edited = [i for i in edited if i['text'] != '猜测她以后完全不想聊工作']
            lc.save_relationship(edited, force=True)
            after_lan = lc.relationship_items()
            lan_item = next((i for i in after_lan if i['id'] == target_item['id']), None)
            guess_deleted = not any(i['text'] == '猜测她以后完全不想聊工作' for i in after_lan)
            lan_sovereignty_ok = bool(
                lan_item and lan_item['source'] == 'lan'
                and lan_item['text'] == '加班累时先问我需不需要安静陪着'
                and guess_deleted
            )

            arm_outcomes[arm_name] = {
                'valid_promoted': valid_promoted,
                'valid_total': valid_total,
                'false_promoted': false_promoted,
                'invalid_total': invalid_total,
                'circular_self_promotion_rate': round(false_promoted / max(1, invalid_total), 4),
                'lan_sovereignty_ok': lan_sovereignty_ok,
                'cases': case_results,
            }

        return {
            's2_capture_chat_user_preserved': s2_chat_preserved,
            's2_quotes_generated': {'e-user-quote': len(qrows1), 'e-multi-exp': len(qrows2),
                                    'e-peter-guess': len(qrows3), 'e-missing-ev': len(qrows4)},
            'arms': arm_outcomes,
        }


def verify_a3_real_writes(learned_context_path: Path, versions_path: Path | None, zone_caps: dict):
    """Run real LearnedContext writes & migration check in an isolated copy of learned-context.json."""
    with tempfile.TemporaryDirectory(prefix='s4-a3-') as tmp:
        root = Path(tmp)
        results = {}
        for arm_name in ('shared_4000B', 'four_zone_independent'):
            arm_root = root / arm_name
            char_dir = arm_root / 'peter'
            char_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(learned_context_path, char_dir / 'learned-context.json')
            if versions_path and Path(versions_path).is_file():
                shutil.copy2(versions_path, char_dir / 'learned-context.versions.jsonl')
            settings = {'file': 'learned-context.json', 'max_bytes': 4000}
            if arm_name == 'shared_4000B':
                lc = LearnedContext(arm_root, 'peter', settings)
            else:
                lc = _FourZoneLearnedContext(arm_root, 'peter', settings, zone_caps=zone_caps)

            # 1. Verify migration / legacy content preservation on load
            before_data = lc.read()
            before_facts = before_data['facts']['text']
            before_growth = before_data['growth']['text']
            before_rel_count = len((before_data.get('relationship') or {}).get('items') or [])
            before_head_bytes = len((lc.context() or '').encode('utf-8'))

            # 2. Execute real S3 target writes through LearnedContext.invoke()
            writes = [
                ('facts+120B', 'profile_append', {'section': 'facts', 'text': '新增长期偏好记录：' + '偏好细节' * 8}),
                ('growth+300B', 'profile_append', {'section': 'growth', 'text': '新增认知成长经验：' + '经验总结复盘' * 15}),
                ('relationship+900B', 'profile_append', {
                    'section': 'relationship', 'status': 'uncertain', 'sources': [],
                    'text': '新增稳定关系认知约定：' + '共同约定与沟通边界记录' * 25}),
                ('ongoing+300B', 'ongoing_update', {
                    'action': 'add', 'sources': [],
                    'text': '新增进行中事项跟进：' + '阶段性推进细节' * 12}),
            ]
            write_log = []
            rejected = 0
            for label, tool, args in writes:
                msg, st, _ = lc.invoke(tool, args)
                is_rej = (st != '成功')
                if is_rej:
                    rejected += 1
                write_log.append({'write': label, 'status': st, 'rejected': is_rej})

            after_data = lc.read()
            legacy_preserved = (
                before_facts in after_data['facts']['text']
                and before_growth in after_data['growth']['text']
                and len((after_data.get('relationship') or {}).get('items') or []) >= before_rel_count
            )
            after_head_bytes = len((lc.context() or '').encode('utf-8'))
            after_rel_bytes = sum(
                _text_bytes(i['text']) for i in (after_data.get('relationship') or {}).get('items') or []
                if i.get('status') in ('confirmed', 'uncertain'))
            after_ong_bytes = len((lc.dynamic_context() or '').encode('utf-8'))

            results[arm_name] = {
                'legacy_content_preserved': legacy_preserved,
                'writes': len(writes),
                'rejected_writes': rejected,
                'write_rejection_rate': round(rejected / len(writes), 4),
                'head_bytes_before': before_head_bytes,
                'head_bytes_after': after_head_bytes,
                'head_delta_bytes': after_head_bytes - before_head_bytes,
                'head_delta_tokens_est': round((after_head_bytes - before_head_bytes) * 0.30, 1),
                'relationship_active_bytes_after': after_rel_bytes,
                'ongoing_rendered_bytes_after': after_ong_bytes,
                'write_log': write_log,
            }
        return results
