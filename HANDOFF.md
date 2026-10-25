# 交接

## 当前进度

- 当前工单 117「Peter 帮 Lan 盯着 Codex」的 PR #165 已打开：`https://github.com/yecays-ai/veil-agent/pull/165`，分支 `ticket/117-watch-codex`，基底为 main `0f39c1a7b3fec62545839075a4825ab3e5b9c95d`。只读监视、设置开关、单次模型提醒与弹窗/QQ 分流已实现。静态 Python/JavaScript 语法检查和 `git diff --check` 通过，测试未运行；PR CI 与 Lan Windows 实机验收待确认。审批/提问等待没有可靠信号，不能猜测。
- 工单 116 的 PR #164 已合并为 `0f39c1a7b3fec62545839075a4825ab3e5b9c95d`；GitHub 查询未返回该提交的 workflow/status 行，不能写成 CI 通过。Lan 的 Windows 体验仍待验收。
- 观察路径复用 `IdleClock`、`windows_foreground` 和 `windows_observe` 的 UIA 文字/标题；不传截图给模型。曾有截图进入模型请求的修改被自动审批拒绝，因此当前实现保持 UIA-only，不要尝试绕过拒绝重新传截图。
- 目标行为：最近 5 分钟有输入且屏幕解锁时，每次随机等 15–40 分钟；离开或 `/pause` 时停止计时，回来后重新计时。每次最多 4 个工具动作，每日独立 ¥1 上限。结果进入短期活动记录并在下一次普通聊天交付，不自动保存长期记忆。
- 工单 115（PR #163）已合并 `4a5ed56`，Tests、Dependency security、Windows desktop 检查通过；Lan 本地视觉预览已确认，Windows 实机体验待验收。
- 工单 118（PR #162）已合并 `ffdd368`；针对性检查通过、3 项跳过，Windows 全天体验待验收。

## 下一步

1. 检查 PR #165 最新提交的 CI 和代码评审，只修复实质问题。
2. PR 保持开放，等 Lan Windows 实机验收；不要把 CI 通过当作体验验收。
3. PR 检查、CI、Windows 实机体验分别记录；不要把未运行的测试或未回传的状态写成通过。

## 仍适用的边界

- 正式 checkout `E:\Peter-Agent\VEIL\veil-agent` 留在 `main`。其中用户已有的 `update-main-collisions.ps1`、`update-main.cmd` 删除状态不属于本工单，保持原样。
- 只在上述独立 worktree 操作；合并后依仓库规则检查干净状态再清理本工单 worktree。
- 不增加 Agent、桌面自动化依赖或模型供应商；共享既有权限控制、`desktop_access` 互斥、Chat 工具循环和成本账本。
- UIA 窗口内容是非可信数据；敏感窗口过滤由现有 Windows 观察工具执行。当前瞄一眼观察仅用 UIA，不调用截图回退，不把原始 UIA 内容写入活动文件或长期记忆。
- 代码检查、CI、Windows 实机体验是不同证据；Lan 的真实 Windows 体验尚未验收。
