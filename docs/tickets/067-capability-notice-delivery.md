# 067｜能力变化通知可靠交付

## 目标

给 Peter 增加、移除或调整工具权限后，他会收到完整英文更新；程序按日期将同一正文保存在他的能力记录中，之后每次聊天都可作为程序状态供他查阅，不向 Lan 弹出通知。

## 当前状态

`tools_agent.py` 同时保存中文 `pending_event` 和英文简略 `pending_event_model`，而 `Chat.send()` 又在模型请求成功前清除通知；因此 Peter 会收到缩水内容或在失败后收不到。已按 reuse-first 检索 GitHub，未找到能直接用于本地 Python 聊天与 capabilities.json 持久化链路的成熟组件，本单将在现有事务机制内修复。

## 验收步骤

1. 给 Peter 增加 `lab_file_digest` 与 `move_lab_file`；页面不应要求 Lan 转交任何能力通知。
2. 问 Peter “Did you get the update about your new tools?”；他无需解释即可说明两项工具和权限限制。
3. 重启后再问一次，确认他仍能说明记录中的更新。
