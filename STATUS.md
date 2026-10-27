# 当前状态

更新：2026-10-28 UTC · Claude（工单 139）。

## 当前目标与现状

- 工单 139「Peter 自己决定怎么醒来」：分支 `ticket/139-wake-control`，Claude 实现，Lan 2026-10-28 同意。参考 Kli Space Wake Veil（1.0 仓库思路 + 2.0 设计稿），Python 重写。自然醒改为醒后安静、慢慢攒够才醒（平均约 90 分钟，多在 30–190 分钟）；新工具 `set_wake_rhythm`（quieter / silent / custom，带时长自动恢复）；`set_next_wake` 可排多个带留言的闹钟、最早 15 分钟，安静时也照响；错过的闹钟下次一起告诉他。Lan 的暂停、夜间安静、12 次/天、¥3/天不变。新增 `tests/test_wake_control.py`（7 项）；全量测试与 main 相同（本机 6 项环境相关失败两边一致）。未改桌面界面。验收见 `docs/tickets/139-wake-control.md`。下一个工单号 140。
- 工单 138「Peter 自己说完就停，Lan 可以叫停」：分支 `ticket/138-turn-stop`，Claude 实现，Lan 2026-10-28 同意。argon 一句晚安发了 19 条，一直跑到 20 步上限。send_message 加了可选的 `done`：他标为最后一条后，这一轮就结束（自主活动里忽略）。聊天「正在回复…」旁加「停下」按钮（`/api/stop`），在下一步开始前停下，已经发出的消息保留。新增 `tests/test_turn_stop.py`（7 项）；全量测试与 main 相同（本机 6 项环境相关失败两边一致），桌面测试通过。需要 update.bat 重新打包 exe。验收见 `docs/tickets/138-turn-stop.md`。PR #184（Codex 的 137）还开着；本单避开了它改的行。下一个工单号 139。
- 工单 137「桌面 UI 提示与连续重新生成」：已合并（PR #184），分支 `ticket/137-regenerate-ui-notifications`。连续重新生成通过原用户消息 ID 保持关联；桌宠调整点默认隐藏、聊天窗口铺满屏幕工作区、语音识别错误三秒后收起、录音提供删除取消入口，普通通知改为留在聊天记录中。Python/JavaScript 语法检查和 `git diff --check` 通过；PR #184；Claude 按审查意见修正：原消息已删除或自主消息时拒绝重新生成；新增 `tests/test_regeneration.py`；全量测试与 main 相同。Windows 体验待验收。验收步骤见 `docs/tickets/137-regenerate-ui-notifications.md`。下一个工单号 138。
- 工单 136「慢模型不再一分钟就算失败」：分支 `ticket/136-slow-model-timeout`，Claude 实现。中转站 Gemini 一步最长约 45 秒，超过 60 秒的一步被当成失败；模型请求等待上限改为 180 秒（`MODEL_TIMEOUT_SECONDS`）。验收见 `docs/tickets/136-slow-model-timeout.md`。下一个工单号 137。
- 工单 135「请求失败时说出原因」：分支 `ticket/135-provider-error-reason`，Claude 实现。134 接中转站后 Lan 只看到「请求失败」。现在红字带 HTTP 状态码/超时/连不上和模型名，接口原文（key 打码）写进本机 `data/<角色>/provider-errors.jsonl`。新增测试在 `tests/test_model_settings.py`。验收见 `docs/tickets/135-provider-error-reason.md`。下一个工单号 136。
- 工单 134「设置里加模型」：分支 `ticket/134-model-settings`，Claude 实现，Lan 2026-10-27 要求（中转站试 Sonnet 5.5，以后试更多模型）。「更多」页「模型」卡片添加接口（key 只进本机 `.env`）和模型（按次或按 token 计价、思考开关），「Peter 本人」和「记忆后台」分别选模型，顶栏下拉切换 Peter 的模型（下一次请求生效）；费用上限改为页面可设（Lan 选试用期 ¥30/天、¥600/月）。本机文件 `data/<角色>/models.json`。新增 `tests/test_model_settings.py`（9 项）；全量测试与 main 相同（本机 6 项环境相关失败两边一致），桌面测试 10 项通过。Windows 体验待验收（update.bat 会重新打包 exe），验收见 `docs/tickets/134-model-settings.md`。下一个工单号 135。
- 工单 133「自主活动和能力说明一次整理」：分支 `ticket/133-autonomy-cleanup`，PR #180，Claude 实现，Lan 2026-10-27 确认并要求合成一个 PR。六部分：醒来说明和聊天账本显示「今天已醒 n/12 次」、约的醒来落空时下次告诉他原因；夜间 01:00–06:00 安静时段（活动页可开关，默认开）；醒来/瞄一眼/Codex 提醒时需要批准的操作返回「不会问 Lan」而不是「Lan 没同意」；每轮固定事实改为真实渠道和记忆页；Peter 可改写自己的实验室文件和自己在桌面新建的文件（程序留旧版本）、挪进 `lab/trash/`，不能删除；醒来请求输入上限 65536（聊天仍 32768），自主预算 ¥3/日。新增 `tests/test_autonomy_cleanup.py`（12 项）；全量测试与 main 相同（本机 6 项环境相关失败两边一致），桌面测试 10 项通过。Windows 体验待验收（update.bat 会重新打包 exe），验收见 `docs/tickets/133-autonomy-and-capability-cleanup.md`。下一个工单号 134。
- 工单 132「看得懂的记忆页」：分支 `ticket/132-readable-memory-page`，Claude 实现，Lan 2026-10-27 要求。记忆页一次读出全部记忆（`/api/memory/panel`），最近的在前、按天分组，四个分组按钮（只合并显示，不改 Ombre 分类），可编辑标题/为什么记/内容（走 trace，旧版本留档）和删除（点两次，同 /delete）；标签含「惊喜」的记忆不发正文。新增 `tests/test_memory_panel.py` 和 web 接口测试；全量测试与 main 相同（本机 13 项环境相关失败两边一致），桌面测试 10 项通过。验收见 `docs/tickets/132-readable-memory-page.md`。下一个工单号 133。
- 工单 131「消息操作和回忆界面」：分支 `ticket/131-message-ui`，PR #178。右键浮起选中消息并虚化背景，使用 Lucide 官方图标；引用有可取消预览和消息内引用块；回复关联记忆以横向卡片显示时间、最多三个标签及正文，最多展示五条。输入区以 T 听写、官方图片图标和内嵌麦克风操作，点击开始、再次点击发送语音。修复发送期间修改草稿会丢失引用的问题。桌面自动测试 10 项和虚构数据 Windows Electron smoke 通过；真实 Windows/Lan 体验待验收。验收见 `docs/tickets/131-message-ui.md`。

