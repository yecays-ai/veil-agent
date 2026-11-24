"""P2-B: Evaluation runner for the 30-case Ground Truth dataset.

Validates relevant recall, missed recall, and false recall against human ground truth,
not relying on past Ombre injection candidates as ground truth.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import brain_retrieval
from dotenv import dotenv_values
from recall_relevance import QueryEmbedder


def run_eval(db_path='data/peter/brain.db', dataset_path=None, env_file=None):
    if dataset_path is None:
        dataset_path = ROOT / 'docs' / 'tickets' / 'p2' / 'evidence' / 'golden_truth_30.json'
    if env_file is None:
        env_file = ROOT / '.env'

    with open(dataset_path, encoding='utf-8') as f:
        cases = json.load(f)

    values = dotenv_values(str(env_file), interpolate=False, encoding='utf-8-sig')
    embed_key = (values.get('EMBED_API_KEY') or '').strip()
    embedder = None
    if embed_key:
        try:
            embedder = QueryEmbedder('https://api.siliconflow.cn/v1', 'BAAI/bge-m3', embed_key)
        except Exception as e:
            print(f'Warning: embedder init failed ({e}), falling back to BM25 keyword')

    settings = brain_retrieval.DEFAULTS
    index = brain_retrieval.get_index(str(db_path), settings)

    results = []
    positive_hits = 0
    missed_recall = 0
    false_positives = 0
    true_negatives = 0

    SCORE_THRESHOLD = 0.35  # Threshold below which negative cases are safely considered unretrieved

    for case in cases:
        cid = case['id']
        query = case['query']
        expected = set(case.get('expected_ids') or [])
        has_answer = case.get('has_answer', True)

        t0 = time.monotonic()
        try:
            hits = index.search(query, limit=5, embedder=embedder)
        except Exception as exc:
            results.append({'id': cid, 'query': query, 'error': str(exc)})
            continue
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)

        retrieved_ids = [item['id'] for item in hits]
        top_score = hits[0]['score'] if hits else 0.0

        if has_answer:
            matched = list(expected & set(retrieved_ids))
            hit = len(matched) > 0
            if hit:
                positive_hits += 1
            else:
                missed_recall += 1
            results.append({
                'id': cid,
                'query': query,
                'topic': case.get('topic'),
                'has_answer': True,
                'expected_ids': list(expected),
                'retrieved_ids': retrieved_ids,
                'matched_ids': matched,
                'hit': hit,
                'top_score': round(top_score, 4),
                'elapsed_ms': elapsed_ms
            })
        else:
            # Negative query with no answer expected
            # If top_score >= SCORE_THRESHOLD, consider it a potential false recall
            is_false_alarm = (top_score >= SCORE_THRESHOLD and len(retrieved_ids) > 0)
            if is_false_alarm:
                false_positives += 1
            else:
                true_negatives += 1
            results.append({
                'id': cid,
                'query': query,
                'topic': case.get('topic'),
                'has_answer': False,
                'expected_ids': [],
                'retrieved_ids': retrieved_ids,
                'top_score': round(top_score, 4),
                'false_positive': is_false_alarm,
                'correct_rejection': not is_false_alarm,
                'elapsed_ms': elapsed_ms
            })

    total_pos = sum(1 for c in cases if c.get('has_answer', True))
    total_neg = sum(1 for c in cases if not c.get('has_answer', True))

    summary = {
        'total_cases': len(cases),
        'positive_cases': total_pos,
        'negative_cases': total_neg,
        'positive_hits': positive_hits,
        'missed_recall_count': missed_recall,
        'recall_rate': round(positive_hits / total_pos, 4) if total_pos else 0,
        'negative_correct_rejection': true_negatives,
        'false_positives_count': false_positives,
        'false_positive_rate': round(false_positives / total_neg, 4) if total_neg else 0,
        'mean_latency_ms': round(sum(r.get('elapsed_ms', 0) for r in results) / len(results), 1) if results else 0,
        'details': results
    }

    out_file = ROOT / 'docs' / 'tickets' / 'p2' / 'evidence' / 'golden_truth_eval_report.json'
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps({k: v for k, v in summary.items() if k != 'details'}, ensure_ascii=False, indent=2))
    return summary


if __name__ == '__main__':
    run_eval()
