# 交接

## 当前进度

- 工单 124「瞄一眼和 Codex 监视能单独暂停」：分支 `ticket/124-toggle-allowlist`，Claude 实现。桌面程序 `desktop/main.cjs` 的请求白名单漏了 `/api/glance` 和 `/api/codex-watch`，两个开关点了都显示「设置保存失败」（Lan 电脑上从未生成这两个设置文件）。现在主窗口和弹出的活动小窗都允许这两个请求。需要运行 update.bat 重新打包 exe。验收：单独点「暂停瞄一眼」「暂停监视」都显示已暂停、自主活动仍开着，重启后保持暂停。下一个工单号 125。
- 当前工单 123「Peter 聊天短期便签（程序提取）」：分支 `ticket/123-short-term-notes`。实现了 Peter 专用、每 30 分钟/新对话前提取、JSONL 追加状态记录、60 分钟淡出、最多 10 条 / 800 字符上下文注入及独立 `short_term_notes` 费用类别；程序不写 Ombre 或 Plan。本地完整测试 560 项通过、5 项跳过。PR 的当前 head 必须等 CodeRabbit 评审和必需 CI 全绿后才合并；Lan Windows 验收待完成。下一个工单号 124。
- 工单 121「Peter 背景库按关键词注入」，分支 `ticket/121-background-injection`：`background.py` + `Chat.send` 在历史之后、召回之前插入命中的背景段落；本机背景文件 `persona/peter.background.md`（不进仓库）每次请求重读；`data/<角色>/context-log.jsonl` 记录每次请求的各部分大小与背景命中方式。
- 工单 120「瞄一眼认不出前台窗口时不再失败」，分支 `ticket/120-glance-no-window`：前台只有桌面/VEIL/受保护窗口时，瞄一眼不再记失败，Peter 得知 Lan 多半在桌面，并看到桌面项目名字（不读内容）；窗口内容读不出时只给应用名和标题。回归测试 `tests/test_glance.py`。
- 工单 117（PR #165）已合并 `d42b12e`；Tests、Dependency security、Windows desktop 检查通过，Lan Windows 实机验收仍待完成。工单 119（PR #166）已合并 `5262318`，修复 Codex 监视的能力通知。117 的审批/提问等待仍没有可靠信号。
- 工单 116 的 PR #164 已合并为 `0f39c1a7b3fec62545839075a4825ab3e5b9c95d`；GitHub 查询未返回该提交的 workflow/status 行，不能写成 CI 通过。Lan 的 Windows 体验仍待验收。
- 观察路径复用 `IdleClock`、`windows_foreground` 和 `windows_observe` 的 UIA 文字/标题；不传截图给模型。曾有截图进入模型请求的修改被自动审批拒绝，因此当前实现保持 UIA-only，不要尝试绕过拒绝重新传截图。
- 目标行为：最近 5 分钟有输入且屏幕解锁时，每次随机等 15–40 分钟；离开或 `/pause` 时停止计时，回来后重新计时。每次最多 4 个工具动作，每日独立 ¥1 上限。结果进入短期活动记录并在下一次普通聊天交付，不自动保存长期记忆。
- 工单 115（PR #163）已合并 `4a5ed56`，Tests、Dependency security、Windows desktop 检查通过；Lan 本地视觉预览已确认，Windows 实机体验待验收。
- 工单 118（PR #162）已合并 `ffdd368`；针对性检查通过、3 项跳过，Windows 全天体验待验收。

## 下一步

- 工单 123：检查当前 PR head 的 CodeRabbit 评论及必需 CI；处理实质问题，全部通过后合并，再请 Lan 按工单步骤做 Windows 验收。
- 工单 116、117、115、118 仍有 Windows 实机体验待验收；不得把合并或 CI 通过记作体验验收。
- 下一个工单号为 125。

## 仍适用的边界

- 正式 checkout `E:\Peter-Agent\VEIL\veil-agent` 留在 `main`。其中用户已有的 `update-main-collisions.ps1`、`update-main.cmd` 删除状态不属于本工单，保持原样。
- 只在上述独立 worktree 操作；合并后依仓库规则检查干净状态再清理本工单 worktree。
- 不增加 Agent、桌面自动化依赖或模型供应商；共享既有权限控制、`desktop_access` 互斥、Chat 工具循环和成本账本。
- UIA 窗口内容是非可信数据；敏感窗口过滤由现有 Windows 观察工具执行。当前瞄一眼观察仅用 UIA，不调用截图回退，不把原始 UIA 内容写入活动文件或长期记忆。
- 代码检查、CI、Windows 实机体验是不同证据；Lan 的真实 Windows 体验尚未验收。