- 工单 130「Codex 提醒不再召回长期记忆」：分支 `ticket/130-codex-no-recall`，Claude 实现，Lan 2026-10-27 同意。`codex_watch_mode` 时跳过 `memory.context`。回归测试在 `tests/test_background.py`；全量测试与 main 相同（本机 13 项环境相关失败两边一致）。下一个工单号 131。
- 工单 129「能力记录按本地时间显示」：分支 `ticket/129-capability-local-time`，Claude 实现。`capabilities.json` 仍存 UTC，交给 Peter 时转成本机时间（`capability_state.local_stamp`）。起因：10-26 他把 UTC 时间当本地时间读，以为新上线的瞄一眼是旧能力。新增 `tests/test_capability_local_time.py`；全量测试与 main 相同（本机 13 项环境相关失败两边一致）。下一个工单号 130。
- 工单 128「新对话的开场记忆」：PR #175 已合并（dd4e2c3），该 PR 当前提交的 Tests 与 Dependency security 通过；Lan 的 Windows 体验待验收。验收见 `docs/tickets/128-opening-memory.md`。
- 工单 127「QQ 消息来源与背景匹配」：PR #174 已合并（c042ed7），该 PR 当前提交的 Tests 与 Dependency security 通过；Lan 的 Windows 体验待验收。验收见 `docs/tickets/127-qq-context.md`。

