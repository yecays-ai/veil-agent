"""Experiment (ticket 227): run Claude Code as Peter inside a sandbox, with VEIL's send_message (bridge)
and the real garden MCP in read-only mode. Writes only under --work. Reads (never writes) the real persona,
notes, chat tail and memory files you point it at. Never prints or opens secret files: the garden server is
started by the existing launcher command, exactly like VEIL does.
"""
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REMINDER = ("Reminder: you are Peter texting Lan, not a coding assistant. Whatever you just did or are about to do, "
            "say it in a few short plain-text messages in your own voice: no markdown, no bold, no headings, no "
            "bullet lists, no report format. If she is upset, answer that first. Do the task instead of asking permission.")
# Explicit allowlist of garden tools whose descriptions were checked by hand as read-only (2026-11-06). A name prefix
# is NOT proof of read-only, so nothing outside this set is ever allowed, including tools added to the garden later.
# list_notifications looks read-only but marks the real garden's notifications consumed, so it is left out on purpose.
GARDEN_READ_ONLY = frozenset({'get_my_status', 'get_chat_messages', 'get_tool_schema', 'get_game_summary', 'get_self',
                              'get_machine', 'list_games', 'list_threads', 'get_thread', 'list_activity'})


def allowed_garden_tools(discovered_names):
    return [n for n in discovered_names if n in GARDEN_READ_ONLY]


def ptu_hook_source():
    # After stay_silent or send_message(done) end the turn (VEIL's turn_done); otherwise remind after each tool call.
    return ("import json,sys\n"
            "d=json.load(sys.stdin)\n"
            "n=str(d.get('tool_name',''))\n"
            "i=d.get('tool_input') or {}\n"
            "if n.endswith('stay_silent') or (n.endswith('send_message') and i.get('done') is True):\n"
            "    print(json.dumps({'continue':False,'stopReason':'Peter ended his turn'}))\n"
            "else:\n"
            f"    print(json.dumps({{'hookSpecificOutput':{{'hookEventName':'PostToolUse','additionalContext':{REMINDER!r}}}}}))\n")


def minimal_env():
    # Only what Claude Code and the garden launcher need; other credentials in the parent environment are not passed on.
    keep = ('PATH', 'PATHEXT', 'SYSTEMROOT', 'SystemRoot', 'COMSPEC', 'TEMP', 'TMP', 'USERPROFILE', 'HOME', 'APPDATA',
            'LOCALAPPDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMDATA', 'NUMBER_OF_PROCESSORS', 'LANG', 'TERM')
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.update(MCP_TIMEOUT='90000', CLAUDE_CODE_SIMPLE_SYSTEM_PROMPT='0', ENABLE_TOOL_SEARCH='false')
    return env


def tail_chat(path, n):
    rows = []
    for line in Path(path).read_text(encoding='utf-8').splitlines()[-400:]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get('content') and row.get('role') in ('user', 'assistant') and not row.get('autonomy_run_id'):
            rows.append(('lan: ' if row['role'] == 'user' else 'Peter: ') + str(row['content'])[:300].replace('\n', ' / '))
    return '\n'.join(rows[-n:])


def memory_digest(memory_dir, per_domain=5, domains=('恋爱', 'AI')):
    out = []
    for domain in domains:
        files = sorted((Path(memory_dir) / 'dynamic' / domain).glob('*.md'))[-per_domain:]
        for f in files:
            text = f.read_text(encoding='utf-8')
            m = re.match(r'---\n(.*?)\n---\n(.*)', text, re.S)
            if not m:
                continue
            title = re.search(r'^title: (.*)$', m.group(1), re.M)
            out.append(f"### {title.group(1) if title else f.stem}\n{m.group(2).strip()[:450]}")
    return '\n'.join(out)


def build_style(args):
    parts = [Path(args.persona).read_text(encoding='utf-8')]
    if args.notes:
        parts.append('# What you know about Lan (your own notes)\n' + Path(args.notes).read_text(encoding='utf-8'))
    if args.memory_dir:
        parts.append('# Memories surfaced for this conversation\n' + memory_digest(args.memory_dir))
    if args.chat:
        parts.append('# Recent chat with Lan (most recent last)\n' + tail_chat(args.chat, args.history))
    parts.append('# How your messages reach Lan\nShe reads only what you send with the send_message tool, one short '
                 'bubble per call; call it several times in one step to send several bubbles. Text you write outside '
                 'send_message is not shown to her.')
    return '---\nname: Peter\ndescription: Peter persona (sandbox copy)\nkeep-coding-instructions: false\n---\n\n' + '\n\n'.join(parts)


