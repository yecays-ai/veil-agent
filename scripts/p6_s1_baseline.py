#!/usr/bin/env python3
"""P6-S1 Baseline-1: isolated, reproducible, zero-cost baseline of the fixed recall chain.

Every input is fictional, every model is a local fake and nothing outside the temporary
directory is read or written: no real chat, memory, prompt, key or paid API call is touched.

It records what P6-S4 can reuse directly:
  1. judge card completeness on historical CRLF Markdown (G-A04);
  2. embedding calls per turn: the old two-call path versus the shared one-call path (G-A05);
  3. active-search date filter and configured weights (G-A09);
  4. embedding dimension / model-identity compatibility and pending-vector recovery (G-A14);
  5. recall judge diagnostics: normal empty, embedding failure, model timeout, degraded pool (G-A12);
  6. continuity: follow-up carry and cooldown after the dead branches were removed (G-A08/G-B01).

Usage:
    python scripts/p6_s1_baseline.py [--out baseline-1.json]

Re-run it on the merged S1 commit and diff the JSON to reproduce the baseline.
"""
import argparse
import json
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import brain_retrieval  # noqa: E402
import brain_writer  # noqa: E402
import veil_brain  # noqa: E402
from brain_read import BrainRead  # noqa: E402
from memory_agent import Memory  # noqa: E402
from recall_judge import DEFAULTS as JUDGE_DEFAULTS, card_text, split_card  # noqa: E402
from veil_agent import Budget, Character, Store  # noqa: E402

CRLF = ('---\r\nid: {id}\r\ntitle: {title}\r\nimportance: {importance}\r\n---\r\n'
        '{opening} ' + ('middle of the fictional memory. ' * 8) + '{ending}\r\n'
        '关键词：{keyword}\r\n')
LF = CRLF.replace('\r\n', '\n')


class CountingEmbedder:
    def __init__(self, vector, model='baseline-fake-embedding'):
        self.vector, self.model, self.calls = list(vector), model, 0

    def embed(self, _text):
        self.calls += 1
        return list(self.vector)


class TimedJudgeModel:
    def __init__(self, delay=0.0, answer='{"ids": []}', error=None):
        self.delay, self.answer, self.error, self.calls = delay, answer, error, 0

    def __call__(self, _messages, **_kwargs):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        return (self.answer, 900, 7, 0)


def old_card_text(raw):
    """The pre-P6-S1 card split, kept here only to measure the documented before/after."""
    if not isinstance(raw, str):
        return ''
    head, _, body = raw.partition('\n---\n')
    import re
    title = re.search(r'^title:\s*(.*)$', head, re.M) or re.search(r'^name:\s*(.*)$', head, re.M)
    tags, inside = [], False
    for line in head.splitlines():
        if line.startswith('tags:'):
            inside = True
            continue
        if inside:
            if line.startswith('- '):
                tags.append(line[2:].strip().strip('\'"'))
            else:
                inside = False
    keywords = re.search(r'(?:关键词|检索词)[：:](.*)', body)
    plain = ' '.join(body.split())
    opening = plain[:150]
    closing = ''
    if plain:
        tail = plain[-130:]
        if len(tail) > 24 and tail not in opening:
            closing = '…ending: ' + tail
    parts = [(title.group(1).strip().strip('\'"')[:60] if title else ''),
             (keywords.group(1).strip()[:60] if keywords else ''),
             ('…opening: ' + opening[:90]) if opening else '',
             closing[:130],
             ' '.join(tags)[:30]]
    return ' | '.join(part for part in parts if part)[:300]