- 工单 126「发完消息不再重复说一遍」：分支 `ticket/126-no-repeat-after-send`，Claude 实现。122 之后 Peter 用 send_message 发完几条，常再写一段换个说法的总结，按 122 的规则会多显示/多发一条（Lan 在 QQ 上看到每段话后面都重复一遍）。现在：发完消息后紧接着写的文字不显示、不发送、不存（以最后一条已发消息为回复）；如果发消息之后他又用了别的工具（如先说「先看一下」再读文件），之后写的文字照常作为新回复。send_message 说明同步改为「紧接着写的文字不会显示」。验收：update.bat 后在 QQ 和桌面各聊 5 分钟，不再出现一组消息后跟一条重述。下一个工单号 127。
- 工单 125「实时上下文窗口」：分支 `ticket/125-live-context`，Claude 实现。新增「上下文」页（可像其他页一样拖出成独立小窗，透明度单独可调）：上半块「聊天时活动」显示最近 10 次请求各带了多少原文轮数、背景段、长期记忆、短期便签、估算 tokens 和这次用了哪些工具（来自 `context-log.jsonl` 与 `tools.jsonl`，按 request_id 对上）；下半块「短期便签」显示进行中的便签（含约何时淡出）和 3 小时内已完成/已过期的，以及下次整理时间。数据随 `/api/events` 下发，只读，不触发提取、不改便签。需要运行 update.bat 重新打包 exe。验收：聊一句后上半块出现这次请求；有便签后下半块出现；拖出「上下文」标签成为小窗，合并回去正常。下一个工单号 126。
- 工单 124「瞄一眼和 Codex 监视能单独暂停」：分支 `ticket/124-toggle-allowlist`，Claude 实现。桌面程序 `desktop/main.cjs` 的请求白名单漏了 `/api/glance` 和 `/api/codex-watch`，两个开关点了都显示「设置保存失败」（Lan 电脑上从未生成这两个设置文件）。现在主窗口和弹出的活动小窗都允许这两个请求。需要运行 update.bat 重新打包 exe。验收：单独点「暂停瞄一眼」「暂停监视」都显示已暂停、自主活动仍开着，重启后保持暂停。下一个工单号 125。
- 工单 123「Peter 聊天短期便签（程序提取）」：分支 `ticket/123-short-term-notes`，实现完成。Peter 每 30 分钟在有新增对话时用非 thinking 调用提取具体事实、决定和未完成事项；点击“新对话”清空历史前也先提取。便签只追加到 `data/peter/notes.jsonl`，状态为进行中/已完成/已过期；普通便签无进展 60 分钟后过期，带未来时间的便签到该时间后再计时。活跃便签与背景并列放入上下文，最多 10 条 / 800 字符，仅作上下文，不写 Ombre 或 Plan；提取费用单列为 `short_term_notes`。本地完整测试 560 项通过、5 项跳过；PR #170 已合并（ce414a2）；Lan Windows 验收待做。下一个工单号 124。
- 工单 121「Peter 背景库按关键词注入」：分支 `ticket/121-background-injection`，Claude 实现。新文件 `background.py` 移植小手机的背景分段注入（关键词 + 正文相关性、`[常驻]`、每轮最多 3 段 / 1500 字符），每次请求重读本机 `persona/peter.background.md`，放在聊天历史之后、召回记忆之前；没有这个文件时一切照旧。每次请求追加一行 `data/peter/context-log.jsonl`（只记编号、关键词、大小，最多 500 行）。全量测试与 main 相同（本机 13 项环境相关失败两边一致），新增 `tests/test_background.py` 14 项通过。验收见 `docs/tickets/121-background-injection.md`；实际每天多花多少钱待用一天后按 `costs.jsonl` 计算。
- 工单 120「瞄一眼认不出前台窗口时不再失败」：分支 `ticket/120-glance-no-window`。lan 停在桌面、只开着 VEIL 或前台是受保护窗口时，`windows_foreground` 报错，瞄一眼直接记为失败。现在 `GlanceController._observe` 照常给 Peter 一次瞄一眼：没有应用窗口在前台时，告诉他 Lan 多半停在桌面，并给他桌面上文件和文件夹的名字（最新的在前，最多 40 个，跳过隐藏项，不读文件内容；`desktop_files.desktop_item_names`）；窗口认得出但内容读不出时，只给应用名和标题。加回归测试 `tests/test_glance.py`。验收：在桌面或只开 VEIL 时等一次瞄一眼，记录不再是「未识别窗口」失败。
- 工单 119「盯 Codex 的能力通知发给 Peter」：分支 `ticket/119-codex-watch-notice`。开着长期记忆时工具被 `ToolSet` 包着，`codex_watch.py` 直接在外层找 `announce_codex_watch`，找不到，所以 Peter 的能力记录里没有盯 Codex 这一条。改为先取 `ToolSet.files`，加回归测试 `tests/test_codex_watch.py`。验收：更新后问 Peter 有什么新能力，他的能力记录里能查到 Codex 监视。
- 工单 116「Lan 在电脑前时，Peter 隔一会儿来瞄她一眼」：PR #164 已合并到 main，合并提交 `0f39c1a7b3fec62545839075a4825ab3e5b9c95d`。GitHub 查询未返回该提交的 workflow/status 行，因此不记录为 CI 通过；Lan Windows 体验仍待验收。实现复用 Windows 输入空闲时钟、UIA 前台观察、现有 Chat 工具循环和预算账本；仅在最近 5 分钟有输入且屏幕解锁时随机等待 15–40 分钟，每次最多 4 个工具动作，每日独立预算 ¥1。活动页提供独立开关，瞄一眼结果作为短期记录交给下一次聊天，不自动写长期记忆。观察只用 UIA 文字和标题，不用截图。
- 工单 115「Peter 可弹窗、发全屏弹幕」：PR #163 已合并到 main，合并提交 `4a5ed56`；Tests、Dependency security、Windows desktop 检查通过。Lan 已确认本地视觉预览；Windows 实机体验待按工单验收。
- 工单 118「放宽自主醒来次数和步数」：PR #162 已合并为 `ffdd368`。配置最快 90 分钟、每天最多 12 次、每次最多 20 个真实工具步骤和 15 分钟；¥2/天预算与 `set_next_wake` 的 60 分钟间隔不变。针对性检查通过、3 项跳过；本机 Node 文件服务用例因服务未启动而单独失败。Lan 的 Windows 全天体验待验收。
- 工单 117「Peter 帮 Lan 盯着 Codex」：PR #165 已合并至 main，合并提交 `d42b12e`；Tests、Dependency security、Windows desktop 检查通过，Lan Windows 实机体验仍待验收。审批/提问等待没有可靠信号，不推断“正在等 Lan”。

