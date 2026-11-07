"""Ticket 239: turn Peter's workbench on or off and choose which project folders it may work in.
Writes data/<character>/workbench.json (git-ignored), so updating the repo never undoes it. Restart Peter afterwards."""
import json
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHARACTER = 'peter'
IMAGE = 'veil-workbench'
KEYS = ('enabled', 'project_roots', 'container_image')


def settings_path(root=ROOT):
    return Path(root) / 'data' / CHARACTER / 'workbench.json'


def load_state(root=ROOT):
    path = settings_path(root)
    if not path.exists():
        return {'enabled': False, 'project_roots': [], 'container_image': IMAGE}
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    return {'enabled': bool(data.get('enabled', False)), 'project_roots': list(data.get('project_roots', [])),
            'container_image': str(data.get('container_image', IMAGE))}


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
    data = (Path(root) / 'data').resolve()
    if path == data or data in path.parents or path in data.parents:
        return False, '这个文件夹包含或位于 Peter 自己的数据文件夹里，不能放进去。'
    text = str(path)
    if text in state['project_roots']:
        return False, '已经在列表里了。'
    state['project_roots'].append(text)
    return True, f'已加入：{text}'


def image_ready(image=IMAGE):
    try:
        return subprocess.run(['docker', 'image', 'inspect', image], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def build_image(root=ROOT, image=IMAGE):
    print('第一次要做运行环境（需要联网，几分钟），请别关窗口 ……', flush=True)
    return subprocess.run(['docker', 'build', '-t', image, '-f', str(Path(root) / 'docker' / 'workbench.Dockerfile'),
                           str(Path(root) / 'docker')]).returncode == 0


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
            if not image_ready() and not build_image():
                print('运行环境没做成功（需要先装好并打开 Docker Desktop），没有开启。')
                continue
            state['enabled'], state['container_image'] = True, IMAGE
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
