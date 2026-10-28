# 当前状态

更新：2026-10-28 UTC · Claude（工单 149：Peter 实验室自主预览，PR #196 开放）。

## 当前目标与现状

- 2026-10-28 Lan 确认：截至工单 141，已经实际使用过的功能都通过 Windows 实际体验验收（清单见下方验收总表）。098 QQ 邮箱除外，等 2026-11-07。
- 当前目标：让 Peter 在 Lab 中预览自己制作的 HTML，基于真实截图和错误信息自主改进。
- 现状：实现与回归测试在 `ticket/149-peter-lab-preview`；PR [#196](https://github.com/yecays-ai/veil-agent/pull/196) 已开放，已改用兼容 Python 3.11 的 `pyee==13.0.1`，并修复完整 CI 发现的未初始化预览器测试替身与空行解析问题；最新提交检查待重跑。CodeRabbit 审查已请求，Cubic 试用额度用尽。真实 Chromium 集成检查在 Windows CI 执行。Lan 的 Windows 体验验收待合并和更新后进行。
- 下一步：确认 PR #196 最新修复提交的必需 CI 与 CodeRabbit 意见；Cubic 因试用额度无法审查。修复实质问题后保持 PR 打开，等待 Lan 按本页步骤体验。下一个工单号 150。
- 后续开发路线以下方验收总表为依据。合并、CI 通过和体验验收分别记录。

## 验收总表（按功能）

### 聊天与消息

| 工单 | 功能 | Lan 体验验收 |
| --- | --- | --- |
| 095 | 消息操作（右键菜单、图标） | **通过**（更早确认） |
| 131 | 消息操作和回忆界面 | **通过** |
| 137 | 桌面 UI 提示与连续重新生成 | **通过** |
| 141 | 自由对话：连发、插话、不回、晚点回、请求回复 | **通过** |
| 122 | send_message 不再结束本轮 | 没有验收记录（未在 10-28 清单中） |
| 126 | 发完消息不再重复说一遍 | 没有验收记录（未在 10-28 清单中） |
| 138 | 说完就停 + 停下按钮 | 未在 10-28 清单中；Lan 10-27 对 134–138 说过「验收完毕」，待她确认后再改 |
| 142 | 气泡额度按 Lan 的消息重计、不回、请求回复图标 | 待验收 |
| 143 | 聊天里不再显示已读不回/长度估算 | 待验收 |
| 144 | 消息队列原子性与回复状态 | 待验收 |
| 146 | 核心可靠性：连发保序、便签预算、空回复不崩 | 待验收 |

### 记忆与上下文

| 工单 | 功能 | Lan 体验验收 |
| --- | --- | --- |
| 096 | 关于 Lan 的个人笔记 | **通过**（更早确认） |
| 123 | 短期便签 | **通过** |
| 127 | QQ 消息来源与背景匹配 | **通过** |
| 128 | 新对话的开场记忆 | **通过** |
| 121 | 背景库按关键词注入 | 没有验收记录（未在 10-28 清单中） |
| 125 | 实时上下文窗口 | 没有验收记录（未在 10-28 清单中） |
| 129 | 能力记录按本地时间显示 | 没有验收记录（未在 10-28 清单中） |
| 130 | Codex 提醒不召回长期记忆 | 没有验收记录（未在 10-28 清单中） |
| 132 | 看得懂的记忆页 | 没有验收记录（未在 10-28 清单中） |
| 145 | 工具说明瘦身 | 待验收 |
| 147 | dream 不再引导反省、无参数工具不再被拒 | 待验收 |

### 自主活动与唤醒

| 工单 | 功能 | Lan 体验验收 |
| --- | --- | --- |
| 097 | 自主唤醒（醒来是他自己的时间） | **通过** |
| 100 | 自定唤醒时间 | **通过** |
| 110 | 长期任务卡住后恢复 | **通过** |
| 118 | 自主醒来次数与步数 | **通过** |
| 133 | 自主活动和能力说明整理（夜间安静、¥3/天） | **通过** |
| 139 | Peter 自己决定怎么醒（节律、多闹钟） | **通过** |
| 113 | 批准不算人工接管 | 没有验收记录（未在 10-28 清单中） |
| 140 | 长期任务进度满了滚动归档 | 没有验收记录（未在 10-28 清单中） |

### 桌面与屏幕

| 工单 | 功能 | Lan 体验验收 |
| --- | --- | --- |
| 106 | 独立窗口与工具活动 | **通过** |
| 111 | Windows 桌面观察 | **通过** |
| 112 | Windows 桌面操作 | **通过** |
| 114 | 桌面自由创作 | **通过**（更早确认） |
| 115 | 弹窗与弹幕 | **通过** |
| 116 | 主动瞄一眼 | **通过** |
| 117 | 帮 Lan 盯 Codex | **通过** |
| 119 | 盯 Codex 的能力通知发给 Peter | 没有验收记录（未在 10-28 清单中） |
| 120 | 瞄一眼认不出前台窗口时不再失败 | 没有验收记录（未在 10-28 清单中） |
| 124 | 瞄一眼和 Codex 监视能单独暂停 | 没有验收记录（未在 10-28 清单中） |

### Lab 创作与附件

| 工单 | 功能 | Lan 体验验收 |
| --- | --- | --- |
| 149 | Peter 自主预览 Lab HTML、查看截图和错误并迭代 | 待验收（PR/Windows CI 待检查） |

### QQ、邮件、网页与附件

| 工单 | 功能 | Lan 体验验收 |
| --- | --- | --- |
| 098 | QQ 邮箱收发 | 待 2026-11-07 后启用 IMAP/SMTP 再实际收发 |
| 099 | 网页搜索与读取 | **通过** |
| 101 | 发送 Lab 文件附件 | **通过** |
| 102 | QQ 通信 | **通过**（更早确认） |
| 103–105 | QQ 修复 | **通过**（更早确认） |
| 107 | QQ 表情发送 | **通过** |

### 模型、费用、更新与稳定性

| 工单 | 功能 | Lan 体验验收 |
| --- | --- | --- |
| 108 | 一键更新 update.bat | **通过** |
| 109 | 工具故障恢复提示 | **通过** |
| 134 | 设置里加模型 | **通过** |
| 135 | 请求失败时说出原因 | 未在 10-28 清单中；项目记录里 Lan 10-27 对 134–138 说过「验收完毕」，待她确认后再改 |
| 136 | 慢模型超时放宽到 180 秒 | 未在 10-28 清单中；Lan 10-27 对 134–138 说过「验收完毕」，待她确认后再改 |

## 待验收与待确认

- 098 QQ 邮箱：2026-11-07 后配置新 QQ 邮箱并验收收发。
- 142–149：已合并或待合并，按各自 `docs/tickets/` 验收步骤做 Windows 体验。
- 135、136、138：Lan 10-27 说过 134–138 验收完毕，但不在 10-28 清单里，待她确认。
- 113、119–122、124–126、129、130、132、140：没有单独的验收记录；多数是修复或后台改动，日常使用中出问题再记。

095 的 UI 视觉设计改版是单独后续事项，不属于功能待验收。

## 工单记录（按编号，开发事实与 PR）

- 工单 149「Peter 实验室自主预览」：分支 `ticket/149-peter-lab-preview`，PR #196 开放；CI 的 Python 3.11/pyee 14、未初始化预览器测试替身和空行解析问题均已修复；最新提交检查待重跑。CodeRabbit 已请求，Cubic 试用额度已用尽。用 Playwright Python/隔离 Chromium 渲染 Peter 自有 Lab HTML，将截图作为当轮临时图像输入送给当前模型，并返回 JS/资源错误；限制 Lab 路径、网络、浏览器状态、大小、两次迭代和 30 秒超时。添加功能/安全/模型图像恢复测试及 Windows Chromium CI。当前本机测试因受限环境不能绑定回环端口；Windows junction 用例不能创建。Lan 尚未体验验收。依赖安装与 3 步验收见 `docs/tickets/149-peter-lab-preview.md`。下一个工单号 150。
- 工单 147「dream 不再引导反省、无参数工具不再被拒」：分支 `ticket/147-dream-and-empty-args`，Claude 实现，Lan 2026-10-28 提出。dream 返回开头的 Ombre 中文反省引导换成一句中性英文（记忆内容不变，不改 Ombre）；工具参数为空或 null 时当作 `{}`。需要 update.bat。验收见 `docs/tickets/147-dream-note-and-empty-args.md`。下一个工单号 148。
- 工单 146「核心可靠性修复」：分支 `ticket/146-core-reliability`，Claude 实现。修复架构与代码审计中的 3 个核心可靠性问题：1. 解耦 `persist_incoming` 准入写入与 `chat.jsonl` 正式历史，引入 `incoming-submissions.jsonl`，消息在实际轮次开始或插话时提交至 `chat.jsonl`，修复连发/插话时的历史错序、prompt 用户消息重复注入及问答归类混乱，服务重启时自动补录排队中未开始的消息，失败媒体上传允许同 request_id 重试；2. 修复短期便签 `_call_extractor` 异常时费用预留泄漏并在 `Budget.__init__` 启动时自动对账清理，防止 pending 累积阻断聊天；3. 引入专用 `EmptyReplyError(UserError)` 异常子类，`run_chat` 文本分支精准捕获模型空回复，防止后台服务异常崩溃退出的同时保留存储与账本致命异常的向外暴露保护。根据 CodeRabbit 评审意见完善了异常分级、通用预算对账与媒体失败重试机制。新增/更新回归测试全部通过。验收见 `docs/tickets/146-core-reliability.md`。下一个工单号 147。
- 工单 145「工具说明瘦身」：分支 `ticket/145-slim-tool-descriptions`，Claude 实现，Lan 2026-10-28 提出（工具定义占上下文一半以上）。能力不删，只把 Ombre 九个工具、文件工具、long_task_update、Windows 工具的说明改成简短英文；trace 去掉 VEIL 本来就拒绝的硬删除/删除审批参数。估计每次请求 46KB → 约 34KB（未验证）。需要 update.bat。验收见 `docs/tickets/145-slim-tool-descriptions.md`。下一个工单号 146。
- 工单 144「消息队列原子性与回复状态可靠性修复」：分支 `ticket/144-chat-queue-reliability`。修复 `reply_later(cancel=True)` 未重置 `turn_done`/`reply_later_called` 导致轮次意外结束的问题（保留 `send_message(done=True)` 状态）；修复 `_chat_batch` 和 `claim_steer` 队列头部检查与取出的竞争窗口，防止 `ChatAction`（如 Request Reply）被吞为文本；修复语音转录阻塞期间新消息入队导致的乱序，改用队首逆序插入严格保序 A → B → C。新增回归测试 5 项（`tests/test_free_reply.py`）。验收见 `docs/tickets/144-chat-queue-reliability.md`。下一个工单号 145。
- 工单 143「聊天里不再出现没意义的提示」：分支 `ticket/143-quiet-silence`，Claude 实现，Lan 2026-10-28 截图反馈。成功的 `stay_silent`、`reply_later` 不再在聊天里显示「Peter 用了…」行（活动页照常记录）；「长度估算」提示不再发到聊天（「上下文」页已有分项）。需要 update.bat。验收：让 Peter 已读不回，聊天里什么都不出现；重启后聊几句不再出现「长度估算」行。下一个工单号 144。
- 工单 142「气泡上限按 Lan 的消息重新计数、请求回复按钮改成图标」：分支 `ticket/142-bubble-cap`，Claude 实现，Lan 2026-10-28 截图反馈。插话后 Peter 的 10 条额度重置；第 10 条发出即结束本轮；请求回复按钮改为 sparkles 图标方形按钮；新工具 `stay_silent`（Peter 明确选择不回，本轮结束、界面不显示任何东西，附一次性能力通知）。新增 3 项回归（`tests/test_free_reply.py`）。需要 update.bat 重新打包 exe。验收见 `docs/tickets/142-bubble-cap-and-reply-button.md`。下一个工单号 143。
- 工单 141「自由对话」：PR #188，分支 `ticket/141-free-reply`，基于 main `bf86eff`。实现随时连发、批量交付、生成中安全边界插话、输入状态事实、自由沉默、持久 `reply_later`、Request Reply、每轮 10 条硬上限与批次状态。CodeRabbit 五项数据/流程问题已修复；本地全量 629 项通过、7 项跳过，桌面 10 项通过。桌面需 `update.bat`；下一个工单号 142。Lan 2026-10-28 确认 Windows 实际体验验收通过。
- 工单 140「长期任务进度不再记满就卡死」：分支 `ticket/140-long-task-rolling`，Claude 实现，Lan 2026-10-28 提出。进度满 40 条后最旧的移入 `long-task-progress-archive.jsonl`，新进度照常写；待定操作在同一步先读后确认即可。新增测试 2 项（`tests/test_long_tasks.py`）。验收见 `docs/tickets/140-long-task-rolling.md`。下一个工单号 141。
- 工单 139「Peter 自己决定怎么醒来」：PR #186 已合并（6f6ba0c），CI 通过；Lan 电脑上已生效（10-28 自然醒 11:52/13:24/14:21，他自己排了闹钟）。分支 `ticket/139-wake-control`，Claude 实现，Lan 2026-10-28 同意。参考 Kli Space Wake Veil（1.0 仓库思路 + 2.0 设计稿），Python 重写。自然醒改为醒后安静、慢慢攒够才醒（平均约 90 分钟，多在 30–190 分钟）；新工具 `set_wake_rhythm`（quieter / silent / custom，带时长自动恢复）；`set_next_wake` 可排多个带留言的闹钟、最早 15 分钟，安静时也照响；错过的闹钟下次一起告诉他。Lan 的暂停、夜间安静、12 次/天、¥3/天不变。新增 `tests/test_wake_control.py`（7 项）；全量测试与 main 相同（本机 6 项环境相关失败两边一致）。未改桌面界面。验收见 `docs/tickets/139-wake-control.md`。下一个工单号 140。Lan 2026-10-28 确认 Windows 实际体验验收通过。
- 工单 138「Peter 自己说完就停，Lan 可以叫停」：分支 `ticket/138-turn-stop`，Claude 实现，Lan 2026-10-28 同意。argon 一句晚安发了 19 条，一直跑到 20 步上限。send_message 加了可选的 `done`：他标为最后一条后，这一轮就结束（自主活动里忽略）。聊天「正在回复…」旁加「停下」按钮（`/api/stop`），在下一步开始前停下，已经发出的消息保留。新增 `tests/test_turn_stop.py`（7 项）；全量测试与 main 相同（本机 6 项环境相关失败两边一致），桌面测试通过。需要 update.bat 重新打包 exe。验收见 `docs/tickets/138-turn-stop.md`。PR #184（Codex 的 137）还开着；本单避开了它改的行。下一个工单号 139。
- 工单 137「桌面 UI 提示与连续重新生成」：已合并（PR #184），分支 `ticket/137-regenerate-ui-notifications`。连续重新生成通过原用户消息 ID 保持关联；桌宠调整点默认隐藏、聊天窗口铺满屏幕工作区、语音识别错误三秒后收起、录音提供删除取消入口，普通通知改为留在聊天记录中。Python/JavaScript 语法检查和 `git diff --check` 通过；PR #184；Claude 按审查意见修正：原消息已删除或自主消息时拒绝重新生成；新增 `tests/test_regeneration.py`；全量测试与 main 相同。验收步骤见 `docs/tickets/137-regenerate-ui-notifications.md`。下一个工单号 138。Lan 2026-10-28 确认 Windows 实际体验验收通过。
- 工单 136「慢模型不再一分钟就算失败」：分支 `ticket/136-slow-model-timeout`，Claude 实现。中转站 Gemini 一步最长约 45 秒，超过 60 秒的一步被当成失败；模型请求等待上限改为 180 秒（`MODEL_TIMEOUT_SECONDS`）。验收见 `docs/tickets/136-slow-model-timeout.md`。下一个工单号 137。
- 工单 135「请求失败时说出原因」：分支 `ticket/135-provider-error-reason`，Claude 实现。134 接中转站后 Lan 只看到「请求失败」。现在红字带 HTTP 状态码/超时/连不上和模型名，接口原文（key 打码）写进本机 `data/<角色>/provider-errors.jsonl`。新增测试在 `tests/test_model_settings.py`。验收见 `docs/tickets/135-provider-error-reason.md`。下一个工单号 136。
- 工单 134「设置里加模型」：分支 `ticket/134-model-settings`，Claude 实现，Lan 2026-10-27 要求（中转站试 Sonnet 5.5，以后试更多模型）。「更多」页「模型」卡片添加接口（key 只进本机 `.env`）和模型（按次或按 token 计价、思考开关），「Peter 本人」和「记忆后台」分别选模型，顶栏下拉切换 Peter 的模型（下一次请求生效）；费用上限改为页面可设（Lan 选试用期 ¥30/天、¥600/月）。本机文件 `data/<角色>/models.json`。新增 `tests/test_model_settings.py`（9 项）；全量测试与 main 相同（本机 6 项环境相关失败两边一致），桌面测试 10 项通过。下一个工单号 135。Lan 2026-10-28 确认 Windows 实际体验验收通过。
- 工单 133「自主活动和能力说明一次整理」：分支 `ticket/133-autonomy-cleanup`，PR #180，Claude 实现，Lan 2026-10-27 确认并要求合成一个 PR。六部分：醒来说明和聊天账本显示「今天已醒 n/12 次」、约的醒来落空时下次告诉他原因；夜间 01:00–06:00 安静时段（活动页可开关，默认开）；醒来/瞄一眼/Codex 提醒时需要批准的操作返回「不会问 Lan」而不是「Lan 没同意」；每轮固定事实改为真实渠道和记忆页；Peter 可改写自己的实验室文件和自己在桌面新建的文件（程序留旧版本）、挪进 `lab/trash/`，不能删除；醒来请求输入上限 65536（聊天仍 32768），自主预算 ¥3/日。新增 `tests/test_autonomy_cleanup.py`（12 项）；全量测试与 main 相同（本机 6 项环境相关失败两边一致），桌面测试 10 项通过。下一个工单号 134。Lan 2026-10-28 确认 Windows 实际体验验收通过。
- 工单 132「看得懂的记忆页」：分支 `ticket/132-readable-memory-page`，Claude 实现，Lan 2026-10-27 要求。记忆页一次读出全部记忆（`/api/memory/panel`），最近的在前、按天分组，四个分组按钮（只合并显示，不改 Ombre 分类），可编辑标题/为什么记/内容（走 trace，旧版本留档）和删除（点两次，同 /delete）；标签含「惊喜」的记忆不发正文。新增 `tests/test_memory_panel.py` 和 web 接口测试；全量测试与 main 相同（本机 13 项环境相关失败两边一致），桌面测试 10 项通过。验收见 `docs/tickets/132-readable-memory-page.md`。下一个工单号 133。
- 工单 131「消息操作和回忆界面」：分支 `ticket/131-message-ui`，PR #178。右键浮起选中消息并虚化背景，使用 Lucide 官方图标；引用有可取消预览和消息内引用块；回复关联记忆以横向卡片显示时间、最多三个标签及正文，最多展示五条。输入区以 T 听写、官方图片图标和内嵌麦克风操作，点击开始、再次点击发送语音。修复发送期间修改草稿会丢失引用的问题。桌面自动测试 10 项和虚构数据 Windows Electron smoke 通过；验收见 `docs/tickets/131-message-ui.md`。Lan 2026-10-28 确认 Windows 实际体验验收通过。
- 工单 130「Codex 提醒不再召回长期记忆」：分支 `ticket/130-codex-no-recall`，Claude 实现，Lan 2026-10-27 同意。`codex_watch_mode` 时跳过 `memory.context`。回归测试在 `tests/test_background.py`；全量测试与 main 相同（本机 13 项环境相关失败两边一致）。下一个工单号 131。
- 工单 129「能力记录按本地时间显示」：分支 `ticket/129-capability-local-time`，Claude 实现。`capabilities.json` 仍存 UTC，交给 Peter 时转成本机时间（`capability_state.local_stamp`）。起因：10-26 他把 UTC 时间当本地时间读，以为新上线的瞄一眼是旧能力。新增 `tests/test_capability_local_time.py`；全量测试与 main 相同（本机 13 项环境相关失败两边一致）。下一个工单号 130。
- 工单 128「新对话的开场记忆」：PR #175 已合并（dd4e2c3），该 PR 当前提交的 Tests 与 Dependency security 通过；验收见 `docs/tickets/128-opening-memory.md`。Lan 2026-10-28 确认 Windows 实际体验验收通过。
- 工单 127「QQ 消息来源与背景匹配」：PR #174 已合并（c042ed7），该 PR 当前提交的 Tests 与 Dependency security 通过；验收见 `docs/tickets/127-qq-context.md`。Lan 2026-10-28 确认 Windows 实际体验验收通过。
- 工单 126「发完消息不再重复说一遍」：分支 `ticket/126-no-repeat-after-send`，Claude 实现。122 之后 Peter 用 send_message 发完几条，常再写一段换个说法的总结，按 122 的规则会多显示/多发一条（Lan 在 QQ 上看到每段话后面都重复一遍）。现在：发完消息后紧接着写的文字不显示、不发送、不存（以最后一条已发消息为回复）；如果发消息之后他又用了别的工具（如先说「先看一下」再读文件），之后写的文字照常作为新回复。send_message 说明同步改为「紧接着写的文字不会显示」。验收：update.bat 后在 QQ 和桌面各聊 5 分钟，不再出现一组消息后跟一条重述。下一个工单号 127。
- 工单 125「实时上下文窗口」：分支 `ticket/125-live-context`，Claude 实现。新增「上下文」页（可像其他页一样拖出成独立小窗，透明度单独可调）：上半块「聊天时活动」显示最近 10 次请求各带了多少原文轮数、背景段、长期记忆、短期便签、估算 tokens 和这次用了哪些工具（来自 `context-log.jsonl` 与 `tools.jsonl`，按 request_id 对上）；下半块「短期便签」显示进行中的便签（含约何时淡出）和 3 小时内已完成/已过期的，以及下次整理时间。数据随 `/api/events` 下发，只读，不触发提取、不改便签。需要运行 update.bat 重新打包 exe。验收：聊一句后上半块出现这次请求；有便签后下半块出现；拖出「上下文」标签成为小窗，合并回去正常。下一个工单号 126。
- 工单 124「瞄一眼和 Codex 监视能单独暂停」：分支 `ticket/124-toggle-allowlist`，Claude 实现。桌面程序 `desktop/main.cjs` 的请求白名单漏了 `/api/glance` 和 `/api/codex-watch`，两个开关点了都显示「设置保存失败」（Lan 电脑上从未生成这两个设置文件）。现在主窗口和弹出的活动小窗都允许这两个请求。需要运行 update.bat 重新打包 exe。验收：单独点「暂停瞄一眼」「暂停监视」都显示已暂停、自主活动仍开着，重启后保持暂停。下一个工单号 125。
- 工单 123「Peter 聊天短期便签（程序提取）」：分支 `ticket/123-short-term-notes`，实现完成。Peter 每 30 分钟在有新增对话时用非 thinking 调用提取具体事实、决定和未完成事项；点击“新对话”清空历史前也先提取。便签只追加到 `data/peter/notes.jsonl`，状态为进行中/已完成/已过期；普通便签无进展 60 分钟后过期，带未来时间的便签到该时间后再计时。活跃便签与背景并列放入上下文，最多 10 条 / 800 字符，仅作上下文，不写 Ombre 或 Plan；提取费用单列为 `short_term_notes`。本地完整测试 560 项通过、5 项跳过；PR #170 已合并（ce414a2）；下一个工单号 124。Lan 2026-10-28 确认 Windows 实际体验验收通过。
- 工单 121「Peter 背景库按关键词注入」：分支 `ticket/121-background-injection`，Claude 实现。新文件 `background.py` 移植小手机的背景分段注入（关键词 + 正文相关性、`[常驻]`、每轮最多 3 段 / 1500 字符），每次请求重读本机 `persona/peter.background.md`，放在聊天历史之后、召回记忆之前；没有这个文件时一切照旧。每次请求追加一行 `data/peter/context-log.jsonl`（只记编号、关键词、大小，最多 500 行）。全量测试与 main 相同（本机 13 项环境相关失败两边一致），新增 `tests/test_background.py` 14 项通过。验收见 `docs/tickets/121-background-injection.md`；实际每天多花多少钱待用一天后按 `costs.jsonl` 计算。
- 工单 120「瞄一眼认不出前台窗口时不再失败」：分支 `ticket/120-glance-no-window`。lan 停在桌面、只开着 VEIL 或前台是受保护窗口时，`windows_foreground` 报错，瞄一眼直接记为失败。现在 `GlanceController._observe` 照常给 Peter 一次瞄一眼：没有应用窗口在前台时，告诉他 Lan 多半停在桌面，并给他桌面上文件和文件夹的名字（最新的在前，最多 40 个，跳过隐藏项，不读文件内容；`desktop_files.desktop_item_names`）；窗口认得出但内容读不出时，只给应用名和标题。加回归测试 `tests/test_glance.py`。验收：在桌面或只开 VEIL 时等一次瞄一眼，记录不再是「未识别窗口」失败。
- 工单 119「盯 Codex 的能力通知发给 Peter」：分支 `ticket/119-codex-watch-notice`。开着长期记忆时工具被 `ToolSet` 包着，`codex_watch.py` 直接在外层找 `announce_codex_watch`，找不到，所以 Peter 的能力记录里没有盯 Codex 这一条。改为先取 `ToolSet.files`，加回归测试 `tests/test_codex_watch.py`。验收：更新后问 Peter 有什么新能力，他的能力记录里能查到 Codex 监视。
- 工单 118「放宽自主醒来次数和步数」：PR #162 已合并为 `ffdd368`。配置最快 90 分钟、每天最多 12 次、每次最多 20 个真实工具步骤和 15 分钟；¥2/天预算与 `set_next_wake` 的 60 分钟间隔不变。针对性检查通过、3 项跳过；本机 Node 文件服务用例因服务未启动而单独失败。Lan 2026-10-28 确认 Windows 实际体验验收通过。
- 工单 117「Peter 帮 Lan 盯着 Codex」：PR #165 已合并至 main，合并提交 `d42b12e`；审批/提问等待没有可靠信号，不推断“正在等 Lan”。Lan 2026-10-28 确认 Windows 实际体验验收通过。
- 工单 116「Lan 在电脑前时，Peter 隔一会儿来瞄她一眼」：PR #164 已合并到 main，合并提交 `0f39c1a7b3fec62545839075a4825ab3e5b9c95d`。GitHub 查询未返回该提交的 workflow/status 行，因此不记录为 CI 通过；实现复用 Windows 输入空闲时钟、UIA 前台观察、现有 Chat 工具循环和预算账本；仅在最近 5 分钟有输入且屏幕解锁时随机等待 15–40 分钟，每次最多 4 个工具动作，每日独立预算 ¥1。活动页提供独立开关，瞄一眼结果作为短期记录交给下一次聊天，不自动写长期记忆。观察只用 UIA 文字和标题，不用截图。Lan 2026-10-28 确认 Windows 实际体验验收通过。
- 工单 115「Peter 可弹窗、发全屏弹幕」：PR #163 已合并到 main，合并提交 `4a5ed56`；Tests、Dependency security、Windows desktop 检查通过。Lan 已确认本地视觉预览。Lan 2026-10-28 确认 Windows 实际体验验收通过。