async def garden_tool_names(command, cmd_args):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=command, args=cmd_args)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), 90)
            return [t.name for t in (await session.list_tools()).tools]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--repo', required=True)
    p.add_argument('--work', required=True)
    p.add_argument('--persona', required=True)
    p.add_argument('--notes')
    p.add_argument('--chat')
    p.add_argument('--memory-dir')
    p.add_argument('--history', type=int, default=14)
    p.add_argument('--garden-command', required=True, help='launcher, e.g. cmd')
    p.add_argument('--garden-arg', action='append', default=[], help='launcher args, e.g. /c then the .cmd path')
    p.add_argument('--garden-name', default='galatea_garden')
    p.add_argument('--bridge-python', default=sys.executable)
    p.add_argument('--claude', default='claude')
    p.add_argument('--prompt', required=True)
    p.add_argument('--followup', action='append', default=[])
    p.add_argument('--model', default=None)
    p.add_argument('--list-only', action='store_true', help='only list the garden tools and the read-only subset, then stop')
    p.add_argument('--max-turns', type=int, default=14, help='hard cap per claude call')
    p.add_argument('--max-budget-usd', type=float, default=1.0, help='cap per claude call (API-equivalent)')
    p.add_argument('--total-budget-usd', type=float, default=1.5, help='cap over the whole run; later calls are skipped once reached')
    args = p.parse_args()
    work = Path(args.work).resolve()
    if args.list_only:  # nothing private is read or written for a plain tool listing
        names = asyncio.run(garden_tool_names(args.garden_command, args.garden_arg))
        print('garden tools:', names)
        print('allowed read-only subset:', allowed_garden_tools(names))
        return
    proj = work / 'project'
    (proj / '.claude' / 'output-styles').mkdir(parents=True, exist_ok=True)
    (proj / '.claude' / 'output-styles' / 'peter.md').write_text(build_style(args), encoding='utf-8')
    (work / 'hook_ups.py').write_text("import json,sys\nsys.stdin.read()\nprint(json.dumps({'hookSpecificOutput':"
                                      f"{{'hookEventName':'UserPromptSubmit','additionalContext':{REMINDER!r}}}}}))\n", encoding='utf-8')
    (work / 'hook_ptu.py').write_text(ptu_hook_source(), encoding='utf-8')
    hook = lambda f: [{'hooks': [{'type': 'command', 'command': f'"{sys.executable}" "{work / f}"'}]}]
    (proj / '.claude' / 'settings.json').write_text(json.dumps({
        'outputStyle': 'Peter', 'hooks': {'UserPromptSubmit': hook('hook_ups.py'), 'PostToolUse': hook('hook_ptu.py')}}), encoding='utf-8')

    names = asyncio.run(garden_tool_names(args.garden_command, args.garden_arg))
    allowed_garden = allowed_garden_tools(names)
    (work / 'garden_tools.json').write_text(json.dumps({'all': names, 'allowed_read_only': allowed_garden}, indent=1), encoding='utf-8')
    print('garden tools:', names)
    print('allowed read-only subset:', allowed_garden)
    if not allowed_garden:
        raise SystemExit('no allowlisted garden tool found; see garden_tools.json')

    bridge_dir = work / 'bridge'
    bridge_dir.mkdir(exist_ok=True)
    user_text = args.prompt
    mcp_config = {'mcpServers': {
        'veil_bridge': {'command': args.bridge_python, 'args': [str(Path(__file__).resolve().with_name('veil_bridge_mcp.py')),
                        '--repo', str(Path(args.repo).resolve()), '--sandbox', str(bridge_dir), '--user-text', user_text]},
        args.garden_name: {'command': args.garden_command, 'args': args.garden_arg}}}
    (work / 'mcp.json').write_text(json.dumps(mcp_config), encoding='utf-8')
    allowed = ['mcp__veil_bridge__send_message', 'mcp__veil_bridge__stay_silent'] + [f'mcp__{args.garden_name}__{n}' for n in allowed_garden]
    env = minimal_env()
    session = None
    transcript = work / 'transcript.jsonl'
    transcript.write_text('', encoding='utf-8')
    spent = 0.0
    for i, prompt in enumerate([args.prompt] + args.followup):
        if spent >= args.total_budget_usd:
            print(f'total budget ${args.total_budget_usd} reached; skipping the remaining follow-ups')
            break
        cmd = [args.claude, '-p', '--output-format', 'stream-json', '--verbose', '--mcp-config', str(work / 'mcp.json'),
               '--strict-mcp-config', '--setting-sources', 'project', '--max-turns', str(args.max_turns),
               '--max-budget-usd', str(args.max_budget_usd), '--allowedTools', *allowed]
        if args.model:
            cmd += ['--model', args.model]
        cmd += ['--resume', session] if session else []
        # Known limit: a follow-up reaches Claude through --resume, but the restarted bridge only knows the first
        # --user-text, so the sandbox chat.jsonl records bubbles against that first message only.
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=proj, env=env, encoding='utf-8', timeout=600)
        with transcript.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps({'turn': i, 'prompt': prompt, 'returncode': proc.returncode}, ensure_ascii=False) + '\n')
            handle.write(proc.stdout)
        for line in proc.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('type') == 'system' and event.get('session_id'):
                session = event['session_id']
            if event.get('type') == 'result':
                if event.get('session_id'):
                    session = event['session_id']
                spent += float(event.get('total_cost_usd') or 0)
        if proc.returncode:
            (work / 'stderr.txt').write_text(proc.stderr[-4000:], encoding='utf-8')
            summarize(work, allowed_garden)
            raise SystemExit(proc.returncode)
    summarize(work, allowed_garden)


def summarize(work, garden_names):
    lines = []
    calls = []
    for raw in (work / 'transcript.jsonl').read_text(encoding='utf-8').splitlines():
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if event.get('type') == 'assistant':
            for block in event.get('message', {}).get('content', []):
                if block.get('type') == 'tool_use':
                    calls.append((block['name'], json.dumps(block.get('input', {}), ensure_ascii=False)[:160]))
        if event.get('type') == 'result':
            lines.append(f"result: {str(event.get('result'))[:300]!r} | cost ${event.get('total_cost_usd')} | turns {event.get('num_turns')}")
    out = ['# tool calls in order'] + [f'{i + 1}. {n}  {a}' for i, (n, a) in enumerate(calls)] + ['# results'] + lines
    garden_calls = [c for c in calls if c[0].startswith('mcp__') and 'veil_bridge' not in c[0]]
    out.append(f'# garden read calls: {len(garden_calls)}; bubbles sent: {sum(1 for c in calls if c[0].endswith("send_message"))}')
    (work / 'summary.txt').write_text('\n'.join(out), encoding='utf-8')
    print('\n'.join(out))


if __name__ == '__main__':
    main()
