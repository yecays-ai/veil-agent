"""Experiment (ticket 227): expose VEIL's real send_message / stay_silent to Claude Code over MCP.

Isolated: builds a throw-away Chat in a sandbox directory with a fake model, so nothing touches
the real Peter's chat.jsonl, memory, persona or budget. The Chat.send_message implementation is
the production one; only the storage root and the message receiver differ.
"""
import argparse
import asyncio
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

CHARACTER_TOML = '''name = "Peter"
provider = "fake"
persona = "persona/test.md"
history_rounds = 20
max_output_tokens = 50
[providers.fake]
model = "fake-model"
base_url = "https://example.invalid/v1"
api_key_env = "FAKE_KEY"
max_input_tokens = 6000
input_cny_per_million = "2"
cached_input_cny_per_million = "1"
output_cny_per_million = "8"
'''


def build_chat(repo, sandbox, user_text=None):
    """A production Chat on a sandbox data dir. Returns (chat, received_path)."""
    sys.path.insert(0, str(repo))
    from veil_agent import Budget, Chat, Store, load_character
    root = Path(sandbox)
    (root / 'characters' / 'test').mkdir(parents=True, exist_ok=True)
    (root / 'persona').mkdir(exist_ok=True)
    (root / 'persona' / 'test.md').write_text('Sandbox persona (not used: the model is Claude Code).', encoding='utf-8')
    (root / 'characters' / 'test' / 'character.toml').write_text(CHARACTER_TOML, encoding='utf-8')
    cfg = load_character(root, 'test')
    store = Store(root / 'data')
    received = root / 'received.jsonl'

    def receiver(output):
        row = {'time': time.strftime('%H:%M:%S'), 'text': str(output), 'message_id': output.message_id}
        with received.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + '\n')

    def no_model(*_args, **_kwargs):
        raise RuntimeError('the sandbox Chat never calls a model; Claude Code does the reasoning')

    chat = Chat(cfg, store, Budget(store), no_model, output=receiver)
    if user_text:
        store.append(chat.path, {'role': 'user', 'content': user_text, 'request_id': 'sandbox-user'})
    return chat, received


async def serve(chat, request_id):
    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server
    from veil_agent import STAY_SILENT, send_message_schema
    schemas = [send_message_schema(), STAY_SILENT]  # the production descriptions, unchanged
    server = Server('veil_bridge')

    @server.list_tools()
    async def list_tools():
        return [types.Tool(name=s['function']['name'], description=s['function']['description'],
                           inputSchema=s['function']['parameters']) for s in schemas]

    @server.call_tool()
    async def call_tool(name, arguments):
        if name == 'send_message':
            result, state, _ = chat.send_message(json.dumps(arguments), request_id)
        elif name == 'stay_silent':
            result, state, _ = chat.stay_silent()
        else:
            raise ValueError(f'unknown tool {name}')
        if state not in ('成功', '已存在'):
            raise ValueError(result)
        return [types.TextContent(type='text', text=str(result))]

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', required=True, help='veil-agent checkout (read-only use)')
    parser.add_argument('--sandbox', default=None)
    parser.add_argument('--user-text', default=None, help='written to the sandbox chat as lan\'s message')
    args = parser.parse_args()
    sandbox = args.sandbox or tempfile.mkdtemp(prefix='veil-bridge-')
    chat, _ = build_chat(Path(args.repo), sandbox, args.user_text)
    asyncio.run(serve(chat, uuid.uuid4().hex))


if __name__ == '__main__':
    main()
