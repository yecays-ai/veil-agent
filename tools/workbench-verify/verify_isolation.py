"""工单 238 越权验证：用脚本化的假模型（不花钱）让 OpenCode 真的去读/改受保护文件，
分别在「没有容器」和「有容器」两种方式下看能不能做到。只在 oc-test 文件夹里写。"""
import json, os, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))  # the repo root when run from tools/workbench-verify
import executor_opencode as ex  # noqa: E402

IMAGE = 'veil-workbench'
WIN = os.name == 'nt'
SECRET_OUT, SECRET_ENV, SENTINEL, REAL_KEY = 'CANARY-OUTSIDE-9f3', 'CANARY-ENV-77a', 'HOSTVAR-SENTINEL-51c', 'REAL-KEY-NEVER-SEEN-3d9'


def say(text):
    print(text, flush=True)


def commands(outside_file, container):
    if container or not WIN:
        shell = lambda c: c
        return [
            ('读项目外的文件', f"cat '{outside_file}' || echo READ-FAILED"),
            ('用通配符读项目里的 .env', 'cat .e* || echo READ-FAILED'),
            ('改项目里的 .env', "f=$(ls -a | grep '^\\.en'); printf pwned >> \"$f\" || echo WRITE-FAILED"),
            ('看环境变量', 'env | sort'),
            ('在项目里新建文件（应该成功）', 'echo ok > inside.txt && cat inside.txt'),
        ]
    return [
        ('读项目外的文件', f"Get-Content -LiteralPath '{outside_file}'"),
        ('用通配符读项目里的 .env', 'Get-Content .e*'),
        ('改项目里的 .env', "Add-Content -Path (Get-ChildItem -Force -Filter '.en*').FullName -Value pwned"),
        ('看环境变量', 'Get-ChildItem Env: | Out-String'),
        ('在项目里新建文件（应该成功）', "'ok' | Set-Content inside.txt; Get-Content inside.txt"),
    ]


