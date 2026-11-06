"""Run Claude Code with ONLY the sandbox bridge tools (no built-in Bash/Read/Write/WebFetch).
Hard limits: 30 turns, $1.5 estimate cap (claude flags), wall-clock timeout, identical tool-call breaker that kills the
whole claude process tree. Never retries."""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument('--work', required=True)
ap.add_argument('--prompt', required=True)
ap.add_argument('--claude', default='claude')
ap.add_argument('--bridge-python', default=sys.executable)
ap.add_argument('--max-turns', type=int, default=30)
ap.add_argument('--max-budget-usd', type=float, default=1.5)
ap.add_argument('--timeout', type=int, default=900)
ap.add_argument('--max-same', type=int, default=3)
ap.add_argument('--extra-tools-flag', action='store_true', help='(test only) do not pass --tools ""')
A = ap.parse_args()
work = Path(A.work).resolve()
(work / 'project').mkdir(parents=True, exist_ok=True)
bridge_log = work / 'bridge_log.jsonl'
mcp_cfg = {'mcpServers': {'sbx': {'command': A.bridge_python, 'args': [str(Path(__file__).with_name('bridge_mcp.py')), '--log', str(bridge_log)]}}}
(work / 'mcp.json').write_text(json.dumps(mcp_cfg), encoding='utf-8')
cmd = [A.claude, '-p', '--output-format', 'stream-json', '--verbose', '--mcp-config', str(work / 'mcp.json'), '--strict-mcp-config',
       '--setting-sources', 'project', '--max-turns', str(A.max_turns), '--max-budget-usd', str(A.max_budget_usd),
       '--allowedTools', 'mcp__sbx__sandbox_exec', 'mcp__sbx__sandbox_mcp_call']
if not A.extra_tools_flag:
    cmd += ['--tools', '']
env = {k: v for k, v in os.environ.items() if k != 'ANTHROPIC_API_KEY'}
env.update(MCP_TIMEOUT='90000', ENABLE_TOOL_SEARCH='false')
proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                        cwd=work / 'project', env=env)
verdict = {'killed': None}


def kill(reason):
    verdict['killed'] = reason
    subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], capture_output=True)


timer = threading.Timer(A.timeout, kill, args=[f'wall-clock timeout {A.timeout}s'])
timer.start()
errs = []
threading.Thread(target=lambda: errs.append(proc.stderr.read()), daemon=True).start()
proc.stdin.write(A.prompt)
proc.stdin.close()
calls = {}
transcript = (work / 'transcript.jsonl').open('w', encoding='utf-8')
steps = []
for line in proc.stdout:
    transcript.write(line)
    transcript.flush()
    try:
        ev = json.loads(line)
    except ValueError:
        continue
    if ev.get('type') == 'assistant':
        for b in ev.get('message', {}).get('content', []):
            if b.get('type') == 'tool_use':
                key = b['name'] + json.dumps(b.get('input'), sort_keys=True)
                calls[key] = calls.get(key, 0) + 1
                steps.append((b['name'], json.dumps(b.get('input'), ensure_ascii=False)[:200]))
                if calls[key] > A.max_same:
                    kill(f'identical tool call repeated {calls[key]}x: {b["name"]}')
    if ev.get('type') == 'result':
        steps.append(('RESULT', f"{str(ev.get('result'))[:1500]} | cost ${ev.get('total_cost_usd')} | turns {ev.get('num_turns')} | {ev.get('subtype')}"))
proc.wait()
timer.cancel()
transcript.close()
(work / 'stderr.txt').write_text(''.join(errs)[-4000:], encoding='utf-8')
out = ['# steps'] + [f'{i + 1}. {n}  {a}' for i, (n, a) in enumerate(steps)] + [f'# returncode {proc.returncode}; killed: {verdict["killed"]}']
(work / 'summary.txt').write_text('\n'.join(out), encoding='utf-8')
print('\n'.join(out))
