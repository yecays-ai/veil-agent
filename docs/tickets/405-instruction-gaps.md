# 工单 405：剩下没登记的提示词全部上表

属于：A 陪伴核心（身份与关系连续）
做完 Lan 能多做的一件事：指令页里能看见并直接改「他醒来怎么理解你多久没说话」这类话，改完立刻生效，不用等改代码。
她怎么验收：指令页 →「运行时」找到「她沉默时他怎么理解」，改一句保存；下一次自主醒来或隔了一小时再聊天，看他的说法变了没有。

## 目标

请求里凡是程序拼进去的提示词，都在指令页列出来：能改的能改，系统定死的只读。

## 当前状态

工单 400 登记了 55 条，工单 402 又补了 4 条醒来框架句。但防写死扫描（`prompts/inline.py` 的 `PROMPT_MODULES`）只扫固定的 12 个文件，**`life_state.py` 不在里面**，所以这个文件里拼进提示词的话从来没被扫描到，也没进登记表。

结果就是 `life_state.gap_text` 那段——「距她上次说话 13 小时……不必每轮都提，她沉默可能是忙、在睡、或者不高兴」——只存在于代码里。指令页搜不到，`persona/instructions/` 里也没有对应的覆盖文件可写，所以改不了。

真实请求快照可以作证：`data/peter/request-snapshots/<request_id>.01.json` 的 `user` 块第一段就是这段原文，而指令页里没有这一条。

同样漏掉的还有 18 段，全部在本次登记。

## 本次改动

19 段程序拼进提示词的话登记成条目；代码改成 `prompts.text()` / `prompts.fill()`，默认输出逐字节不变。

| 新条目 | 可改 | 原来写在哪 | 界面分组 |
| --- | --- | --- | --- |
| `life.time_gap` | 可改 | `life_state.gap_text` 的数据句 | 运行时 |
| `life.time_gap.proactive` | 可改 | 同上，他主动找过你几次那句 | 运行时 |
| `life.time_gap.last` | 可改 | 同上，你最后几条可见消息 | 运行时 |
| `life.time_guidance` | 可改 | 同上，「不必提 / 可能在忙、睡、不高兴」 | 运行时 |
| `runtime.investigation_checkpoint.rules` | 只读 | `investigation_checkpoint.resume_context` | 运行时 |
| `life.state.frame` | 可改 | `life_state.state_text` 开头 | 记忆 |
| `life.state.rules` | 可改 | 同上，只用 in_progress 的规则 | 记忆 |
| `life.state.more_events` | 可改 | 同上，日程超过上限那句 | 记忆 |
| `autonomy.lab.frame` | 可改 | `autonomy._lab_context` 开头 | 自主活动 |
| `autonomy.lab.more` | 可改 | 同上，还有 N 个更早文件 | 自主活动 |
| `autonomy.lab.newest` | 可改 | 同上，最新文件的开头与结尾 | 自主活动 |
| `autonomy.earlier.frame` | 可改 | `autonomy._context` 更早自主活动开头 | 自主活动 |
| `autonomy.budget.line` | 可改 | 同上，今日预算那一行 | 自主活动 |
| `autonomy.wake_count` | 可改 | `autonomy.wake_count_text` 今天醒了几次 | 自主活动 |
| `autonomy.wake_quiet` | 可改 | 同上，安静时段 | 自主活动 |
| `autonomy.wake_rhythm` | 可改 | 同上，自然醒来节奏 | 自主活动 |
| `autonomy.wake_scheduled` | 可改 | 同上，他自己定的闹钟 | 自主活动 |
| `safety.untrusted_lab_page` | 只读 | `lab_preview.IMAGE_PROMPT` | 系统与安全 |
| `safety.untrusted_codex` | 只读 | `codex_watch` 的提醒提示词 | 系统与安全 |

另外两处：

- `prompts/inline.py` 的扫描名单加上 `life_state.py`，以后再有人往这个文件里写死提示词，测试会拦下。不加白名单、不抬 `BASELINE`。
- `life_state.gap_text` 也被普通聊天每轮末尾用到，这条改动对聊天同样生效，不是只影响自主活动。

**不做的**：工具返回、报错、命令说明这些不是提示词，是程序反馈，不进登记表。

## 验收步骤

1. 指令页 →「运行时」，找到「她沉默时他怎么理解」，按你想要的语气改一句，保存。
2. 隔一小时以上再和他聊天，或等他下一次自主醒来，看他的话有没有按新写法走。
3. 「自主活动」里确认 Lab 清单、预算、醒来次数三处都在且能点开编辑；「系统与安全」里 Lab 截图和 Codex 摘录两条是灰的（只读）。
