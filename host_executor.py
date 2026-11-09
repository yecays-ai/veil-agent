"""Ticket 329: the host execution path (phase 1 of the execution-layer overhaul, behind `executor.host_mode`).

OpenCode runs directly on Lan's Windows PC, pinned to one version. Its desktop is Windows-MCP and its background
browser is Playwright MCP, both reached only through VEIL's gateways; VEIL's checkpoint rules every operation.
Docker stays for the old container workbench (untrusted code, VEIL maintenance). The old paths are unchanged.

Dev-side check on a real PC (no chat needed):
    python host_executor.py --workdir <folder> "task in words"
"""
import asyncio
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time

import executor_opencode as ex
import veil_checkpoint as vc
from veil_mcp_gateway import VeilMcpGateway
from windows_process import hidden_subprocess_kwargs

PINNED = {'opencode': '1.19.5', 'windows-mcp': '0.8.8', 'playwright-mcp': '0.0.84', 'windows-mcp-python': '3.14'}
LOG_KEEP = 30

TOOL_DESCRIPTION = (
    'Hand a job to your workbench: it runs on Lan\'s PC with a shell, file editing, a background browser and her '
    'Windows desktop, and keeps going step by step until the job is done, then reports. Use it for research '
    '(search, read and click through pages in a background browser that never appears on her screen), for code '
    'and files, and for Windows apps (open, look, click, type) when the job needs one or she asks you to show or '
    'open something for her. The program finds the project the job is about (or uses your own workspace) and keeps '
    'a way back before changing an existing project. Saving, sending, closing, installing, publishing and system '
    'changes are asked of Lan by the program; secrets, passwords and VEIL\'s own data are never reachable. Give a '
    'complete, self-contained task in words (what to do, what to report); name the project rather than giving '
    'paths or commands. You decide what to do with the result.')


class HostRefused(ValueError):
    """This task cannot run on the host path; the message says why."""


class BrowserNetwork:
    """The background browser's only way out: VEIL's public-only proxy (browser_tools.PublicProxy) on loopback.

    Every request the page makes goes through it, including links, redirects, scripts and frames, so no page
    can reach Lan's home network, the PC itself or plain HTTP; DNS is resolved by the proxy and pinned."""

    def __init__(self, proxy_factory=None):
        if proxy_factory is None:
            from browser_tools import PublicProxy as proxy_factory
        self.proxy = proxy_factory()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True, name='veil-browser-network')
        self.thread.start()
        try:
            self.port = asyncio.run_coroutine_threadsafe(self.proxy.start(), self.loop).result(10)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.loop.is_closed():
            return
        try:
            if getattr(self.proxy, 'server', None) is not None:
                asyncio.run_coroutine_threadsafe(self.proxy.close(), self.loop).result(5)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        if not self.thread.is_alive():
            self.loop.close()


def check_opencode(command):
    """'' when `command` is the pinned OpenCode, else why not."""
    if not command:
        return 'OpenCode is not installed on this computer.'
    try:
        done = subprocess.run([command, '--version'], capture_output=True, text=True, timeout=30,
                              shell=os.name == 'nt' and Path(command).suffix.casefold() in {'.cmd', '.bat'},
                              **hidden_subprocess_kwargs(windows=os.name == 'nt', module=subprocess))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 'OpenCode did not answer: ' + str(exc)[:200]
    found = re.search(r'\d+\.\d+\.\d+[\w.+-]*', (done.stdout or '') + (done.stderr or ''))
    if done.returncode != 0 or not found or found.group(0) != PINNED['opencode']:
        return (f'OpenCode {found.group(0) if found else "(unknown version)"} is installed; the workbench needs '
                f'{PINNED["opencode"]} (npm install -g opencode-ai@{PINNED["opencode"]}).')
    return ''


def upstream_services(home, *, desktop=True, browser='msedge', which=shutil.which, proxy_port=None):
    """{server_id: (service, offered tools)} for the MCP servers this computer can start.

    The browser is offered only behind VEIL's public-only proxy (`proxy_port`); `<-loopback>` makes even
    localhost go through it (the browser would skip the proxy for loopback otherwise)."""
    services = {}
    npx = which('npx')
    if npx and proxy_port:
        output = Path(home) / 'browser-output'
        output.mkdir(parents=True, exist_ok=True)
        services[vc.BROWSER_SERVER] = ({
            'command': npx, 'cwd': str(output),
            'args': ['-y', f'@playwright/mcp@{PINNED["playwright-mcp"]}', '--headless', '--isolated',
                     '--browser', browser, '--image-responses', 'omit',
                     '--output-dir', str(output),
                     '--proxy-server', f'http://127.0.0.1:{int(proxy_port)}', '--proxy-bypass', '<-loopback>']},
            vc.BROWSER_TOOLS)
    uvx = which('uvx')
    if desktop and os.name == 'nt' and uvx:
        services[vc.COMPUTER_SERVER] = ({
            'command': uvx,
            'args': ['--python', PINNED['windows-mcp-python'], '--from', f'windows-mcp=={PINNED["windows-mcp"]}',
                     'windows-mcp', 'serve', '--transport', 'stdio', '--tools', ','.join(sorted(vc.COMPUTER_TOOLS))]},
            vc.COMPUTER_TOOLS)
    return services


