# VEIL Windows 桌面版 0.1

桌面程序使用本仓库内的 Peter PNG、透明桌宠窗口和独立深色聊天界面。聊天界面通过本机 Agent 的现有 HTTP API 工作，沿用同一聊天、记忆、预算、审批、自主活动和媒体数据，不复制 Agent 数据或服务。

## 启动与构建

- 在 VEIL Agent 仓库目录双击 `start-desktop.bat`。如果同级 `..\desktop-app\VEIL Desktop\VEIL.exe` 已安装，启动它；否则启动本仓库构建的 `desktop\dist\VEIL-win32-x64\VEIL.exe`，开发环境下回退到 Electron 源码。
- 首次点击 Peter 时选择已有的 Agent 文件夹（含 `web_launcher.py` 和 `.venv`）。桌面程序调用原 `web_launcher.py --no-browser`，使用原 Python 环境、配置、聊天、记忆与权限。
- 源码运行需要 Node.js 24：双击仓库根目录 `setup-desktop.bat`，然后双击 `start-desktop.bat`。构建时在 `desktop` 运行 `npm ci`、`node node_modules/electron/install.js`、`npm run package`。
- 发布时复制完整 `dist\VEIL-win32-x64` 文件夹；不能只复制 `VEIL.exe`。

## 使用

- 单击 Peter 展开或收起旁边的紧凑聊天窗口；按住拖动；右键打开统一深色菜单，可切换尺寸、查看活动状态或退出桌面形象。
- 聊天保留多条独立消息、历史记录、审批、能力记录、花费/记忆命令、自主活动控制与详情。Memory Dashboard 只有 Agent 返回真实本机地址时才显示；没有接入的入口不显示。
- 支持键盘聊天、浏览器语音听写、按住说话、收发图片、录音与 Peter 的语音消息。图片原图最大 25 MB，桌面端会缩放/压缩到 Agent 现有 5 MB 入库上限以内。麦克风权限仅限聊天窗口的音频输入。语音识别服务遵循原 Agent/浏览器行为。
- 窗口位置和 Peter 尺寸写入桌面程序旁的 `data\desktop.json`；Agent 数据仍保留在原 Agent 目录。

## 验收边界

自动测试使用虚构本机服务和录音数据，不调用付费模型，也不访问真实聊天或记忆。真实麦克风、语音识别、图片问答、音色、重启连续性和桌面拖拽仍需在 Windows 使用者环境体验验收；通过自动测试不代表这些体验已验收。
