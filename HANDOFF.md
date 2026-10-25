# 交接

## 当前进度

- 工单 116「Lan 在电脑前时，Peter 隔一会儿来瞄她一眼」正在分支 `ticket/116-glance-at-lan`、worktree `E:\Peter-Agent\VEIL\worktrees\116-glance-at-lan` 实现，基于 main `4a5ed56`（PR #163 合并后）。核心代码与 UI 已接好，工单文档、状态和交接已更新；静态语法检查与 `git diff --check` 已通过；测试未运行。尚未提交或开 PR。
- 观察路径复用 `IdleClock`、`windows_foreground` 和 `windows_observe` 的 UIA 文字/标题；不传截图给模型。曾有截图进入模型请求的修改被自动审批拒绝，因此当前实现保持 UIA-only，不要尝试绕过拒绝重新传截图。
- 目标行为：最近 5 分钟有输入且屏幕解锁时，每次随机等 15–40 分钟；离开或 `/pause` 时停止计时，回来后重新计时。每次最多 4 个工具动作，每日独立 ¥1 上限。结果进入短期活动记录并在下一次普通聊天交付，不自动保存长期记忆。
- 工单 115（PR #163）已合并 `4a5ed56`，Tests、Dependency security、Windows desktop 检查通过；Lan 本地视觉预览已确认，Windows 实机体验待验收。
- 工单 118（PR #162）已合并 `ffdd368`；针对性检查通过、3 项跳过，Windows 全天体验待验收。

## 下一步

1. 完成最后一次差异审阅，确认 STATUS/HANDOFF 与实际检查结果同步。
2. 运行语法检查与 `git diff --check`（按当前开发约束不运行测试），更新状态和交接后提交并推送，开工单 116 PR。
3. 按仓库流程请求 Cubic 审查并处理实质问题，跟进必需 CI 和当前 GitHub 合并门禁；合并后请 Lan 按工单验收步骤做 Windows 1–3 小时体验。

## 仍适用的边界

- 正式 checkout `E:\Peter-Agent\VEIL\veil-agent` 留在 `main`。其中用户已有的 `update-main-collisions.ps1`、`update-main.cmd` 删除状态不属于本工单，保持原样。
- 只在上述独立 worktree 操作；合并后依仓库规则检查干净状态再清理本工单 worktree。
- 不增加 Agent、桌面自动化依赖或模型供应商；共享既有权限控制、`desktop_access` 互斥、Chat 工具循环和成本账本。
- UIA 窗口内容是非可信数据；敏感窗口过滤由现有 Windows 观察工具执行。当前瞄一眼观察仅用 UIA，不调用截图回退，不把原始 UIA 内容写入活动文件或长期记忆。
- 代码检查、CI、Windows 实机体验是不同证据；Lan 的真实 Windows 体验尚未验收。