## 工单 116 的 Windows 体验验收

1. 更新并重启 Windows 版 VEIL，打开「活动」页启用「主动瞄一眼」，正常使用电脑 1–3 小时；确认 Peter 至少自然回应一次，活动记录显示他看到的应用与决定。
2. 离开电脑超过半小时再回来，确认离开期间没有瞄一眼记录、回来后重新计时；暂停全局自主活动或关闭瞄一眼开关后，确认不会继续触发。
3. 聊天询问 Peter 刚才看到什么，确认短期回想；查看 `/cost` 有单独「瞄一眼」费用且未超过 ¥1/日，并确认长期记忆未自动写入窗口琐事。
## 此前工单验收状态

| 工单 | 合并与 CI | 已有体验或验证 | Lan 体验验收 |
| --- | --- | --- | --- |
| 115 弹窗弹幕 | PR #163 已合并；Tests、Dependency security、Windows desktop 通过 | Lan 已确认本地视觉预览 | 待 Windows 实机验收 |
| 118 自主醒来步数 | PR #162 已合并；针对性检查通过，3 项跳过 | Node 文件服务用例未启动服务 | 待 Windows 全天体验 |
| 095 消息操作 | PR #139 已合并；CI 通过 | 消息操作功能可用 | **通过**。UI 仍难看；视觉设计改版作为独立后续事项 |
| 096 关于 Lan 的个人笔记 | PR #140 已合并；CI 通过 | Lan 已实际使用 | **通过**，目前够用 |
| 097 自主唤醒调整 | PR #143 已合并；CI 通过 | 自主行为需长期观察 | 未通过/未验收；继续观察，不把合并当作体验通过 |
| 098 QQ 邮箱 | PR #142 已合并；CI 通过 | 新 QQ 邮箱到 2026-11-07 才能开 IMAP/SMTP | 待届时实际收发，当前不能验收 |
| 099 上网搜索与网页读取 | PR #148 已合并；CI 通过 | Lan 看过 Peter 首次自主搜索蜘蛛丝强度，搜索有真实成功案例 | 部分验证；网页读取和其他场景仍待验收 |
| 100 自定唤醒时间 | PR #152 已合并为 `c0de006`；最新 PR 提交 `f7951d5` 的 Tests #1256、Dependency security #201 通过 | 本地专项检查通过 | 待 Lan Windows 体验 |
| 102 QQ 通信 | PR #144 已合并；CI 通过 | Lan 确认核心通信能用 | **核心通信功能通过** |
| 103–105 QQ 修复 | PR #145、#146、#147 已合并；CI 通过 | Lan 确认 QQ 修复现在能用 | **通过** |
| 106 桌面独立窗口 | PR #149 已合并；Tests #1237、Dependency security #193、Windows desktop #100 通过 | Lan 确认预览效果 | 待 Windows 实际拖出、合并和重启恢复，不提前标记完成 |
| 107 QQ 表情发送修复 | PR #150 已合并；CI 通过 | Lan 电脑诊断确认根因并已修复 | 带表情整句实际发送仍待确认 |
| 108 一键更新 | PR #151 已合并；CI 通过 | `update.bat` 已实现 | Windows 一键更新流程待体验 |
| 109 清除已恢复的普通工具故障提示 | PR #153 已合并为 `006a4b7`；最新 PR 提交的 Tests #1259、Dependency security #202 通过 | 普通工具故障可在后续成功后恢复显示；结果不确定的写入仍需人工核对 | Lan 的实际恢复流程待确认 |

## 下一步

工单 137 合并后按 `docs/tickets/137-regenerate-ui-notifications.md` 更新 Windows 桌面程序并验收。工单 116、117 的 Windows 实机验收仍待完成。

