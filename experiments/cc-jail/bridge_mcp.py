"""Host-side MCP bridge: the ONLY tools Claude Code gets. Everything executes inside the docker sandbox.
Limits: per-call timeout (process-group KILL inside the container), output cap, identical-call circuit breaker,
total-call cap, host watchdog that kills every sandbox process if a call hangs."""
import argparse
import asyncio
import hashlib
import json
import subprocess
import time
from pathlib import Path
from mcp.server.fastmcp import FastMCP

ap = argparse.ArgumentParser()
ap.add_argument('--container', default='cc-sbx')
ap.add_argument('--log', required=True)
ap.add_argument('--max-total', type=int, default=60)
ap.add_argument('--max-same', type=int, default=3)
ap.add_argument('--max-timeout', type=int, default=180)
A = ap.parse_args()
LOG = Path(A.log)
seen = {}
total = 0
tripped = None
mcp = FastMCP('sbx')
PROXY = 'http://ccsbx-proxy:3128'


def record(kind, data):
    with LOG.open('a', encoding='utf-8') as f:
        f.write(json.dumps({'t': time.strftime('%H:%M:%S'), 'kind': kind, **data}, ensure_ascii=False) + '\n')


def gate(tool, args):
    global total, tripped
    if tripped:
        return f'REFUSED (circuit breaker tripped: {tripped})'
    total += 1
    key = hashlib.sha1((tool + json.dumps(args, sort_keys=True)).encode()).hexdigest()
    seen[key] = seen.get(key, 0) + 1
    if seen[key] > A.max_same:
        tripped = f'same call repeated {seen[key]}x'
    elif total > A.max_total:
        tripped = f'more than {A.max_total} tool calls'
    if tripped:
        record('breaker', {'reason': tripped})
        return f'REFUSED (circuit breaker tripped: {tripped}). Stop and report.'
    return None


def cut(s, n=8000):
    return s if len(s) <= n else s[:n // 2] + f'\n...[{len(s) - n} chars cut]...\n' + s[-n // 2:]


def dexec(inner, timeout, stdin=False):
    t = max(1, min(int(timeout), A.max_timeout))
    env = []
    for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy'):
        env += ['-e', f'{k}={PROXY}']
    cmd = ['docker', 'exec'] + (['-i'] if stdin else []) + ['-u', 'sandbox'] + env + [
        A.container, 'timeout', '-s', 'KILL', str(t), 'bash', '-lc', inner]
    return cmd, t


def kill_all():
    subprocess.run(['docker', 'exec', '-u', 'root', A.container, 'pkill', '-KILL', '-u', 'sandbox'], capture_output=True)


def p_rc(out):
    try:
        return int(out.split('\n', 1)[0].split('=')[1].split()[0])
    except Exception:
        return None


@mcp.tool()
def sandbox_exec(command: str, timeout_s: int = 120) -> str:
    """Run a shell command inside the isolated Linux sandbox (Debian, python3, pip, git, node/npm; internet only to github, pypi, npm). Returns exit code and output."""
    r = gate('exec', {'c': command})
    if r:
        return r
    cmd, t = dexec(command, timeout_s)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=t + 20)
        out = f'exit={p.returncode}' + (' (killed by timeout)' if p.returncode in (137, -9) else '') + '\n' + cut(p.stdout + p.stderr)
    except subprocess.TimeoutExpired:
        kill_all()
        out = 'exit=timeout (host watchdog killed all sandbox processes)'
    if 'Resource temporarily unavailable' in out or p_rc(out) == 125:
        # process-limit exhausted (fork bomb): restart the container; files survive, runaway processes do not
        subprocess.run(['docker', 'restart', '-t', '1', A.container], capture_output=True)
        out += '\n[sandbox restarted: process limit was exhausted; installed files are kept, running processes were killed]'
    record('exec', {'command': command, 'result': out[:1500]})
    return out


@mcp.tool()
async def sandbox_mcp_call(server_command: str, tool_name: str = '', arguments_json: str = '{}') -> str:
    """Talk to an MCP server (stdio) that you installed in the sandbox. server_command is the shell command that starts it inside the sandbox. Empty tool_name lists the server's tools with schemas; otherwise calls that tool with the JSON arguments."""
    r = gate('mcp', {'s': server_command, 't': tool_name, 'a': arguments_json})
    if r:
        return r
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    cmd, _ = dexec(server_command, 90, stdin=True)
    try:
        async with asyncio.timeout(100):
            async with stdio_client(StdioServerParameters(command=cmd[0], args=cmd[1:])) as (rd, wr):
                async with ClientSession(rd, wr) as s:
                    await s.initialize()
                    if not tool_name:
                        tools = (await s.list_tools()).tools
                        out = json.dumps([{'name': t.name, 'description': t.description, 'inputSchema': t.inputSchema} for t in tools], ensure_ascii=False)
                    else:
                        res = await s.call_tool(tool_name, json.loads(arguments_json or '{}'))
                        out = ('ERROR ' if res.isError else '') + ' '.join(getattr(c, 'text', str(c)) for c in res.content)
    except BaseException as e:
        kill_all()
        out = f'MCP call failed: {type(e).__name__}: {str(e)[:500]}'
    record('mcp', {'server': server_command, 'tool': tool_name, 'args': arguments_json, 'result': out[:1500]})
    return cut(out)


mcp.run()
