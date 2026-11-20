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

23 段程序拼进提示词的话登记成条目；代码改成 `prompts.text()` / `prompts.fill()`，默认输出逐字节不变。

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
| `safety.untrusted_window_image` | 只读 | `windows_observer` 看窗口截图的说明 | 系统与安全 |
| `workbench.container` | 只读 | `executor_opencode.CONTAINER_NOTE` | 系统与安全 |
| `workbench.scope` | 只读 | `executor_opencode.TASK_SCOPE_NOTE` | 系统与安全 |
| `workbench.maintenance` | 只读 | `executor_opencode.MAINTENANCE_NOTE` | 系统与安全 |

另外两处：

- `prompts/inline.py` 的扫描名单加上 `life_state.py`，以后再有人往这个文件里写死提示词，测试会拦下。不加白名单、不抬 `BASELINE`。
- `life_state.gap_text` 也被普通聊天每轮末尾用到，这条改动对聊天同样生效，不是只影响自主活动。

**不做的**：工具返回、报错、命令说明这些不是提示词，是程序反馈，不进登记表。

## 评审后补的两处

1. **扫描范围改成自动发现。** 原来靠手写名单，`life_state.py` 就是这样漏了三张工单。现在 `prompts/inline.py` 的 `modules()` 会自动收进**所有 import 了 `prompts` 的根目录模块**；手写名单只当底。当前自动多收了 `codex_watch.py`、`instructions.py`、`investigation_checkpoint.py`、`lab_preview.py`、`request_snapshots.py`，收进来后新增未登记长句 0 条、白名单没加、`BASELINE` 没抬。回归测试两条：`test_every_module_that_imports_prompts_is_scanned`（漏一个模块就红）、`test_new_prose_in_a_model_module_is_caught`（临时写一个带长句的模块，必须被扫出来）。
2. **必需占位符不能删。** `registry.Entry` 有新字段 `placeholders`；`store.save()` 发现必需占位符被删就拒绝保存，并在页面显示原因（哪几个、为什么要留）。19 条里带数据的 11 条都登记了必需占位符，纯措辞的条目照旧随便改。回归测试四条，含「改完 `life.time_guidance` 下一次 `gap_text` 真的用新文本」。

要求没变：只补功能与防漏，不加新功能。

## 评审后补的第三处（自动扫描仍有漏洞）

上一版 `modules()` 只收「import 了 `prompts` 的模块」和一个手写名单。问题：新建模块直接在代码里拼请求、忘了 import `prompts`，就仍然扫不到。

补法：`prompts/inline.py` 加 `builds_requests()`，按**请求形状**识别——带 `role` 键的消息字典、`messages=` 参数、`messages` 赋值、`tool_schemas()` 调用。任何一种命中就算「会构造模型请求」。测试 `test_every_module_that_builds_requests_is_scanned` 要求所有这类模块都在扫描集合里；`test_request_building_module_without_prompts_import_is_flagged` 直接造一个「不 import prompts、只拼 role 消息」的模块，断言它会被认出来且不在扫描集合里（也就是会让上一条测试变红）。

这条检查一上线就抓到 3 个漏网的：`executor_opencode.py`、`ombre_entry.py`、`windows_observer.py`。其中两段是真提示词，一并登记：工作台的三段任务说明（`workbench.container` / `.scope` / `.maintenance`）和看窗口截图时的防注入说明（`safety.untrusted_window_image`），都是只读。剩下 2 条不是提示词（一段容器初始化 shell、一句环境报错），进反馈清单，`BASELINE` 43 → 45。

## 验收步骤

1. 指令页 →「运行时」，找到「她沉默时他怎么理解」，按你想要的语气改一句，保存。
2. 隔一小时以上再和他聊天，或等他下一次自主醒来，看他的话有没有按新写法走。
3. 「自主活动」里确认 Lab 清单、预算、醒来次数三处都在且能点开编辑；「系统与安全」里 Lab 截图和 Codex 摘录两条是灰的（只读）。
4. 把「距她上次说话的时间（数据句）」里的 `{duration}` 删掉再保存：应该保存不了，并告诉你这一条必须保留哪些占位符。