## 尚待 Lan 验收或观察

- 116：按 `docs/tickets/116-glance-at-lan.md` 验收主动瞄一眼、离开/暂停时停止并重新计时、短期回想、独立预算和不自动写入长期记忆。
- 117：按 `docs/tickets/117-watch-codex.md` 验收 Codex 完成弹窗、离开时 QQ、中止、长时间没有新记录的谨慎措辞、开关和不误报完成。当前日志格式无法可靠识别审批或提问等待；不期待等待提醒。
- 115：按 `docs/tickets/115-popup-danmaku.md` 验收 Windows 实机上的弹窗、连弹、弹幕点击穿透、全屏视频自动转换和活动账本状态；本地视觉预览已通过。
- 118：更新后观察一天，确认周期至少 90 分钟、每天不超过 12 次、长任务可走到第 20 步、暂停会在当前步骤后生效且自主费用不超过 ¥2。
- 114：Lan 2026-10-25 已验收（在床上通过 QQ 让 Peter 在桌面新建了 5 个文件）。
- 113：批准后动作照常执行，批准后持续晃鼠标则不执行。

- 110：按 `docs/tickets/110-long-task-unstick.md` 的步骤验收：卡住的生日寻宝任务能被 Peter 自己放行，之后写文件不再循环被拒绝。
- 111：按本文件的工单 111 Windows 体验步骤验收桌面观察、后台截图及桌面不受干扰。
- 112：按 `docs/tickets/112-desktop-interaction.md` 验收白名单启动、文字输入、菜单和窗口切换、人工接管、权限限制及活动账本。

- 097：继续观察自主行为，不标记体验通过。
- 098：2026-11-07 后配置新 QQ 邮箱并验收 IMAP/SMTP 收发。
- 099：验收网页读取及蜘蛛丝搜索之外的场景。
- 100：按下方步骤在 Windows 验收定时研究、QQ 提醒、服务重启和错过时间后的单次补醒。
- 101：PR #154 已合并；在 Windows 验收 Lab 附件、历史恢复和邮件附件。
- 106：验收真实拖出、合并和重启恢复。
- 107：验收带 😭💀 等表情的 QQ 整句发送。
- 108：按 PR #151 的步骤在 Windows 双击 `update.bat` 验收更新与重新打包。
- 109：验收普通工具故障在后续成功后显示已恢复，同时确认结果不确定的写入仍需人工核对。

## 工单 100 的 Windows 体验验收

1. 更新并重启 Peter，告诉他“明天下午 3 点继续研究蜘蛛丝强度”，确认安排成功，活动记录里能看到时间和理由。
2. 告诉他“明早 8 点叫我”，确保 QQ 联系方式可用；到期前重启 VEIL 服务，确认计划仍在，到点只联系一次。
3. 让另一项计划在电脑睡眠或关机期间经过；恢复电脑并启动 VEIL，确认最多补醒一次，没有重复消息或自主活动记录。

## 工单 111 的 Windows 桌面观察体验验收

1. 同时打开两个以上应用，问 Peter“你看看我现在开着什么软件”，核对名称、标题、状态和同名窗口区分。
2. 在记事本输入测试文字，让 Peter 读取并核对内容。
3. 选一个 UIA 内容不足的界面（可以在后台，不用切到前台），确认 Peter 使用目标窗口截图辅助理解；若截图是黑屏，Peter 应明确说截图失败而不是描述画面；浏览器窗口不提供截图回退。
4. 打开 Codex，让 Peter 描述屏幕当前可见状态；不以一次截图推断是否长期卡死。
5. 一边操作其他软件一边请求观察，确认没有焦点、布局、剪贴板或文件变化。
6. 在活动窗口核对工具开始与成功/失败状态；确认账本和聊天历史未保存控件文字或截图。
7. 关闭目标窗口或观察不可访问窗口，核对 Peter 明确说明失败、不编造内容。
8. 普通观察请求不得让 Peter 点击、输入、删除或修改文件。

## 工单 101 的 Windows 体验验收

1. 合并后退出 Peter，双击 `update.bat` 更新并启动；让 Peter 在 Lab 做一个小网页或图片并发给 Lan，确认聊天卡片出现，图片可看、HTML 可在默认浏览器打开、文本可在聊天窗口展开查看。
2. 重启 VEIL 后从历史消息再次打开附件，确认卡片与文件都还在；让 Peter 回复一封邮件并附上 Lab 文件，确认邮件收到附件。

095 的 UI 视觉设计改版是单独后续事项，不属于功能待验收，也不在工单 101 范围。