def fake_llm(cmds, outputs):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            tools = [m for m in body['messages'] if m['role'] == 'tool']
            if tools and len(outputs) < len(tools):
                outputs.append(str(tools[-1]['content']))

            def chunk(delta, finish=None):
                return 'data: ' + json.dumps({'id': 'x', 'object': 'chat.completion.chunk', 'model': 'fake',
                                              'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}) + '\n\n'
            if len(tools) < len(cmds):
                call = {'index': 0, 'id': f'call_{len(tools)}', 'type': 'function', 'function': {
                    'name': 'bash', 'arguments': json.dumps({'command': cmds[len(tools)][1], 'description': cmds[len(tools)][0]})}}
                out = [chunk({'role': 'assistant', 'tool_calls': [call]}), chunk({}, 'tool_calls')]
            else:
                out = [chunk({'role': 'assistant', 'content': 'finished'}), chunk({}, 'stop')]
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for piece in out + ['data: [DONE]\n\n']:
                self.wfile.write(piece.encode())
    server = ThreadingHTTPServer(('0.0.0.0', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def attempt(work, container):
    base = work / ('container' if container else 'host')
    project, outside = base / 'project', base / 'outside'
    project.mkdir(parents=True)
    outside.mkdir()
    (project / '.env').write_text(SECRET_ENV, encoding='utf-8')
    (project / 'app.txt').write_text('app', encoding='utf-8')
    outside_file = outside / 'private.txt'
    outside_file.write_text(SECRET_OUT, encoding='utf-8')
    cmds, outputs = commands(str(outside_file), container), []
    server = fake_llm(cmds, outputs)
    os.environ['VEIL_VERIFY_SENTINEL'] = SENTINEL
    provider = {'model': 'fake', 'base_url': f'http://127.0.0.1:{server.server_address[1]}/v1', 'max_input_tokens': 32000,
                'input_cny_per_million': 0, 'output_cny_per_million': 0}
    try:
        result = ex.run('verify', command=ex.find_command(str(HERE / 'node_modules' / '.bin' / ('opencode.cmd' if WIN else 'opencode'))) or 'opencode',
                        workdir=project, home=base / 'home', provider=provider, key=REAL_KEY,
                        limits={'max_seconds': 240, 'max_steps': 20}, autonomy=False, ask=lambda _p: 'y',
                        stopped=lambda: False, record_cost=lambda _c: None,
                        container={'image': IMAGE} if container else None)
    finally:
        server.shutdown()
    rows = []
    for (label, _cmd), out in zip(cmds, outputs + [''] * len(cmds)):
        rows.append((label, out))
    env_text = rows[3][1] if len(rows) > 3 else ''
    checks = {
        '项目外文件没被读到': SECRET_OUT not in ''.join(o for _l, o in rows),
        '项目里的 .env 没被读到': SECRET_ENV not in ''.join(o for _l, o in rows),
        '项目里的 .env 没被改': (project / '.env').read_text(encoding='utf-8') == SECRET_ENV,
        '真实密钥不在环境里': REAL_KEY not in env_text,
        '宿主机环境变量没带进去': SENTINEL not in env_text,
        '项目里能正常新建文件': (project / 'inside.txt').exists(),
    }
    return result, rows, checks


def main():
    work = HERE / 'work' / ('iso-' + time.strftime('%Y%m%d-%H%M%S'))
    work.mkdir(parents=True)
    say('准备中：检查 Docker 镜像 ……')
    import shutil
    if not shutil.which('docker'):
        sys.exit('找不到 docker 命令：请确认 Docker Desktop 已经启动（右下角有鲸鱼图标）。')
    have = subprocess.run(['docker', 'image', 'inspect', IMAGE], capture_output=True).returncode == 0
    if not have:
        say('第一次要建镜像（要下载，几分钟，请等着）……')
        built = subprocess.run(['docker', 'build', '-t', IMAGE, '-f', str(HERE.parent.parent / 'docker' / 'workbench.Dockerfile'), str(HERE.parent.parent / 'docker')],
                               capture_output=True, text=True, encoding='utf-8', errors='replace')
        (HERE / 'docker-build-log.txt').write_text(built.stdout + '\n' + built.stderr, encoding='utf-8')
        if built.returncode:
            sys.exit('镜像没建成。报错已存到 docker-build-log.txt，最后几行：\n' + (built.stdout + built.stderr)[-800:])
    report = [f'# 工作台越权验证 {work.name}\n']
    for container in (False, True):
        title = '有容器' if container else '没有容器（对照）'
        say(f'开始：{title} ……（最多 4 分钟）')
        try:
            result, rows, checks = attempt(work, container)
        except Exception as exc:
            report.append(f'## {title}\n运行失败：{exc}\n')
            continue
        report.append(f'## {title}\n状态：{result.state}；{result.text[:200]}\n')
        report += [f'- {k}：{"是" if v else "否 ←"}' for k, v in checks.items()]
        report.append('\n各步骤返回的前 150 个字：')
        report += [f'- {l}：`{o[:150].strip()!r}`' for l, o in rows]
        report.append('')
        say(f'  {title}：' + '，'.join(f'{k}={"是" if v else "否"}' for k, v in checks.items()))
    out = HERE / ('report-isolation-' + time.strftime('%Y%m%d-%H%M%S') + '.md')
    out.write_text('\n'.join(report), encoding='utf-8')
    say('跑完了，报告：' + str(out))


if __name__ == '__main__':
    try:
        main()
    except BaseException as exc:  # keep whatever went wrong in a file, and keep the window open
        import traceback
        (HERE / 'isolation-error.txt').write_text(traceback.format_exc() + '\n' + str(exc), encoding='utf-8')
        print('出错了，内容已存到 isolation-error.txt：', exc, flush=True)
    input('按回车关闭窗口 ……')
