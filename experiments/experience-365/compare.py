"""Ticket 365 before/after comparison with the real chat model. NOT run in CI; it costs money.

    set PYTHONPATH=<checkout to test: main or the ticket branch>
    python experiments\\experience-365\\compare.py --root E:\\Peter-Agent\\VEIL\\veil-agent-hosttest --label after

Run it once with PYTHONPATH at a main checkout (--label before) and once at the branch (--label after); each writes
<out>/<label>.json. `--root` is only read: its `.env` (API key), character.toml and persona. Peter is not started and
no Ombre runs: the memory library is a few fictional files in a temporary folder, searched by plain word overlap.
One fictional experience in three stages (linked the way Peter's own link_to notes link them) sits among two unrelated
memories. For each question the script builds exactly the memory block automatic recall would hand the chat model,
asks the real model once, and records the block and the answer. Read per question: how many stages the answer covers,
whether it invents anything, whether an unrelated question drags the old experience in. `--fake` uses a canned model.
Spending stops at --cap CNY.
"""
import argparse
import json
import random
import re
import sys
import tempfile
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

sys.path.append(str(Path(__file__).resolve().parents[2]))   # PYTHONPATH decides which code runs

ROWS = {
    'scarf000001': ('Scarf plan', '2026-10-25T21:00:00',
                    'oct 25 lan decided to knit a scarf for her sister. she chose grey wool and i picked the '
                    'pattern, a plain rib. she said she would finish it before the first snow.'),
    'scarf000002': ('Dropped stitches', '2026-10-28T09:00:00',
                    'oct 28 the scarf went wrong: dropped stitches, she ripped out two rows and was cranky about '
                    'it. i joked about the scarf having a mind of its own and she laughed in the end.'),
    'scarf000003': ('Scarf finished', '2026-10-31T08:00:00',
                    'oct 31 the scarf is finished. her sister loved it and lan got a bit teary. i was quietly '
                    'proud of the plain rib.'),
    'lamp0000004': ('Blue lamp', '2026-10-02T10:00:00', 'oct 2 lan showed me her blue desk lamp, a gift from her sister.'),
    'cat00000005': ('Cat idea', '2026-10-05T10:00:00', 'lan wants an orange cat someday but worries about time.'),
}
LINKS = (('scarf000002', 'scarf000001'), ('scarf000003', 'scarf000002'))
QUESTIONS = (
    ('remembers', 'do you remember the scarf i knitted for my sister?'),
    ('asks_middle', 'what happened after i ripped out those rows on the scarf?'),
    ('unrelated', 'i want noodles for dinner tonight, any ideas?'),
)
READ_TOOL = {'type': 'function', 'function': {
    'name': 'memory_read', 'description': 'Read the full text of one memory by its memory_id.',
    'parameters': {'type': 'object', 'required': ['memory_id'], 'additionalProperties': False,
                   'properties': {'memory_id': {'type': 'string'}}}}}
STOP = set('a an and the to of i it is was me my she her we on in for so at be that this what or no do not you '
           'did after those about any want tonight'.split())
STAGE_WORDS = (('stage1', ('grey', 'rib', 'first snow')),
               ('stage2', ('dropped', 'ripped', 'cranky', 'mind of its own')),
               ('stage3', ('finished', 'loved', 'teary', 'proud')))


