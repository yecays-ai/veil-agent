"""Ticket 228: a real Claude Code run through the VEIL chat channel, in a throw-away data folder.

Uses a stub persona and a fake approval answerer; nothing from the real Peter is read or written.
Usage: python scripts/claude_agent_smoke.py [--claude PATH] [--keep]
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

TOML = '''name = "Peter"
provider = "cc"
persona = "persona/test.md"
history_rounds = 20
max_output_tokens = 50
[providers.cc]
kind = "claude_agent"
model = "claude-agent"
max_input_tokens = 200000
[tools]
enabled = true
folder = "data/test/workspace"
max_steps = 6
'''
PERSONA = ("You are Peter, 23, Lan's boyfriend, texting her. Casual American English, short messages, no lectures. "
           "You may use your tools; the program asks Lan when she must say yes.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--claude', default=None)
    parser.add_argument('--keep', action='store_true')
    args = parser.parse_args()
    from veil_agent import Budget, Chat, Store, load_character
    import claude_agent_channel as channel
    root = Path(tempfile.mkdtemp(prefix='veil-cc-smoke-'))
    (root / 'characters' / 'test').mkdir(parents=True)
    (root / 'persona').mkdir()
    (root / 'persona' / 'test.md').write_text(PERSONA, encoding='utf-8')
    (root / 'characters' / 'test' / 'character.toml').write_text(TOML, encoding='utf-8')
    (root / 'data' / 'test' / 'workspace').mkdir(parents=True)
    cfg = load_character(root, 'test')
    if args.claude:
        cfg.provider['claude_path'] = args.claude
    store = Store(root / 'data')
    shown, asked = [], []

    def approval(prompt):
        asked.append(prompt)
        print('--- APPROVAL CARD ---\n' + prompt)
        answer = 'y' if ('hello.txt' in prompt or 'example.com' in prompt) else 'n'
        print('answered:', answer)
        return answer

    def no_model(*_a, **_k):
        raise AssertionError('the API model must not be called')

    chat = Chat(cfg, store, Budget(store), no_model, output=lambda out: (shown.append(str(out)), print('PETER:', str(out))),
                approval=approval)
    try:
        started = time.time()
        for text in ("hey, quick check: are you there? answer in two short messages",
                     "make a file called hello.txt in your workspace folder containing the word hi, use the shell",
                     "now use the shell to fetch the first 80 characters of https://example.com and tell me what it says"):
            print('\nLAN:', text)
            chat.send(text)
            print(f'(turn done, {time.time() - started:.0f}s since start)')
        print('\n=== summary ===')
        print('bubbles:', len(shown), '| approval cards:', len(asked))
        print('costs:', store.read('test/claude-agent-costs.jsonl'))
        print('workspace files:', [p.name for p in (root / 'data' / 'test' / 'workspace').rglob('*') if p.is_file()])
        print('data folder:', root)
    finally:
        if not args.keep:
            import shutil
            shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
