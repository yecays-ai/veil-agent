## 目标
在模型页选择 Claude Code 的 Sonnet、Haiku 或 Opus，并为自主活动单独选择 API 模型，让聊天改用 Claude Code 后自主活动仍能正常运行。

## 当前状态
实现已完成：Claude Code 按模型页所选别名传入 `--model`；自主唤醒使用独立 API 模型并在请求结束后恢复聊天 provider；瞄一眼与记忆后台保持原行为。无可用 API 模型时模型页仍可打开，自主活动会给出配置提示。Haiku、Sonnet、Opus 使用 Claude Code 官方 `--model` 别名，旧 `claude-agent` 值兼容映射为 Sonnet。Windows Peter 实机验收仍待完成。

## 验收步骤
1. 在模型页将 Peter 聊天切换为 Claude Code，确认可选 Sonnet、Haiku、Opus；选择 Haiku 后聊天正常回复。
2. 在同一模型页为“自主活动”选择一个 API 模型，保持 Peter 聊天使用 Claude Code；启用自主活动并等待下一次唤醒，确认活动正常完成且记录归属自主活动费用。
3. 确认“瞄一眼”原有模型选项仍生效，停止 Peter 后没有残留进程。