class Baseline:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'data')
        self.db = self.store.root / 'fake' / 'brain.db'
        self.db.parent.mkdir(parents=True)
        connection = sqlite3.connect(str(self.db))
        connection.executescript(veil_brain.SCHEMA)
        brain_writer.ensure_schema(connection)
        connection.close()
        provider = {'model': 'fake', 'max_input_tokens': 20000, 'input_cny_per_million': 1,
                    'output_cny_per_million': 2, 'cached_input_cny_per_million': 1}
        self.settings = {'model': 'baseline-fake-embedding', 'input_cny_per_million': '0',
                         'mode': 'native', 'base_url': 'https://example.invalid/v1',
                         'brain': {'write_authority': True, 'shadow': False},
                         'retrieval': {'backend': 'brain', 'weight_vector': 0.65,
                                       'weight_keyword': 0.35}}
        self.cfg = Character('fake', 'Example', 'persona', 20, 1024, provider, 'fake', self.settings)
        self.memory = Memory(self.cfg, self.store, Budget(self.store), SimpleNamespace(call=None))
        self.memory._embedder = CountingEmbedder([0.4] * 1024)
        self.results = {}
        self.checks = []
        self.notes = []

    def close(self):
        try:
            self.memory.__exit__(None, None, None)
        except Exception:
            pass
        try:
            self.temp.cleanup()
        except OSError:
            # Windows may still hold the SQLite file for a moment; the temporary directory is
            # disposable and the JSON report is complete, so this must not fail the run.
            pass

    def check(self, name, ok, detail):
        self.checks.append({'name': name, 'ok': bool(ok), 'detail': detail})

    def create(self, body, title='', created='', embedder=None, raw=None, connection=None):
        conn = connection or sqlite3.connect(str(self.db))
        brain_writer.ensure_schema(conn)
        result = brain_writer.create_memory(
            conn, {'body': body, 'title': title or body[:40], 'created': created or brain_writer.now()},
            embedder=(embedder or self.memory._embedder).embed
            if hasattr(embedder or self.memory._embedder, 'embed')
            else (embedder or self.memory._embedder))
        if raw is not None:
            conn.execute('UPDATE memories SET raw_markdown=? WHERE id=?', (raw, result['memory_id']))
            conn.commit()
        if connection is None:
            conn.close()
        return result

    # 1. CRLF card completeness -------------------------------------------------
    def card_completeness(self):
        total = old_complete = new_complete = 0
        old_chars = new_chars = 0
        for index in range(20):
            is_crlf = index % 20 < 14          # audit: 154/225 (68.4%) stored with CRLF
            title, opening, ending, keyword = ('Fictional %d' % index, 'opening line %d' % index,
                                               'ending detail %d' % index, 'keyword-%d' % index)
            raw = (CRLF if is_crlf else LF).format(
                id='m%d' % index, title=title, importance=5, opening=opening,
                ending=ending, keyword=keyword)
            old, new = old_card_text(raw), card_text(raw)
            total += 1
            old_complete += bool(old and 'opening line' in old)
            new_complete += bool(new and 'opening line' in new and 'ending detail' in new)
            old_chars += len(old)
            new_chars += len(new)
        self.results['card_completeness'] = {
            'samples': total, 'crlf_samples': 14,
            'old_body_visible': old_complete, 'new_body_visible': new_complete,
            'old_avg_chars': round(old_chars / total, 1),
            'new_avg_chars': round(new_chars / total, 1),
            'audit_reference': 'P6-1: 154/225 CRLF, card mean 289.5 -> 116.5 chars; the same '
                               '20-sample measure now shows every card with opening and ending'}
        self.check('card: every CRLF card carries opening and ending', new_complete == total,
                   '%d/%d' % (new_complete, total))
        self.check('card: old split loses the body on every CRLF sample',
                   old_complete == total - 14, '%d/%d' % (old_complete, total))

    # 2. one embedding per turn ------------------------------------------------
    def embedding_calls(self):
        self.create('蓝色风筝挂在墙上', '风筝')
        judge_embedder = CountingEmbedder([0.4] * 1024)
        judge = self.memory.attach_recall_judge(
            self.cfg, TimedJudgeModel(), {'timeout_seconds': 1.0, 'enabled': True})
        judge.make_embedder = lambda: judge_embedder
        memory_embedder = self.memory._embedder
        memory_embedder.calls = 0
        self.memory.context('风筝')
        shared = judge_embedder.calls + memory_embedder.calls
        retrieval = dict(self.memory.last_recall_debug.get('retrieval') or {})
        # The old path: the judge prefetch and an independent retrieval embed were two calls.
        judge_embedder.calls = memory_embedder.calls = 0
        judge.prefetch('风筝')
        self.memory.search_candidates('风筝', 8)
        old_path = judge_embedder.calls + memory_embedder.calls
        self.memory.judge.close()
        self.memory.judge = None
        self.results['embedding_calls'] = {
            'current_shared_per_turn': shared, 'old_independent_path': old_path,
            'shared_with_judge': retrieval.get('embedding') == 'shared',
            'audit_reference': 'P6-1: judge.prefetch() and search_candidates() each embedded '
                               'the same query (2 calls / 100% extra embedding cost)'}
        self.check('embedding: exactly one call per turn', shared == 1, shared)
        self.check('embedding: old path had two', old_path == 2, old_path)

    # 3. date filter and configured weights -----------------------------------
    def active_search(self):
        self.create('十一月一号的旧事', '一号', created='2026-11-01T10:00:00+08:00')
        late = self.create('十一月二十号的新事', '二十号', created='2026-11-20T10:00:00+08:00')['memory_id']
        authority = self.memory.brain_authority
        text = authority.breath_search('十一月', 10, self.memory._embedder,
                                       date_from='2026-11-10', date_to='2026-11-30')
        in_range = late in text and '一号' not in text
        rejected = False
        try:
            authority.breath_search('十一月', 10, self.memory._embedder, date_from='2026-13-40')
        except ValueError:
            rejected = True
        weights = dict(authority.retrieval_settings())
        self.results['active_search'] = {
            'date_filter_inclusive': in_range, 'invalid_date_rejected': rejected,
            'weights': weights,
            'audit_reference': 'P6-1: breath_search passed date_from/date_to that '
                               'brain_authority.breath_search() dropped, and hard-coded 0.65/0.35'}
        self.check('active search: date range filters hits', in_range, text[:80])
        self.check('active search: invalid date rejected', rejected, '')
        self.check('active search: configured weights are readable',
                   weights['weight_vector'] == 0.65 and weights['weight_keyword'] == 0.35, weights)

    # 4. dimension / model compatibility and vector recovery ------------------
    def vector_compatibility(self):
        three = CountingEmbedder([1.0, 0.0, 0.0], 'baseline-fake-embedding')
        self.create('三维向量记忆', '三维', embedder=three)
        self.create('一零二四维向量记忆', '一零二四', embedder=self.memory._embedder)
        simple = CountingEmbedder([1.0, 0.0, 0.0], 'baseline-fake-embedding')
        _, meta = brain_retrieval.search_text(self.db, '三维', embedder=simple)
        mismatch_meta = brain_retrieval.search_text(
            self.db, '三维', embedder=simple, model='another-embedding-model')[1]
        wrong_model = CountingEmbedder([1.0, 0.0, 0.0], 'another-embedding-model')
        refused = self.create('另一模型的向量', '拒绝', embedder=wrong_model)
        # A memory left pending (vector missing) recovers through the retained retry path.
        pending_id = self.create('待索引记忆', '待索引', embedder=lambda _b: None)['memory_id']
        connection = sqlite3.connect(str(self.db))
        try:
            recovered = brain_writer.retry_pending_vectors(
                connection, CountingEmbedder([0.2] * 1024, 'baseline-fake-embedding').embed)
        finally:
            connection.close()
        self.results['vector_compatibility'] = {
            'mixed_dim_skipped': meta['skipped_dim'] >= 1,
            'mixed_dim_skipped_count': meta['skipped_dim'],
            'three_dim_found': meta['count'] >= 1,
            'model_mismatch_degraded_to_keyword': mismatch_meta['model_mismatch']
            and mismatch_meta['keyword_only'],
            'writer_kept_pending_on_model_mismatch': refused['vector_state'] == 'pending',
            'pending_vectors_recovered': recovered['saved'] >= 1 and bool(pending_id),
            'retry_result': recovered,
            'audit_reference': 'P6-1/P6-4 G-A14: brain_retrieval.py and brain_writer.py hard-coded '
                               'dim == 1024; a second embedding model could silently mix vectors'}
        self.check('vector: mixed dimensions are skipped, not crashed', meta['skipped_dim'] >= 1,
                   meta['skipped_dim'])
        self.check('vector: a different embedding model degrades to keyword',
                   mismatch_meta['model_mismatch'] and mismatch_meta['keyword_only'], '')
        self.check('vector: writer refuses another model vector',
                   refused['vector_state'] == 'pending', refused.get('vector_rejected'))
        self.check('vector: pending vectors still recover', recovered['saved'] >= 1, recovered)

    # 4b. embedding-model switch: no paid retry, explicit reindex restores recovery ------------
    def model_switch(self):
        """After a deliberate embedding-model change the old vectors are incompatible.

        The baseline locks in: pending retries make no paid call and write nothing while the
        recorded model differs, and the explicit reindex restores the normal pending recovery.
        """
        created = self.create('切换模型前的记忆', '切换',
                              embedder=CountingEmbedder([0.2] * 1024, 'baseline-fake-embedding').embed)
        connection = sqlite3.connect(str(self.db))
        try:
            connection.execute("UPDATE memories SET vector_state='pending' WHERE id=?",
                               (created['memory_id'],))
            connection.execute('DELETE FROM vectors')
            connection.commit()
            new_model = CountingEmbedder([0.2] * 1024, 'new-baseline-model')
            blocked = brain_writer.retry_pending_vectors(connection, new_model.embed)
            reindex = brain_writer.prepare_embedding_reindex(connection, 'new-baseline-model')
            recovered = brain_writer.retry_pending_vectors(connection, new_model.embed)
        finally:
            connection.close()
        self.results['model_switch'] = {
            'blocked': blocked,
            'embedder_calls_before_reindex': new_model.calls - recovered['saved'],
            'reindex': reindex,
            'recovered': recovered,
            'audit_reference': 'P6-S1 post-merge review: the first S1 write stored the model identity '
                               'but retried pending vectors before checking it, so a model switch '
                               'could pay for rejected embeddings on every retry',
        }
        self.check('model switch: a paid retry is blocked before the explicit reindex',
                   blocked.get('blocked') == 'embedding_model_mismatch' and blocked['saved'] == 0,
                   blocked)
        self.check('model switch: the explicit reindex restores pending recovery',
                   reindex['memories_marked_pending'] >= 1 and recovered['saved'] >= 1, recovered)

    # 5. judge diagnostics ---------------------------------------------------
    def judge_diagnostics(self):
        def run(judge_model, judge_embedder=None, wake=False, timeout=0.2):
            memory = Memory(self.cfg, self.store, Budget(self.store), SimpleNamespace(call=None))
            memory._embedder = CountingEmbedder([0.4] * 1024)
            self.create('蓝色风筝挂在墙上', '风筝')
            judge = memory.attach_recall_judge(
                self.cfg, judge_model,
                {**JUDGE_DEFAULTS, 'timeout_seconds': timeout, 'enabled': True})
            judge.make_embedder = lambda: (judge_embedder or CountingEmbedder([0.4] * 1024))
            try:
                memory.context('风筝', wake=wake)
                debug = dict(memory.last_recall_debug)
            finally:
                memory.__exit__(None, None, None)
            return debug

        normal = run(TimedJudgeModel())
        slow = run(TimedJudgeModel(delay=0.8), timeout=0.1)
        bad_embedder = CountingEmbedder([0.4] * 1024)
        bad_embedder.embed = lambda _text: (_ for _ in ()).throw(RuntimeError('embedding down'))
        broken = run(TimedJudgeModel(), judge_embedder=bad_embedder)
        wake = run(TimedJudgeModel(delay=0.8), wake=True, timeout=0.1)
        self.results['judge_diagnostics'] = {
            'normal_empty': {'status': normal['judge'].get('status'),
                             'degraded': normal.get('degraded') or ''},
            'model_timeout': {'status': slow['judge'].get('status'),
                              'reason': slow['judge'].get('reason'),
                              'detail': slow['judge'].get('detail'),
                              'embedding_seconds': slow['judge'].get('embedding_seconds'),
                              'model_seconds': slow['judge'].get('model_seconds'),
                              'degraded': slow.get('degraded') or ''},
            'embedding_failure': {'status': broken['judge'].get('status'),
                                   'reason': broken['judge'].get('reason'),
                                   'detail': broken['judge'].get('detail'),
                                   'retrieval_fallback': broken.get('retrieval', {}).get('fallback'),
                                   'degraded': broken.get('degraded') or ''},
            'wake_timeout_fallback': {'status': wake['judge'].get('status'),
                                       'degraded': wake.get('degraded') or ''},
            'audit_reference': 'P6-1: 5/5 Brain-era judge failures were timeout:model with one '
                               'shared 4s deadline; embedding/model now have separate clocks'}
        self.check('judge: normal empty is not marked degraded',
                   normal['judge'].get('status') == 'empty' and not normal.get('degraded'),
                   normal['judge'].get('status'))
        self.check('judge: a slow model is timeout:model',
                   slow['judge'].get('status') == 'failed'
                   and slow['judge'].get('reason') == 'timeout'
                   and slow['judge'].get('detail') == 'model', slow['judge'])
        self.check('judge: an embedding failure is its own degraded stage',
                   broken['judge'].get('status') == 'failed'
                   and broken['judge'].get('reason') == 'embedding_error'
                   and broken.get('degraded'), broken['judge'])
        wake_degraded = wake.get('degraded')
        self.check('judge: wake degrades explicitly to the pool',
                   isinstance(wake_degraded, dict) and wake_degraded.get('fallback') == 'pool',
                   wake_degraded)

    # 6. continuity ----------------------------------------------------------
    def continuity(self):
        memory = Memory(self.cfg, self.store, Budget(self.store), SimpleNamespace(call=None))
        memory._embedder = CountingEmbedder([0.4] * 1024)
        self.create('花园里一起种下的玫瑰', '花园', created='2026-10-01T10:00:00+08:00')
        first = memory.context('花园玫瑰')
        first_refs = list(memory.last_context_refs)
        turns = [t for t in (memory.rotation.last or {}).values()] if hasattr(memory.rotation, 'last') else []
        memory.last_injected_refs = list(first_refs)
        memory.context('后来呢', recent=[{'role': 'user', 'content': '还记得花园那次吗'},
                                        {'role': 'assistant', 'content': '记得'}])
        carry = list(memory.last_recall_debug.get('carried') or [])
        second = memory.context('花园玫瑰')
        cooled = [item for item in memory.last_recall_debug.get('dropped') or []
                  if item.get('reason') == 'cooldown']
        memory.__exit__(None, None, None)
        self.results['continuity'] = {
            'first_turn_refs': first_refs, 'followup_carried': carry,
            'cooldown_dropped_on_repeat': cooled,
            'reuse_recall_removed': not hasattr(Memory, 'reuse_recall'),
            'dead_drift_removed': not hasattr(brain_retrieval, 'DRIFT_MARK'),
            'audit_reference': 'P6-1 G-B01/G-A08: reuse_recall() matched 0/220 and the drift '
                               'len(results)<3 branch never fired; both removed, continuity kept'}
        self.check('continuity: the follow-up carries the previous turn memory', bool(carry), carry)
        self.check('continuity: a repeated turn keeps cooldown', bool(cooled), cooled)
        self.check('continuity: removed dead branches leave continuity intact',
                   bool(first_refs) and not hasattr(Memory, 'reuse_recall')
                   and not hasattr(brain_retrieval, 'DRIFT_MARK'), first_refs)

    def run(self):
        for name in ('card_completeness', 'embedding_calls', 'active_search',
                     'vector_compatibility', 'judge_diagnostics', 'continuity',
                     'model_switch'):
            getattr(self, name)()
        try:
            commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT,
                                             text=True).strip()
        except Exception:
            commit = ''
        return {
            'baseline': 'P6-S1 Baseline-1',
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'code_commit': commit,
            'python': platform.python_version(),
            'environment': 'isolated tempdir, fictional data, fake embedder/judge model, no network',
            'configuration': {
                'embedding_model': self.settings['model'],
                'retrieval': dict(self.settings['retrieval']),
                'judge': {'timeout_seconds': 1.0, 'source': 'recall_judge.DEFAULTS',
                          'defaults': dict(JUDGE_DEFAULTS)},
            },
            'results': self.results,
            'checks': self.checks,
            'all_checks_pass': all(item['ok'] for item in self.checks),
            'notes': self.notes,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', help='write the baseline JSON here instead of stdout')
    args = parser.parse_args()
    baseline = Baseline()
    try:
        payload = baseline.run()
    finally:
        baseline.close()
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + '\n', encoding='utf-8')
        failed = [item for item in payload['checks'] if not item['ok']]
        print('Baseline-1 written to %s; checks %d/%d pass'
              % (args.out, len(payload['checks']) - len(failed), len(payload['checks'])))
        if failed:
            print('FAILED CHECKS:', json.dumps(failed, ensure_ascii=False))
            return 1
        return 0
    print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())