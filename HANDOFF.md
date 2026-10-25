# 交接

## 当前进度

- 当前工单 121「Peter 背景库按关键词注入」，分支 `ticket/121-background-injection`：`background.py` + `Chat.send` 在历史之后、召回之前插入命中的背景段落；本机背景文件 `persona/peter.background.md`（不进仓库）每次请求重读；`data/<角色>/context-log.jsonl` 记录每次请求的各部分大小与背景命中方式。下一个工单号 122。
- 当前工单 120「瞄一眼认不出前台窗口时不再失败」，分支 `ticket/120-glance-no-window`：前台只有桌面/VEIL/受保护窗口时，瞄一眼不再记失败，Peter 得知 Lan 多半在桌面，并看到桌面项目名字（不读内容）；窗口内容读不出时只给应用名和标题。回归测试 `tests/test_glance.py`。
- 工单 117（PR #165）已合并 `d42b12e`；工单 119（PR #166）已合并 `5262318`，修复 Codex 监视的能力通知。117 的审批/提问等待仍没有可靠信号。
- 工单 116 的 PR #164 已合并为 `0f39c1a7b3fec62545839075a4825ab3e5b9c95d`；GitHub 查询未返回该提交的 workflow/status 行，不能写成 CI 通过。Lan 的 Windows 体验仍待验收。
- 观察路径复用 `IdleClock`、`windows_foreground` 和 `windows_observe` 的 UIA 文字/标题；不传截图给模型。曾有截图进入模型请求的修改被自动审批拒绝，因此当前实现保持 UIA-only，不要尝试绕过拒绝重新传截图。
- 目标行为：最近 5 分钟有输入且屏幕解锁时，每次随机等 15–40 分钟；离开或 `/pause` 时停止计时，回来后重新计时。每次最多 4 个工具动作，每日独立 ¥1 上限。结果进入短期活动记录并在下一次普通聊天交付，不自动保存长期记忆。
- 工单 115（PR #163）已合并 `4a5ed56`，Tests、Dependency security、Windows desktop 检查通过；Lan 本地视觉预览已确认，Windows 实机体验待验收。
- 工单 118（PR #162）已合并 `ffdd368`；针对性检查通过、3 项跳过，Windows 全天体验待验收。

## 下一步

0. 工单 121 合并后，Lan 退出 Peter 运行 update.bat 再重开；按工单验收第 2–7 步检查，一天后算背景的实际费用写进 STATUS。
1. 工单 120 CI 通过后合并，Lan 运行 update.bat 后在桌面或只开 VEIL 时验收一次瞄一眼。
2. 115–119 仍等 Lan Windows 实机验收；不要把 CI 通过当作体验验收。
3. PR 检查、CI、Windows 实机体验分别记录；不要把未运行的测试或未回传的状态写成通过。

## 仍适用的边界

- 正式 checkout `E:\Peter-Agent\VEIL\veil-agent` 留在 `main`。其中用户已有的 `update-main-collisions.ps1`、`update-main.cmd` 删除状态不属于本工单，保持原样。
- 只在上述独立 worktree 操作；合并后依仓库规则检查干净状态再清理本工单 worktree。
- 不增加 Agent、桌面自动化依赖或模型供应商；共享既有权限控制、`desktop_access` 互斥、Chat 工具循环和成本账本。
- UIA 窗口内容是非可信数据；敏感窗口过滤由现有 Windows 观察工具执行。当前瞄一眼观察仅用 UIA，不调用截图回退，不把原始 UIA 内容写入活动文件或长期记忆。
- 代码检查、CI、Windows 实机体验是不同证据；Lan 的真实 Windows 体验尚未验收。
