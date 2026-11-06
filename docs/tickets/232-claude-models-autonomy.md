## 目标
在模型页选择 Claude Code 的 Sonnet、Haiku 或 Opus，并为自主活动单独选择 API 模型，让聊天改用 Claude Code 后自主活动仍能正常运行。

## 当前状态
Claude Code 目前启动时未传 `--model`，实际版本由账户设置决定；`claude_agent_channel.py`、`characters/peter/character.toml`、`model_settings.py` 和模型页共同负责模型选择及启动。自主唤醒从 `autonomy.py` 调用 `Chat.send(..., autonomy=True)`，复用 Peter 聊天模型；当聊天模型是 Claude Code 时会被 `_claude_agent_chat` 拒绝。沿用现有模型列表与 provider 临时切换机制；自主活动排除 Claude Code，只允许选 API 模型。Haiku、Sonnet、Opus 由 Claude Code 官方 `--model` 别名选择；默认明确设为 Sonnet。

## 验收步骤
1. 在模型页将 Peter 聊天切换为 Claude Code，确认可选 Sonnet、Haiku、Opus；选择 Haiku 后聊天正常回复。
2. 在同一模型页为“自主活动”选择一个 API 模型，保持 Peter 聊天使用 Claude Code；启用自主活动并等待下一次唤醒，确认活动正常完成且记录归属自主活动费用。
3. 确认“瞄一眼”原有模型选项仍生效，停止 Peter 后没有残留进程。
