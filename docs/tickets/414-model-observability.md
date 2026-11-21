# 工单 414：模型调用口径、上下文保护与工具哈希

- 格：A 陪伴核心（工具循环是聊天能力，不是新 B 功能）
- 做完 lan 能多做的一件事：普通聊天时，面板里的模型调用次数和 token 不再虚高；Gemini 中转低报 usage 时不会让上下文保护失效；不同模型的推理字段按各自协议传，不再靠猜。
- 验收：聊一句普通话，打开上下文页/请求快照，预检不再算一次调用，估算和实际分开展示；切到 Gemini 发一句，续请求 usage 异常时显示「用量不可信」且上下文不被错误放大或裁掉；现有工具、记忆、自主活动、多气泡不变。

## 目标

只改观测口径和模型协议适配，不改变 Peter 的回复行为：预检快照不计数、usage 合理性校验、修正每条消息 256 字节的估算 framing、真实调用记录延迟/输出/结束原因、工具参数与结果哈希、推理字段按模型显式配置。

## 当前状态

- 旧行为：预检快照被当成一次真实调用；message_request_bytes 每条消息固定加 256 字节；provider 报多少 prompt_tokens 就信多少；推理字段写死 reasoning_content；工具日志没有参数/结果哈希。
- 本 PR：request_snapshots 增加 sent 并默认过滤预检；Budget.input_token_ratio 跳过不可信 usage；_tool_chat 对不可信 usage 不更新校准基线；framing 改为 16 字节；context-log 增加 latency/completion/finish/usage_status；tools.jsonl 增加参数/结果哈希；模型可显式配置 reasoning_field，不做 reasoning 与 reasoning_content 的隐式互转。
- 未做（按工单延后）：思维链落盘、流式输出、缩短历史窗口、无进展检测/工具去重（PR2）。

## 验收步骤

1. 正常聊一句，打开「上下文」页：应能看到「预检」和真实调用分开，真实调用显示实际 token/输出/延迟。
2. 切到 Gemini 聊一句：续请求若只报很小的 prompt_tokens，页面/账本应标「用量不可信」，且后续上下文不被错误放大。
3. 用现有工具、记忆、自主活动和多气泡回复一遍，确认行为与之前一致。
