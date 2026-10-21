# 047 · Windows 桌面版 0.1

## 目标

Lan 可以在 Windows 桌面拖动、调整大小的透明 Peter 上点击展开/收起真实聊天，继续使用现有 Python Agent 的聊天记录、权限、记忆和预算；Memory / Ombre、Lab 桌面入口明确不可用。

## 当前状态

- 基准 main `29f2379ae7aace048001991d75628deb2e038fda`，分支 `ticket/047-windows-desktop`。新增 `desktop/`、源码启动/安装入口与 Windows 构建工作流；桌面无桥接的 preload 只添加禁用入口、布局和语音不可用说明。原网页、Agent 核心及数据目录不变，可连接已有安装。
- 复用调查（2026-10-21）：[Electron](https://github.com/electron/electron) 123449 stars、当日仍有提交，MIT；直接使用其窗口、安全隔离和打包生态，不复制桌宠核心。[固定源码](https://github.com/electron/electron/blob/ffbe71f787a7948ebe9add39ab170febf00134a6/shell/browser/native_window_views.cc#L294) 的 `NativeWindowViews` 为透明窗口设置 `kTranslucent`，无框时不显示阴影；固定 [LICENSE](https://github.com/electron/electron/blob/ffbe71f787a7948ebe9add39ab170febf00134a6/LICENSE) 已保留，分发包保留运行时许可证。Windows 运行库较大，但可独立打包，不向原 Agent Python 环境增加 GUI 依赖。
- 比较：[DyberPet](https://github.com/ChaozhongLiu/DyberPet) 1003 stars、2026-10-16 最近推送，GPL-3.0；仅比较桌宠形态，不复制代码或素材，完整宠物系统超出本单范围。[pywebview](https://github.com/r0x0r/pywebview/blob/master/LICENSE) 为 BSD-3-Clause，允许复用，但当前方案采用可独立分发的 Electron 壳，复用现有 HTTP 网页，不再向 Python 环境安装 GUI/渲染依赖。
- `desktop/assets/pep.png` 是 Lan 提供的 PNG 原文件，SHA256 `24cd6623a4cdfb1a9e76f4f481dc7613140e38d00059efe1032c9fe090bcc4bf`，未重画或改图，自带透明通道。
- 已实现静态透明形象、拖动、菜单大小切换、透明像素鼠标转发、单实例、位置/大小保存、原服务鉴权连接/统一启动器复用、真实网页窗口收放、禁用入口。桌面退出保留后台服务，聊天里的原退出按钮负责停服务；支持重新连接。
- 本地 Node HTTP 接入 2 项通过；原网页消息节奏/语音生命周期检查通过；真实 Windows Electron 窗口自动检查通过（虚构服务：历史、单次发送、拒绝审批、收放、大小、沙箱和不可用入口）。已生成 `VEIL.exe` 完整文件夹。此证据不是 Lan 的真实 Windows 验收，也没有调用真实/付费模型。
- 分发包是 Windows x64 桌面壳：需要已有可用 Python Agent 文件夹，首次点击选择；没有打包私人配置、聊天或记忆，没有捆绑完整 Python Agent。麦克风/语音输入未接通。真实跨屏/DPI、透明点击、重启及真实服务体验待验收。

## 验收步骤

1. 完整解压运行文件夹，双击 `VEIL.exe`，确认透明原图能拖动、切换大小，透明空白不挡桌面；聊天窗口可拖动缩放、点击 Peter 收放。
2. 选择原 Agent 目录，确认旧聊天在；正常聊天一次、拒绝一次原有需审批操作，收放后无重复发送。Memory / Ombre、Lab 显示 Coming Soon 且不可点击。
3. 退出并重开桌面版，确认记录及位置大小保留；在聊天停止服务后用“重新连接聊天”恢复。避免真实敏感数据和额外消费。未通过不得标记体验完成。