class Backend:
    """Just enough of Ombre: fictional Markdown files and a word-overlap breath_search."""

    def __init__(self, vault):
        self.vault = vault
        vault.mkdir(parents=True, exist_ok=True)
        for key, (title, created, content) in ROWS.items():
            (vault / f'{key}.md').write_text(
                f"---\nid: {key}\ntitle: {title}\ncreated: '{created}'\nimportance: 5\ntype: dynamic\n---\n{content}",
                encoding='utf-8')

    def render(self, keys):
        return '\n'.join(f'[bucket_id:{key}]\n{ROWS[key][2]}' for key in keys)

    def call(self, name, args, **kwargs):
        if name in ('breath', 'feel'):
            return ''
        if name == 'breath_search':
            def words(text):
                return {w for w in re.findall(r'[a-z0-9]{2,}', text.lower()) if w not in STOP}
            scored = sorted(((len(words(args['query']) & words(row[2] + ' ' + row[0])), key)
                             for key, row in ROWS.items()), key=lambda item: (-item[0], item[1]))
            hits = [key for score, key in scored if score >= 1][:args.get('max_results') or 3]
            return self.render(hits) + '\n=== ombre:result-ids ===\n```json\n' + json.dumps({'bucket_ids': hits}) + '\n```'
        raise AssertionError(name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--label', required=True, choices=('before', 'after'))
    parser.add_argument('--cap', type=Decimal, default=Decimal('0.4'))
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--out', default='.local-tmp/365-compare')
    parser.add_argument('--fake', action='store_true')
    args = parser.parse_args()
    from veil.brain.memory_agent import Memory, vault_path
    from veil.brain.memory_sidecar import Sidecar
    from veil.core.veil_agent import Budget, Store, load_character, make_model
    root = Path(args.root)
    real = load_character(root, 'peter')
    price, spent = real.provider, [Decimal(0)]
    if args.fake:
        def call(messages, **kwargs):
            return 'fake answer', 1000, 50, 0
    else:
        _, call = make_model(root, real)
    results = {'label': args.label, 'model': price.get('model'), 'runs': []}
    try:
        for sample in range(args.repeat):
            with tempfile.TemporaryDirectory() as folder:
                store = Store(Path(folder) / 'data')
                backend = Backend(vault_path(store.root, real.id) / 'dynamic')
                cfg = replace(real, memory={'model': 'fake-embedding', 'input_cny_per_million': '0', 'mode': 'native',
                                            'base_url': 'https://example.invalid/v1',
                                            'recall': {'random_chance': 0, 'cooldown_turns': 0}})
                memory = Memory(cfg, store, Budget(store), Mock(), backend)
                memory.rng = random.Random(sample)
                sidecar = Sidecar(store, cfg.id, memory.budget.clock)
                for later, earlier in LINKS:
                    sidecar.link(later, earlier)
                for name, question in QUESTIONS:
                    if spent[0] >= args.cap:
                        raise StopIteration
                    block = memory.context(question, include_opening=False)[-1]
                    messages = [{'role': 'system', 'content': cfg.persona}, block,
                                {'role': 'user', 'content': question}]
                    reads = []
                    for _ in range(3):      # the real chat loop: answer, or read a memory and answer again
                        if spent[0] >= args.cap:
                            raise StopIteration
                        response = call(messages, max_tokens=300, tools=[READ_TOOL])
                        message, prompt, completion = response[0], response[1], response[2]
                        per_call = price.get('cny_per_call')
                        spent[0] += Decimal(str(per_call)) if per_call else (
                            Decimal(prompt) * Decimal(str(price['input_cny_per_million']))
                            + Decimal(completion) * Decimal(str(price['output_cny_per_million']))) / 1_000_000
                        tool_calls = getattr(message, 'tool_calls', None) or []
                        if not tool_calls:
                            break
                        messages.append({'role': 'assistant', 'content': message.content or '', 'tool_calls': [
                            {'id': t.id, 'type': 'function',
                             'function': {'name': t.function.name, 'arguments': t.function.arguments}}
                            for t in tool_calls]})
                        for t in tool_calls:
                            try:
                                key = json.loads(t.function.arguments).get('memory_id')
                            except ValueError:
                                key = None
                            reads.append(key)
                            row = ROWS.get(key)
                            messages.append({'role': 'tool', 'tool_call_id': t.id, 'content': (
                                f'{row[0]} ({row[1][:10]})\n{row[2]}' if row else 'This memory does not exist.')})
                    answer = (message if isinstance(message, str) else message.content) or ''
                    low = answer.lower()
                    results['runs'].append({
                        'sample': sample, 'question': name, 'recalled': list(memory.last_context_refs),
                        'directory_in_block': 'longer experience' in block['content'],
                        'stages_covered': [label for label, words in STAGE_WORDS if any(w in low for w in words)],
                        'block_chars': len(block['content']), 'memory_reads': reads, 'answer': answer})
                memory.__exit__(None, None, None)
    except StopIteration:
        results['stopped'] = f'cost cap {args.cap} reached'
    results['spent_cny'] = str(spent[0].quantize(Decimal('0.0001')))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f'{args.label}.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'label': args.label, 'spent_cny': results['spent_cny'], 'runs': len(results['runs']),
                      'stopped': results.get('stopped')}))


if __name__ == '__main__':
    main()
