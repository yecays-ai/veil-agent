"""Ticket 239: turn Peter's workbench on or off and choose which project folders it may work in.
Writes data/<character>/workbench.json (git-ignored), so updating the repo never undoes it. Restart Peter afterwards."""
import json
import re
import subprocess
import sys
import traceback
from pathlib import Path
from windows_process import hidden_subprocess_kwargs

ROOT = Path(__file__).resolve().parent
CHARACTER = 'peter'
IMAGE = 'veil-workbench'
KEYS = ('enabled', 'project_roots', 'container_image', 'web_hosts')
HOST_PATTERN = re.compile(r'[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?')
MAX_HOSTS = 50


def settings_path(root=ROOT):
    return Path(root) / 'data' / CHARACTER / 'workbench.json'


def load_state(root=ROOT):
    path = settings_path(root)
    if not path.exists():
        return {'enabled': False, 'project_roots': [], 'container_image': IMAGE, 'web_hosts': []}
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    roots = data.get('project_roots', [])
    image = data.get('container_image', IMAGE)
    hosts = data.get('web_hosts', [])
    if not isinstance(data, dict) or not isinstance(roots, list) or not isinstance(image, str) or not isinstance(hosts, list):
        raise ValueError('workbench.json 格式不对')
    return {'enabled': data.get('enabled') is True, 'project_roots': [str(x) for x in roots], 'container_image': image,
            'web_hosts': [str(x) for x in hosts]}


def save_state(state, root=ROOT):
    path = settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {k: state[k] for k in KEYS}
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def add_folder(state, folder, root=ROOT):
    """Returns (ok, message). The folder must exist and must not contain or sit inside VEIL's own data folder."""
    path = Path(str(folder).strip().strip('"')).expanduser()
    if not str(folder).strip() or not path.is_dir():
        return False, '这个文件夹不存在。'
    path = path.resolve()
    for name in ('data', 'characters', 'persona'):
        own = (Path(root) / name).resolve()
        if path == own or own in path.parents or path in own.parents:
            return False, '这个文件夹包含或位于 Peter 自己的文件夹里，不能放进去。'
    text = str(path)
    if text in state['project_roots']:
        return False, '已经在列表里了。'
    state['project_roots'].append(text)
    return True, f'已加入：{text}'


def add_host(state, host):
    """Returns (ok, message). Accepts a bare host name or a pasted address; keeps only the host name."""
    text = str(host).strip().casefold()
    text = re.sub(r'^[a-z][a-z0-9+.-]*://', '', text).split('/')[0].split('?')[0].split('#')[0].rsplit('@', 1)[-1]
    text = text.split(':')[0].strip('.')
    if not text or not HOST_PATTERN.fullmatch(text) or '.' not in text:
        return False, '请填网站地址，例如 github.com。'
    if text in state['web_hosts']:
        return False, '已经在列表里了。'
    if len(state['web_hosts']) >= MAX_HOSTS:
        return False, f'最多放 {MAX_HOSTS} 个网站。'
    state['web_hosts'].append(text)
    return True, f'已加入：{text}'


def remove_host(state, host):
    if host in state['web_hosts']:
        state['web_hosts'].remove(host)
        return True
    return False


def image_ready(image=IMAGE):
    try:
        if image != IMAGE:
            # A user-provided workbench image is never rebuilt from our Dockerfile.
            return subprocess.run(['docker', 'image', 'inspect', image], capture_output=True, timeout=60,
                                  **hidden_subprocess_kwargs(module=subprocess)).returncode == 0
        finished = subprocess.run(
            ['docker', 'image', 'inspect', '--format', '{{ index .Config.Labels "veil.workbench.network-lock" }}/{{ index .Config.Labels "veil.workbench.install-env" }}',
             image], capture_output=True, text=True, timeout=60,
            **hidden_subprocess_kwargs(module=subprocess))
        return finished.returncode == 0 and finished.stdout.strip() == '1/1'
    except (OSError, subprocess.SubprocessError):
        return False


def build_image(root=ROOT, image=IMAGE):
    print('第一次要做运行环境（需要联网，几分钟），请别关窗口 ……', flush=True)
    try:
        return subprocess.run(['docker', 'build', '-t', image, '-f', str(Path(root) / 'docker' / 'workbench.Dockerfile'),
                               str(Path(root) / 'docker')]).returncode == 0
    except OSError:
        return False


def remove_folder(state, folder):
    if folder in state['project_roots']:
        state['project_roots'].remove(folder)
        return True
    return False


def status(root=ROOT):
    """What the settings page shows."""
    state = load_state(root)
    return {**state, 'image_ready': image_ready(state['container_image'])}


def show(state):
    print('\n工作台现在：' + ('开' if state['enabled'] else '关'))
    print('可以动的项目文件夹：' + ('（还没设置，只能用工作台自己的文件夹）' if not state['project_roots'] else ''))
    for item in state['project_roots']:
        print('  ' + item)


def main():
    state = load_state()
    while True:
        show(state)
        print('\n1 开启   2 关闭   3 加一个项目文件夹   4 清空项目文件夹   0 退出（改完要重开 Peter）')
        choice = input('选哪个：').strip()
        if choice == '1':
            image = state['container_image']
            if not image_ready(image):
                if image != IMAGE:
                    print('你配置的自定义工作台镜像不存在；不会用默认 Dockerfile 覆盖它，请先自行构建。')
                    continue
                if not build_image(image=image):
                    print('运行环境没做成功（需要先装好并打开 Docker Desktop），没有开启。')
                    continue
            state['enabled'] = True
            save_state(state)
            print('已开启。')
        elif choice == '2':
            state['enabled'] = False
            save_state(state)
            print('已关闭。')
        elif choice == '3':
            ok, message = add_folder(state, input('把项目文件夹拖进来或粘贴路径：'))
            if ok:
                save_state(state)
            print(message)
        elif choice == '4':
            state['project_roots'] = []
            save_state(state)
            print('已清空。')
        elif choice == '0':
            return
        else:
            print('请输入 0 到 4。')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        log = ROOT / '.local-tmp' / 'workbench-switch-error.txt'
        log.parent.mkdir(exist_ok=True)
        log.write_text(traceback.format_exc(), encoding='utf-8')
        print('出错了，已存到 .local-tmp\\workbench-switch-error.txt', file=sys.stderr)
    input('按回车关闭窗口 ……')