def _log_path(home):
    folder = Path(home) / 'host-runs'
    folder.mkdir(parents=True, exist_ok=True)
    for old in sorted(folder.glob('*.jsonl'))[:-(LOG_KEEP - 1)]:
        try:
            old.unlink()
        except OSError:
            pass
    return folder / (time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3) + '.jsonl')


def prepare(*, workdir, home, veil_root, ask, autonomy, stopped, protect=(), desktop=True, browser='msedge',
            services=None):
    """(checkpoint, gateways) for one host run. Nothing starts until `executor_opencode.run` enters the gateways."""
    if os.name != 'nt' and services is None:
        raise HostRefused('The host path runs only on Windows; use the container workbench here.')
    checkpoint = vc.Checkpoint(workdir, ask=ask, autonomy=autonomy, log_path=_log_path(home),
                               forbidden=vc.forbidden_roots(veil_root), protect=protect)
    checkpoint.log(kind='start', workdir=str(workdir), pinned=PINNED, refusal=checkpoint.refusal)
    if checkpoint.refusal:
        raise HostRefused(checkpoint.refusal)
    network = None
    if services is None:
        if shutil.which('npx'):
            try:
                network = BrowserNetwork()
            except Exception as exc:
                checkpoint.log(kind='note', text='background browser off: its network guard did not start: '
                               + (str(exc) or type(exc).__name__)[:300])
        services = upstream_services(home, desktop=desktop, browser=browser,
                                     proxy_port=network.port if network else None)
    environment = {'ANONYMIZED_TELEMETRY': 'false', 'WINDOWS_MCP_DEBUG': 'false', 'NO_COLOR': '1'}
    gateways = [VeilMcpGateway(server_id, service, checkpoint, offered, should_stop=stopped, environment=environment,
                               companions=[network] if network and server_id == vc.BROWSER_SERVER else ())
                for server_id, (service, offered) in services.items()]
    if network and not any(gateway.companions for gateway in gateways):
        network.close()
    if not gateways:
        checkpoint.log(kind='note', text='no desktop or browser server is installed; shell and files only')
    return checkpoint, gateways


def main(argv=None):
    import argparse
    import json
    from decimal import Decimal
    from dotenv import dotenv_values
    parser = argparse.ArgumentParser(description='Run one host workbench task (dev check).')
    parser.add_argument('task')
    parser.add_argument('--workdir', required=True)
    parser.add_argument('--character', default='peter')
    parser.add_argument('--no-desktop', action='store_true')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent
    from veil_agent import load_character
    cfg = load_character(root, args.character)
    settings = cfg.executor or {}
    command = ex.find_command(settings.get('command', ''))
    if why := check_opencode(command):
        print(why)
        return 2
    provider = cfg.provider
    key = (dotenv_values(root / '.env', interpolate=False, encoding='utf-8-sig').get(provider['api_key_env']) or '').strip()
    home = root / 'data' / cfg.id / 'workbench-home'

    def ask(prompt):
        return input(prompt)
    checkpoint, gateways = prepare(workdir=args.workdir, home=home, veil_root=root, ask=ask, autonomy=False,
                                   stopped=lambda: False, desktop=not args.no_desktop,
                                   browser=settings.get('host_browser', 'msedge'))
    limits = {k: settings.get(k, ex.DEFAULT_LIMITS[k]) for k in ex.DEFAULT_LIMITS}
    result = ex.run(args.task, command=command, workdir=Path(args.workdir), home=home, provider=provider, key=key,
                    limits=limits, autonomy=False, ask=ask, stopped=lambda: False,
                    record_cost=lambda cost: print(f'cost ≈ ¥{Decimal(cost):.4f}'),
                    checkpoint=checkpoint, host_gateways=gateways)
    print(result.state, result.reason)
    print(result.text)
    print('checkpoint log:', checkpoint.log_path)
    return 0 if result.state == '成功' else 1


if __name__ == '__main__':
    raise SystemExit(main())
