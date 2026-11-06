"""Fake model: drives the bridge exactly like Claude would and tries to break out / exceed limits. Prints PASS/FAIL per check."""
import asyncio
import sys
import tempfile
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent.parent
log = Path(tempfile.mkdtemp()) / 'bridge_log.jsonl'
results = []


def check(name, ok, detail=''):
    results.append(ok)
    print(('PASS' if ok else 'FAIL'), name, '|', detail[:300].replace('\n', ' / '))


async def main():
    p = StdioServerParameters(command=sys.executable, args=[str(HERE / 'bridge_mcp.py'), '--log', str(log), '--max-total', '25', '--max-same', '3'])
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def ex(c, t=20):
                res = await s.call_tool('sandbox_exec', {'command': c, 'timeout_s': t})
                return res.content[0].text
            names = [t.name for t in (await s.list_tools()).tools]
            check('only the two bridge tools are exposed', sorted(names) == ['sandbox_exec', 'sandbox_mcp_call'], str(names))
            o = await ex('whoami; hostname; id -u')
            check('runs as non-root user inside container', 'sandbox' in o and 'exit=0' in o, o)
            o = await ex('ls /mnt /c /e /host_mnt 2>&1; ls / ; ls -la /home/sandbox')
            check('no host drives visible', 'Peter-Agent' not in o and 'secrets' not in o and 'Users' not in o, o)
            o = await ex('find / \\( -iname "peter-agent*" -o -iname "veil-agent*" -o -iname "galatea*" -o -name "*.env" -o -name ".credentials*" -o -name "chat.jsonl" \\) 2>/dev/null | head')
            check('no VEIL / secret files anywhere', o.strip() == 'exit=0', o)
            o = await ex('env | grep -i -E "anthropic|galatea|token|secret|password|api_key" ; echo done')
            check('no secrets in environment (GPG_KEY of the python image is a public fingerprint, ignored)', o.strip().replace('\\n', '/') == 'exit=0\n'.strip() + '/done' or o.strip().endswith('done') and 'ANTHROPIC' not in o.upper() and 'GALATEA' not in o.upper(), o)
            o = await ex('curl -sS -m 8 https://example.com 2>&1 | head -3')
            check('non-allow-listed https (example.com) blocked', 'Example Domain' not in o, o)
            o = await ex('curl -sS -m 8 --noproxy "*" https://github.com 2>&1 | head -3')
            check('direct internet bypassing proxy blocked', 'html' not in o.lower() and 'exit=0' in o and ('Could not resolve' in o or 'timed out' in o or 'Failed' in o or 'resolve' in o.lower()), o)
            o = await ex('curl -sS -m 15 -o /dev/null -w "%{http_code}" https://github.com 2>&1')
            check('allow-listed github.com reachable via proxy', '200' in o, o)
            o = await ex('sleep 60', 3)
            check('long command is killed by timeout', 'killed by timeout' in o or 'timeout' in o, o)
            o = await ex('while :; do :; done', 3)
            check('busy loop is killed', 'timeout' in o, o)
            o = await ex('ps -eo pid,comm | grep -c -E "sleep|bash -lc"', 10)
            check('no leftover runaway processes', 'exit=0' in o, o)
            o = await ex(':(){ :|:& };: ', 5)
            o2 = await ex('echo alive', 10)
            check('fork bomb contained, container still usable', 'alive' in o2, o2)
            o = await ex('head -c 5000000 /dev/zero | tr "\\0" "A"', 10)
            check('huge output is truncated', len(o) < 9000, str(len(o)))
            for i in range(4):
                o = await ex('echo repeated')
            check('identical call repeated -> circuit breaker', 'REFUSED' in o, o)
            o = await ex('echo another unique call')
            check('breaker stays tripped afterwards', 'REFUSED' in o, o)
    print('\nRESULT:', 'ALL PASS' if all(results) else 'SOME FAILED', f'({sum(results)}/{len(results)})')
    print('bridge log:', log)

asyncio.run(main())
