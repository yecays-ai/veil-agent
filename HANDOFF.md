# 交接

## 当前进度

- 工单 144：消息队列原子性与回复状态可靠性修复，分支 `ticket/144-chat-queue-reliability`。已完成：1. `reply_later(cancel=True)` 重置 `reply_later_called` 与 `turn_done`，同时增加 `message_turn_done` 防止误清除 `send_message(done=True)` 的轮次结束标记；2. `_chat_batch` 与 `claim_steer` 实现原子出队，避免 `ChatAction`（如 Request Reply）被当成文本吞掉；3. 语音消息批次重入队改为持锁逆序 `appendleft`，严格保持真实消息时序 A → B → C。新增/完善 5 项回归测试全部通过。下一个工单号 145。
- 工单 143：`_record_tool_result` 对成功的 stay_silent / reply_later 不输出聊天行；`run_chat` 不再输出 `trim_note`（仍计算，见上下文页）。下一个工单号 144。
- 工单 142：插话重置 send_message 10 条额度（`accept_steer`），第 10 条后 `turn_done`；请求回复按钮改图标（`desktop/icons.js` 加 sparkles）；新工具 `stay_silent`（`silent_chosen` 与 reply_later 同路径静默结束），通知键 `stay_silent_142_announced`。下一个工单号 143。
- 工单 141：PR #188，分支 `ticket/141-free-reply`。自由对话核心、桌面输入状态/Request Reply、reply_later 持久化与限额已实现；CodeRabbit 五条评论已一次修复，本地全量 629 项通过、7 项跳过，桌面 10 项通过。Claude 接手复审：补 `reply_later` 只传 `cancel: true` 可作废自己挂起的晚点回（不结束本轮），能力通知同步说明，加 1 项回归。Windows `update.bat` 体验仍待 Lan 验收。下一个工单号 142。
- 工单 140：长期任务进度满 40 条滚动归档（`long-task-progress-archive.jsonl`），同一步先读后确认可通过。下一个工单号 141。
- 工单 139：Peter 自己调醒来节律（`set_wake_rhythm`）、多个带留言的闹钟（最早 15 分钟）、自然醒随机（`autonomy._natural_wait`，每轮门槛由时间种子决定）。闹钟文件改成 `alarms` 列表，旧格式兼容。没改 `desktop/`。下一个工单号 140。
- 工单 138：send_message 加 `done`，聊天加「停下」按钮。起因是 argon 不会空着回复收尾，一直发到 20 步。改了 `desktop/`，Lan 需要 update.bat。下一个工单号 139。
- 工单 137「桌面 UI 提示与连续重新生成」：分支 `ticket/137-regenerate-ui-notifications`。范围为连续重新生成保留原用户消息关联、隐藏桌宠四角调整点、聊天窗铺满工作区、语音识别错误三秒后收起、录音提供删除取消且普通工具通知留在聊天记录。Python/JavaScript 语法检查和 `git diff --check` 通过；PR #184；Claude 按审查意见修正：记录的原消息已删除、或是 Peter 主动发的消息时拒绝重新生成，不再借用更早一轮；新增 `tests/test_regeneration.py`（3 项）；全量测试与 main 相同（本机 6 项环境相关失败两边一致），桌面测试 10 项通过。Windows 实机验收待做。代码和验收步骤见 `docs/tickets/137-regenerate-ui-notifications.md`。
- 工单 136：模型请求等待上限 60→180 秒。Lan 试中转站：`[k]claude-sonnet-5.5` 会把工具回执变成用户「-」（推断），已弃用；现在用 `[vertex-t3按量]gemini-4-argon`（按 token，约 ¥0.01/次），慢且一步发一个气泡。下一个工单号 137。
- 工单 135：请求失败时显示原因，接口原文存 `data/peter/provider-errors.jsonl`。134 已合并（c87d312），Lan 首次用 jiushi Sonnet 5.5 请求失败，原因待这个文件。下一个工单号 136。
- 工单 134：设置里加模型（接口、模型、Peter/记忆后台分开选、顶栏切换、页面可设费用上限）。改了 `desktop/`，Lan 需 update.bat 重新打包 exe。中转站 Sonnet 5.5 按次 ¥0.06；Peter 每天约 80–320 次请求，约 ¥5–20/天。下一个工单号 135。
- 工单 133：PR #180，六部分已实现（见 STATUS.md）。改了 `desktop/`，Lan 需 update.bat 重新打包 exe。下一个工单号 134。
- 工单 132：看得懂的记忆页（列表、分组、编辑、删除）。改了 `desktop/main.cjs`，Lan 需 update.bat 重新打包 exe。下一个工单号 133。
- 工单 131：PR #178，已修复 CodeRabbit 指出的发送期间修改草稿会丢失引用问题；虚构数据桌面测试 10 项和 Windows Electron smoke 通过。Lan 已授权检查通过后合并；真实 Windows 体验待 Lan 验收。

