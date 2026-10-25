# 交接

## 当前进度

- 工单 115 已按 Lan 确认的视觉方向实现。远端分支 `ticket/115-popup-danmaku` 基于最新 main `ffdd368`，PR #163 已创建，CodeRabbit 手动审查请求已发；本地 shell Git 因 GitHub DNS 不可解析，发布由 GitHub 接口完成。本地预览位于 `C:\Users\Administrator\.codex\visualizations\2026\10\25\01a17433-c932-74b3-9c37-40fafe38032c\ticket-115-preview`，由工单 HTML/CSS/JS 渲染、背景为模拟效果。语法与 diff 检查通过，单元测试未运行；必需 CI 和 Windows 实机体验待跟进。
- PR #162（工单 118）已合并为 `ffdd368`；配置、20 步工具上限、15 分钟运行上限和一次性通知已进入 main。118 项针对性检查通过、3 项跳过；本机 Node 文件服务用例未满足启动前置条件，Lan 全天体验待验收。
- 工单 114（PR #160，合并 `0d3b412`）：白名单应用普通操作免审批，审批可走 QQ；`desktop_create_file` 独占创建以避免覆盖。Lan 2026-10-25 已用 QQ 实测创建 5 个文件。
- 工单 113（PR #159，合并 `7c6cf18`）：批准后等待空闲再执行；38 项桌面交互/观察检查通过，Windows 回归仍待 Lan 确认。
- 工单 112（PR #158，合并 `cdf0860`）：白名单记事本交互已进入 main；Ubuntu/Windows Python 3.11/3.12 CI 与依赖安全检查通过。Lan 实机体验待验收。
- 工单 111 已通过 PR #157 合并为 `b64875d`；桌面观察功能 CI 通过，Lan 的 Windows 实机体验仍待进行。

## 下一步

1. Lan 跟进 PR #163 的 CodeRabbit 审查与必需 CI；只处理实质问题。
2. CI/审查通过后按 `docs/tickets/115-popup-danmaku.md` 做 Windows 实机验收，再决定合并。
3. PR 保持打开，等 Lan 按工单步骤做 Windows 实机验收，再决定合并。

## 仍适用的边界

- 正式 checkout `E:\Peter-Agent\VEIL\veil-agent` 留在 `main`。其中用户已有的 `update-main-collisions.ps1`、`update-main.cmd` 删除状态不属于本工单，保持原样。
- 本工单使用独立 worktree 与分支；发布时以合并 PR #162 后的最新 main `ffdd368` 为基准。
- 只有 Peter 配置启用 Windows 交互；初始只开放 `notepad`。不接受任意 exe、命令行、Shell 或 PowerShell；应用身份由可执行文件和进程路径白名单匹配。
- 所有桌面动作与 QQ 共用 `desktop_access`；近期人工输入、锁屏/安全桌面、暂停、窗口身份变化、UIA 目标不明或结果不确定时停止。不会自动重做结果不确定的动作。
- 文本、聊天、截图不进入工具活动明细；不新增模型供应商或预算。额外 UIA 工具步骤会使用现有请求的模型 token 和预算。
- UIA 操作没有在 Lan 的真实 Windows 桌面验证；模拟后端只能验证 VEIL 权限、路由、互斥和结果处理，不能替代实机体验。

## 仍有效的相关技术事实

- 工单 111 用 `PrintWindow` 观察后台窗口，并拒绝黑屏/单色截图；保留 `SENSITIVE_LABEL` 的整词匹配和窗口进程/创建时间身份校验。
- QQ 与普通 Windows 桌面工作共享 `desktop_access`，不得引入绕过锁的独立输入路径。
- 桌面版入口仍通过 `FilesystemTools` 共享实现接入；终端与 desktop `serve_chat` 均使用既有 Chat 工具循环、权限、预算和 `tools.jsonl` 活动账本。
- Windows UIA 依赖仍为 `uiautomation==2.0.29`；不得为了本工单加入第二套桌面自动化框架。
