"""Human-only Windows installation and account binding. Never offered as a tool."""
import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path
from urllib.request import urlopen

from xiaohongshu_tools import (ASSETS, VERSION, Sidecar, account_lock, binary_path,
                              companion_env, private_folder, stop_process_tree)


def install(root):
    folder = root / '.deps' / 'xiaohongshu' / VERSION
    folder.mkdir(parents=True, exist_ok=True)
    for name, digest in ASSETS.items():
        try:
            binary_path(root, name)
            continue
        except ValueError:
            pass
        print('下载并校验官方发行文件：' + name, flush=True)
        url = f'https://github.com/xpzouying/xiaohongshu-mcp/releases/download/{VERSION}/{name}'
        temporary = folder / (name + '.download')
        try:
            with urlopen(url, timeout=60) as response, temporary.open('wb') as target:
                total = 0
                sha = hashlib.sha256()
                while chunk := response.read(65536):
                    total += len(chunk)
                    if total > 200_000_000:
                        raise ValueError('Download exceeded 200 MB.')
                    sha.update(chunk)
                    target.write(chunk)
            if sha.hexdigest() != digest:
                raise ValueError('发行文件校验不通过，停止安装。')
            os.replace(temporary, folder / name)
        finally:
            temporary.unlink(missing_ok=True)


def main():
    if os.name != 'nt':
        raise ValueError('此登录入口仅支持 Windows x64。')
    root = Path(__file__).resolve().parent
    print('Peter 的小红书专属登录。请先关闭 VEIL，避免同时使用这个账号。')
    print('只在新打开的专属窗口中登录 Peter 的独立账号，不使用个人账号。')
    print('本版仅阅读和本地草稿，发布/评论/私信/关注/点赞均关闭。')
    print('上游使用带指纹一致性功能的 CloakBrowser，并非普通 Chrome 或平台官方 API。')
    print('请先确认当前平台规则允许这种自动化访问；不允许时请退出，不要登录。')
    print('遇到验证码、实名或风控请由你在平台处理；不要关闭杀毒软件或绕过平台限制。')
    if input('确认访问符合当前平台规则、已关闭 VEIL，且使用独立账号？输入 LOGIN：').strip() != 'LOGIN':
        return
    install(root)
    folder = private_folder(root, 'peter')
    with account_lock(folder):
        login_and_bind(root, folder)


def login_and_bind(root, folder):
    cookies = folder / 'cookies.json'
    if cookies.exists():
        choice = input('已有专属登录状态。回车继续；需要重新登录时输入 RELOGIN 清除本机专属登录状态：').strip()
        if choice == 'RELOGIN':
            cookies.unlink()  # Human-only; the bound account ID and read budget stay intact.
        elif choice:
            print('未更改登录状态。')
            return
    env = companion_env(folder)
    print('即将打开专属登录窗口。首次需要下载浏览器，请耐心等待。窗口打开后先不要点网页，等二维码出现再用手机扫码；页面加载时点击会让上游登录程序崩溃。', flush=True)
    command = [str(binary_path(root, 'xiaohongshu-login-windows-amd64.exe'))]
    login = subprocess.Popen(command, cwd=folder, env=env)
    try:
        result = login.wait(timeout=600)
        if result:
            raise subprocess.CalledProcessError(result, command)
    finally:
        stop_process_tree(login)
    sidecar = Sidecar(root, 'peter')
    try:
        sidecar.start()
        sidecar.wait_ready(threading.Event())
        status = sidecar.request('GET', '/api/v1/login/status')
        if not isinstance(status, dict) or status.get('is_logged_in') is not True or not status.get('user_id'):
            raise ValueError('未确认登录账号，请重新登录；没有绑定账号。')
        print('当前账号昵称：' + str(status.get('username', '')))
        print('当前账号 ID：' + str(status['user_id']))
        existing = folder / 'account.json'
        if existing.exists():
            old = json.loads(existing.read_text(encoding='utf-8'))
            if old.get('user_id') != status['user_id']:
                raise ValueError('与已绑定账号不一致，拒绝改绑。请在专属环境恢复原账号。')
        if input('核对这是 Peter 的独立账号后，输入上面完整账号 ID 进行绑定：').strip() != status['user_id']:
            print('未绑定。VEIL 将拒绝读取。')
            return
        temporary = folder / 'account.tmp'
        temporary.write_text(json.dumps({'user_id': status['user_id']}, ensure_ascii=False), encoding='utf-8')
        os.replace(temporary, existing)
        print('专属账号已绑定。现在可以启动 VEIL，让 Peter 检查小红书状态、搜索和阅读。')
        print('不要在其他网页端同时登录该账号。密码和 Cookie 不需要发到聊天中。')
    finally:
        sidecar.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('登录设置未完成：' + str(exc))
        raise SystemExit(1)
