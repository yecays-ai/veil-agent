# 当前状态

更新：2026-11-08 UTC。

## 当前目标

修复 Peter 的输入 Token 估算过高、工具循环提前停止及回复截断；真实超限时保留安全停止。

## 现状

- PR #305（表情包网格）已 squash 合入 main，提交 `5582474`；Windows 体验验收仍待 Lan 确认。
- 工单 250 在分支 `ticket/250-context-continuity`。已追到实际用量校准被 0.4 下限覆盖、工具新增内容按 0.5 下限估算，以及本地长度停止原因被作为普通回复返回。
- 当前分支用至少 16 KiB 的实际用量和 20% 余量校准；小样本/无数据时使用保守回退。工具 Schema/结果新增内容在有可靠校准时复用该比例。上下文停止提示独立输出；输出截断最多续接一次。已添加模型、上限、估算/实际输入、Schema 字节和停止原因诊断。
- 相关本地回归 `tests.test_chat tests.test_tools.ToolLoopTests tests.test_tool_groups tests.test_image_recovery_and_context`：138 项通过。扩展 Web 测试运行中出现 DNS/启动/端口相关失败并已中断，因此不记为通过；待 PR CI。Windows Electron 实际聊天体验仍待 Lan 验收。

## 下一步

完成 diff 审查、提交并开 PR；等待 CI 和 Cubic 审查后合并，再请 Lan 按下方步骤在 Windows 上验收。

## Lan 的 Windows 体验验收

1. 在当前 65K 配置下，让 Peter 连续阅读至少五个 Galatea Garden 帖子、展开 social 工具组并继续聊天；不应因估算偏高提前停止。
2. 让 Peter 写一段较长回复，确认能自然收尾；真正达到上限时，停止提示单独显示，已发出的消息仍保留。
3. 确认记忆、聊天历史、表情包、social 工具组恢复和费用限制仍可正常使用。
