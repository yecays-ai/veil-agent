# 工单 146：核心可靠性修复

## 目标

修复代码审计中发现的 3 个核心可靠性问题：
1. 连发或插话时，聊天消息历史错序与模型 prompt 重复注入问题；
2. 短期便签后台提取失败时，费用预留永久处于 pending 累积锁死预算的问题；
3. 模型返回空文本回复时触发未捕获异常导致后台服务直接崩溃退出的问题。

## 当前状态

- **聊天连发、插话与历史保序（Bug A）**：
  - 根因：`submit()` 在接收到 HTTP 请求后立即调用 `persist_incoming()` 写入 `chat.jsonl`，导致正在生成的上一轮 assistant 回复被追加到新 user 消息之后，造成 `[User 1, User 2, Assistant 1]` 的错序；后续轮次因为尾部切片比较不匹配，导致历史记录将 User 2 重复打包进 prompt，界面刷新后问答归组也错乱。
  - 修复：将 `persist_incoming()` 的准入持久化与 `chat.jsonl` 正式对话历史解耦。接收消息时先写入 `data/<character>/incoming-submissions.jsonl` 保证崩溃安全性，不污染对话历史；仅在消息真正开始生成或被插话（`_send` / `accept_steer`）时才正式 commit 并写入 `chat.jsonl`。服务重启时通过 `recover_uncommitted_incoming()` 自动补录未提交消息，保证零丢消息、严格时序与模型 prompt 不重复。
- **短期便签费用预留结算（Bug B）**：
  - 根因：`short_term_notes.py` 中 `_call_extractor` 调用 `budget.reserve()` 后，若模型请求超时或抛出异常，`budget.settle()` 被跳过且异常被外层静默吞掉，导致预留永久留在 `pending`，累积后触发总预算上限锁死正常聊天。
  - 修复：在 `_call_extractor` 捕获异常处调用 `budget.settle_estimate(...)` 保守结算已预留费用，避免 `pending` 泄露；并在 `ShortTermNotes.__init__` 初始化时调用 `budget.reconcile_unsettled({COST_ACCOUNTING})` 自动对账清理历史遗留未结预留。
- **空模型回复服务崩溃防护（Bug C）**：
  - 根因：`veil_agent.py` 的 `run_chat` 文本分支仅捕获 `TooLong` 和 `RequestFailed`，未捕获模型空回复抛出的 `UserError`，导致异常溢出到 `serve_chat()` 的 `finally: hub.request_stop()`，彻底终止后台服务。
  - 修复：增加 `except UserError as exc:` 异常捕获，重置并同步内存轮次状态（`chat.history, chat.rounds = chat.recent()`），将错误原因输出到聊天界面并通知丢弃输入，保持聊天循环正常运转。
- 涉及文件：`veil_agent.py`、`short_term_notes.py`、`web_agent.py`、`tests/test_chat.py`、`tests/test_free_reply.py`、`tests/test_live_context.py`、`tests/test_web.py`。

## 验收

1. 退出 Peter，运行 `update.bat` 启动最新版本。
2. **连发与插话保序**：在 Peter 正在回复期间快速发送两条消息 A 和 B，观察 Peter 完成当前轮次后依次处理 A 与 B，刷新或重启界面后确认气泡顺序与问答关联完全正确，不会出现回复插入两条消息之间的情况。
3. **短期便签预算**：在后台短期便签提取发生网络抖动或失败时，在聊天中输入 `/cost`，确认未结算预留（pending）不会无限制累积，正常聊天不会因后台提取失败而被预算上限意外拦截。
4. **空回复健壮性**：若模型接口偶发返回空文本（如敏感词拦截或模型空输出），界面显示红字错误提示，后台服务保持运行，用户可直接发送下一句对话。
