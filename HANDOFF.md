## 当前工单 250（2026-11-08 UTC）

- PR #305 已 squash 合并到 main，提交 `5582474`；只改表情网格样式，Windows 验收未观察。
- 新分支 `ticket/250-context-continuity` 从该提交建立。实现中：按当前角色与模型至少 16 KiB 的实际输入用量校准，估算增加 20% 余量；小样本/无数据时保留保守回退；工具循环新增内容在有可靠校准时用同一比例。
- 本地长度停止原因通过错误事件单独显示，不保存成 Peter 正文。正常输出遇到 `length` 最多追加一次受预算约束的续接；真正本地或供应商上下文上限均停止，不循环重试。
- `context-log.jsonl` 增加工具循环估算/实测输入、Schema 字节、模型上限和本地停止触发原因；供应商上下文拒绝写入独立诊断。
- `tests.test_chat tests.test_tools.ToolLoopTests tests.test_tool_groups tests.test_image_recovery_and_context` 本地共 138 项通过。扩大到 Web 套件时出现 DNS/启动/端口相关失败，已中断该批次，不能记为通过。PR/Cubic 检查和 Windows Electron 实际体验尚未完成。

## 下一个窗口第一步

读取分支实际 diff 与新增回归用例，确认工具结果裁剪、预算预留、错误事件和回复续接的边界；提交并开 PR，等必需检查及 Cubic 审查，再按仓库规则合并。

## 背景和边界

- `Budget.input_token_ratio` 已按角色和模型记录实际供应商用量并保留历史高水位；估算器先前的 0.4 校准下限和工具循环的 0.5 新增字节下限会盖过较低的实际比例。
- 65K 是用户确认的当前模型输入上限，本工单不改配置，不删除聊天、记忆、表情包或 MCP 工具。
- 不调用付费模型；最终 Windows 体验由 Lan 验收，不把 CI 结果当作实机验收。
