# 交接

## 当前进度

- 工单 114（Claude）：`windows_interaction._needs_approval` 只对 invoke/toggle/select 且控件名含保存/发送/提交/发布/覆盖、或祖先是覆盖确认框时要审批；set_text 不再审批。`Hub.approval` 会调用每个 `AwayBridge.approval_asked`，只在该渠道的回合内把问题发出；`poll_once` 在已发问且 `hub.pending` 存在时把最后一行的同意/不同意交给 `hub.answer`。新模块 `desktop_files.py`（`desktop_create_file`，'xb' 独占创建，Known Folder 路径）。`desktop_access.register_input_tracker` / `program_input_made` 让 QQ `IdleClock` 与 `DesktopInputMonitor` 互认程序输入（只在 Windows 真时钟上注册）。`desktop_create_file` 不在长期任务 `SIDE_EFFECT_TOOLS` 里，避免再出现无法核实的待办卡死。
- 工单 111 已通过 PR #157 合并为 `b64875d`；四项 Windows/Linux Python 3.11/3.12 CI 通过。Lan 的 Windows 实机观察验收仍待进行。
- 工单 112 在独立 worktree `E:\Peter-Agent\VEIL\worktrees\112-desktop-interaction`、分支 `ticket/112-desktop-interaction` 上实现，基于 main `b64875d`。PR #158 已创建并保持打开；Ubuntu/Windows Python 3.11/3.12 全量 CI 和依赖安全检查通过，CodeRabbit 审查仍在处理中。已有记事本程序白名单、启动/复用、窗口管理、精确 UIA 控件动作、文本核验、人工输入优先、共享桌面锁及活动记录接入。
- Microsoft winapp CLI 固定提交 `6564e7adba6d339f2b06868baca050e54687a2b3` 已审阅；只借用精确目标解析、显式 UIA pattern、动作前重查和桌面协调思路，没有增加依赖或复制代码。
- 视觉点击未开放：当前实现无法把截图坐标可靠绑定到窗口几何和布局版本；保留工单 111 的截图观察能力，目标不确定时不点。
- 已通过 `tests.test_windows_interaction` 与 `tests.test_windows_observer` 共 34 项；Python 编译检查通过。PR #158 的 Ubuntu/Windows Python 3.11/3.12 全量 CI 和依赖安全检查通过，CodeRabbit 审查仍在处理中。另一次 181 项工具/聊天/自主活动回归运行有本机依赖、Node 服务和 junction 权限导致的失败；不是完整通过。Lan Windows 实机验收尚未完成。当前 Python 环境未安装 `uiautomation`，没有做真实桌面动作。

## 下一步

1. 跟进 PR #158 的 CodeRabbit/Cubic 审查意见；仅对实质问题提交修复到同一分支。
2. 等待 Lan 按工单步骤完成 Windows 实机验收，并与自动检查分开记录。
3. PR 保持打开，等待 Lan 验收；不要自动合并。

## 仍适用的边界

- 正式 checkout `E:\Peter-Agent\VEIL\veil-agent` 留在 `main`。其中用户已有的 `update-main-collisions.ps1`、`update-main.cmd` 删除状态不属于本工单，保持原样。
- 不修改工单 111 尚未合并分支或旧 worktree；本工单独立基于其已合并提交 `b64875d`。
- 只有 Peter 配置启用 Windows 交互；初始只开放 `notepad`。不接受任意 exe、命令行、Shell 或 PowerShell；应用身份由可执行文件和进程路径白名单匹配。
- 所有桌面动作与 QQ 共用 `desktop_access`；近期人工输入、锁屏/安全桌面、暂停、窗口身份变化、UIA 目标不明或结果不确定时停止。不会自动重做结果不确定的动作。
- 文本、聊天、截图不进入工具活动明细；不新增模型供应商或预算。额外 UIA 工具步骤会使用现有请求的模型 token 和预算。
- UIA 操作没有在 Lan 的真实 Windows 桌面验证；模拟后端只能验证 VEIL 权限、路由、互斥和结果处理，不能替代实机体验。

## 仍有效的相关技术事实

- 工单 111 用 `PrintWindow` 观察后台窗口，并拒绝黑屏/单色截图；保留 `SENSITIVE_LABEL` 的整词匹配和窗口进程/创建时间身份校验。
- QQ 与普通 Windows 桌面工作共享 `desktop_access`，不得引入绕过锁的独立输入路径。
- 桌面版入口仍通过 `FilesystemTools` 共享实现接入；终端与 desktop `serve_chat` 均使用既有 Chat 工具循环、权限、预算和 `tools.jsonl` 活动账本。
- Windows UIA 依赖仍为 `uiautomation==2.0.29`；不得为了本工单加入第二套桌面自动化框架。
