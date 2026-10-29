"""Apply the narrowly scoped VEIL page-wait patch to upstream v2.5.5."""
import sys
from pathlib import Path


def replace_once(root: Path, relative: str, before: bytes, after: bytes) -> None:
    path = root / relative
    source = path.read_bytes()
    count = source.count(before)
    if count != 1:
        raise SystemExit(f'{relative}: expected one pinned source line, found {count}')
    path.write_bytes(source.replace(before, after, 1))


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit('usage: patch_xiaohongshu_mcp_v2_5_5.py UPSTREAM_ROOT')
    root = Path(sys.argv[1]).resolve()
    login_path = root / 'xiaohongshu/login.go'
    login_source = login_path.read_bytes()
    newline = b'\r\n' if b'\r\n' in login_source else b'\n'
    replace_once(
        root,
        'xiaohongshu/login.go',
        b'"github.com/go-rod/rod"',
        b'"github.com/go-rod/rod"' + newline + b'\t"github.com/go-rod/rod/lib/proto"',
    )
    login = newline.join((
        b'func (a *LoginAction) CheckLoginStatus(ctx context.Context) (bool, error) {',
        '\t// 加超时保护：只是查登录态的快速检查，不应无限挂（登录扫码的等待在 Login/WaitForLogin 里）'.encode('utf-8'),
        b'\tpp := a.page.Context(ctx).Timeout(30 * time.Second)',
        b'\tpp.MustNavigate("https://www.xiaohongshu.com/explore").MustWaitLoad()',
    ))
    login_after = newline.join((
        b'func (a *LoginAction) CheckLoginStatus(ctx context.Context) (bool, error) {',
        '\t// 加超时保护：只是查登录态的快速检查，不应无限挂（登录扫码的等待在 Login/WaitForLogin 里）'.encode('utf-8'),
        b'\tpp := a.page.Context(ctx).Timeout(30 * time.Second)',
        b'\twaitDOMLoaded := pp.WaitNavigation(proto.PageLifecycleEventNameDOMContentLoaded)',
        b'\tpp.MustNavigate("https://www.xiaohongshu.com/explore")',
        b'\twaitDOMLoaded()',
        b'\tpp.MustWait(`() => {',
        b'\t\tconst user = window.__INITIAL_STATE__ && window.__INITIAL_STATE__.user;',
        b'\t\tconst info = user && user.userInfo && user.userInfo.value !== undefined ? user.userInfo.value : (user && user.userInfo);',
        b'\t\treturn !!info && (info.guest === true || !!(info.userId || info.user_id));',
        b'\t}`)',
    ))
    replace_once(
        root,
        'xiaohongshu/login.go',
        login,
        login_after,
    )
    replace_once(
        root,
        'xiaohongshu/feeds.go',
        b'deadline := time.Now().Add(8 * time.Second)',
        b'deadline := time.Now().Add(25 * time.Second)',
    )


if __name__ == '__main__':
    main()
