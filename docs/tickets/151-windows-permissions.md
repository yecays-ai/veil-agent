# 工单 151：Peter 的 Windows 权限扩展

## 目标

让 Peter 在三项可分别关闭的授权内，直接读取 Lan 桌面上的普通文本文件、在独立 Chrome Guest 窗口打开公开网页，并读取 Windows 提供的当前媒体标题、歌手和播放状态。

## 当前状态

- 桌面文件：现有 `desktop_create_file` / `desktop_move_to_trash` 只允许 Peter 管理自己创建的部分文件；工作区的 `list_directory` / `read_text_file` 不访问桌面。本工单新增独立的桌面只读权限，仅限 Windows 已知桌面目录，可列出可见项目、按完整文件名搜索，并读取 UTF-8 `.txt` / `.md`，单文件上限 256 KB。敏感名称、隐藏项、符号链接、越界路径和不支持文件类型会被拒绝；没有写入、移动或删除接口。
- 浏览器：复用 `windows_list`、`windows_observe` 和现有受限截图能力。只把 Chrome 加入角色应用白名单，并只允许从 `windows_launch` 打开经过公开 HTTP(S) 校验的 URL；Chrome 使用 `--guest --new-window` 和独立随机临时用户数据目录，不读取 Lan 的个人 Chrome 配置、Cookie 或登录状态，也不复用或激活已有 Chrome 窗口。临时目录会在 Chrome 进程退出后清理。若策略关闭 Guest，`--incognito` 和隔离目录仍避免访问 Lan 的个人数据。程序只有观察到新窗口才报告成功；未安装、无法启动或 Guest 窗口未出现时会明确报错且不会自动重试。直接可执行文件/安装包 URL 被拒绝。Chrome 白名单不开放窗口管理或页面控件点击、表单输入、提交；Lab 预览继续使用独立隔离 Chromium，公开网页内容读取继续使用现有 `web_fetch`。本机标准 Chrome 安装可用；进程枚举因系统访问限制未能确认当时是否已有 Chrome 窗口。
- 音乐：Windows 提供 `GlobalSystemMediaTransportControlsSessionManager` 系统媒体会话接口；当前 Python 环境未安装 WinRT 投影，开发机当时没有检测到正在运行的音乐播放器。使用 MIT 许可的 PyWinRT `winrt-Windows.Media.Control` 3.2.1 及其锁定依赖；不读音频、不录音，也不提供播放控制。若当前会话没有元数据，工具如实返回无信息。
- 复用调查：GitHub `pywinrt/pywinrt`（MIT）通过其官方 `Windows.Media.Control` 投影直接读取 GSMTC；按项目源码签名复用 `request_async`、`get_current_session`、`get_playback_info` 与 `try_get_media_properties_async`，不复制实现代码。[固定源码版本](https://github.com/pywinrt/pywinrt/tree/52fd2a4fdcae2f08dd5a7a0fc8479ff24dadde62)。YASB 也使用该 Windows API 读取媒体会话；本工单不引入其桌面程序或服务。[参考实现](https://github.com/amnweb/yasb/blob/538959f4723d6f34f6194f01ebde89b06492ef4b/src/core/widgets/services/media/media.py)。

## 验收步骤

### A. 桌面文本只读

1. 在桌面新建一个普通 `.txt`，让 Peter 按文件名找到并读出内容；确认不需要打开记事本。
2. 让 Peter 尝试读取桌面外路径、敏感名称或较大的文件，确认都被拒绝；确认该能力没有改写、移动、删除工具。

### B. 公开网页

1. 让 Peter 打开一个公开网页，确认新 Chrome Guest 窗口显示该页、原有 Chrome 窗口保持原样；让他报告当前标题或可访问页面内容。需要画面观察时使用现有 `windows_observe`。
2. Lan 操作鼠标键盘时让 Peter 再打开网页，确认他先让位；尝试本地文件/私网/可执行文件 URL 和页面控件操作，确认程序拒绝。Chrome 不存在或无法启动时，确认 Peter 明确报告失败而不声称页面已打开。

### C. 音乐状态

1. 在支持系统媒体会话的播放器播放一首歌，询问 Peter 歌名、歌手和播放状态；暂停后再问，确认状态变为暂停。
2. 关闭或停止播放器后再问，确认他报告无当前会话/已停止；如果播放器没有向 Windows 发布元数据，确认他明确说无法读取且不猜歌名。