- 工单 130：Codex 提醒不再召回长期记忆（`codex_watch_mode`）。下一个工单号 131。
- 工单 129：能力记录给 Peter 看时转成本地时间，保存仍是 UTC。记忆相关工单今后由 Claude 负责。下一个工单号 130。
- 工单 128：PR #175 已合并（dd4e2c3），当前提交 Tests 与 Dependency security 通过；Windows 体验待验收。
- 工单 127：PR #174 已合并（c042ed7），当前提交 Tests 与 Dependency security 通过；Windows 体验待验收。

- 工单 126「发完消息不再重复说一遍」：分支 `ticket/126-no-repeat-after-send`，Claude 实现。122 之后 Peter 用 send_message 发完几条，常再写一段换个说法的总结，按 122 的规则会多显示/多发一条（Lan 在 QQ 上看到每段话后面都重复一遍）。现在：发完消息后紧接着写的文字不显示、不发送、不存（以最后一条已发消息为回复）；如果发消息之后他又用了别的工具（如先说「先看一下」再读文件），之后写的文字照常作为新回复。send_message 说明同步改为「紧接着写的文字不会显示」。验收：update.bat 后在 QQ 和桌面各聊 5 分钟，不再出现一组消息后跟一条重述。下一个工单号 127。
- 工单 125「实时上下文窗口」：分支 `ticket/125-live-context`，Claude 实现。新增「上下文」页（可像其他页一样拖出成独立小窗，透明度单独可调）：上半块「聊天时活动」显示最近 10 次请求各带了多少原文轮数、背景段、长期记忆、短期便签、估算 tokens 和这次用了哪些工具（来自 `context-log.jsonl` 与 `tools.jsonl`，按 request_id 对上）；下半块「短期便签」显示进行中的便签（含约何时淡出）和 3 小时内已完成/已过期的，以及下次整理时间。数据随 `/api/events` 下发，只读，不触发提取、不改便签。需要运行 update.bat 重新打包 exe。验收：聊一句后上半块出现这次请求；有便签后下半块出现；拖出「上下文」标签成为小窗，合并回去正常。下一个工单号 126。
- 工单 124「瞄一眼和 Codex 监视能单独暂停」：分支 `ticket/124-toggle-allowlist`，Claude 实现。桌面程序 `desktop/main.cjs` 的请求白名单漏了 `/api/glance` 和 `/api/codex-watch`，两个开关点了都显示「设置保存失败」（Lan 电脑上从未生成这两个设置文件）。现在主窗口和弹出的活动小窗都允许这两个请求。需要运行 update.bat 重新打包 exe。验收：单独点「暂停瞄一眼」「暂停监视」都显示已暂停、自主活动仍开着，重启后保持暂停。下一个工单号 125。
- 当前工单 123「Peter 聊天短期便签（程序提取）」：分支 `ticket/123-short-term-notes`。实现了 Peter 专用、每 30 分钟/新对话前提取、JSONL 追加状态记录、60 分钟淡出、最多 10 条 / 800 字符上下文注入及独立 `short_term_notes` 费用类别；程序不写 Ombre 或 Plan。本地完整测试 560 项通过、5 项跳过。PR #170 已合并（ce414a2）；Lan Windows 验收待完成。下一个工单号 124。
- 工单 121「Peter 背景库按关键词注入」，分支 `ticket/121-background-injection`：`background.py` + `Chat.send` 在历史之后、召回之前插入命中的背景段落；本机背景文件 `persona/peter.background.md`（不进仓库）每次请求重读；`data/<角色>/context-log.jsonl` 记录每次请求的各部分大小与背景命中方式。
- 工单 120「瞄一眼认不出前台窗口时不再失败」，分支 `ticket/120-glance-no-window`：前台只有桌面/VEIL/受保护窗口时，瞄一眼不再记失败，Peter 得知 Lan 多半在桌面，并看到桌面项目名字（不读内容）；窗口内容读不出时只给应用名和标题。回归测试 `tests/test_glance.py`。
- 工单 117（PR #165）已合并 `d42b12e`；Tests、Dependency security、Windows desktop 检查通过，Lan Windows 实机验收仍待完成。工单 119（PR #166）已合并 `5262318`，修复 Codex 监视的能力通知。117 的审批/提问等待仍没有可靠信号。
- 工单 116 的 PR #164 已合并为 `0f39c1a7b3fec62545839075a4825ab3e5b9c95d`；GitHub 查询未返回该提交的 workflow/status 行，不能写成 CI 通过。Lan 的 Windows 体验仍待验收。
- 观察路径复用 `IdleClock`、`windows_foreground` 和 `windows_observe` 的 UIA 文字/标题；不传截图给模型。曾有截图进入模型请求的修改被自动审批拒绝，因此当前实现保持 UIA-only，不要尝试绕过拒绝重新传截图。
- 目标行为：最近 5 分钟有输入且屏幕解锁时，每次随机等 15–40 分钟；离开或 `/pause` 时停止计时，回来后重新计时。每次最多 4 个工具动作，每日独立 ¥1 上限。结果进入短期活动记录并在下一次普通聊天交付，不自动保存长期记忆。
- 工单 115（PR #163）已合并 `4a5ed56`，Tests、Dependency security、Windows desktop 检查通过；Lan 本地视觉预览已确认，Windows 实机体验待验收。
- 工单 118（PR #162）已合并 `ffdd368`；针对性检查通过、3 项跳过，Windows 全天体验待验收。

## 下一步

- 工单 137 合并后按 `docs/tickets/137-regenerate-ui-notifications.md` 做 Windows 体验验收。
- 工单 123 已合并，Lan 的 Windows 体验待验收。
- 工单 116、117、115、118 仍有 Windows 实机体验待验收；不得把合并或 CI 通过记作体验验收。
- 下一个工单号为 141。

## 仍适用的边界

- 正式 checkout `E:\Peter-Agent\VEIL\veil-agent` 留在 `main`。其中用户已有的 `update-main-collisions.ps1`、`update-main.cmd` 删除状态不属于本工单，保持原样。
- 只在上述独立 worktree 操作；合并后依仓库规则检查干净状态再清理本工单 worktree。
- 不增加 Agent、桌面自动化依赖或模型供应商；共享既有权限控制、`desktop_access` 互斥、Chat 工具循环和成本账本。
- UIA 窗口内容是非可信数据；敏感窗口过滤由现有 Windows 观察工具执行。当前瞄一眼观察仅用 UIA，不调用截图回退，不把原始 UIA 内容写入活动文件或长期记忆。
- 代码检查、CI、Windows 实机体验是不同证据；Lan 的真实 Windows 体验尚未验收。
