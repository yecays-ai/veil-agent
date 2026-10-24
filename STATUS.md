# 当前状态

更新：2026-10-24 UTC · Codex。

## 当前目标与现状

- Reliability 五阶段已完成；运行期监测与安全恢复退出条件满足。
- PR #126（工单 082）已合并，Tests、Windows desktop、Dependency security 均通过；Lan 已确认桌面 Windows 体验验收完成。
- PR #127（工单 083）已合并，聊天恢复、上下文预算校准和 DSML 原文拦截已进入 `main`；对应 CI 已通过，Lan 已确认最新桌面修改与验收完成。
- PR #128（工单 084）已合并，提交 `283f3be`；同步了 Reliability 和已完成的桌面验收状态。
- 工单 086「Chat 与 Memory 多条 assistant 消息轮次契约」在独立分支 `ticket/086-chat-turn-contract`。约定一个用户事件和它到下一用户事件前的所有 assistant 事件组成一轮；Memory 只计已完成的用户轮次，assistant-only 开场和未回复用户消息不推进游标。读取文件的轮次不向记忆提取暴露任何 assistant 回复正文。
- 当前修改统一了轮次分组与工具请求编号的配对，补了多 assistant 回复和文件读取遮蔽回归用例；未读写用户真实聊天、记忆或媒体。目标用例 3 项通过；完整 `tests.test_chat` / `tests.test_memory` 共 116 项中 114 项通过，另 2 项因本机缺少 `httpx2`、`python-dotenv` 未能运行。

## 下一步

完成目标单测与 diff 检查后只提交一次；创建 PR #130 后手动请求 CodeRabbit 评论。自动检查和评论结果以 PR #130 当前 head 为准，处理实质问题后再合并。

## Lan 的体验验收

1. 在一轮中让 Peter 连续发送两条独立回复，重启后确认两条都按原顺序保留，并出现在后续上下文中。
2. 完成四轮（其中一轮含多条 assistant 回复）后，确认自动记忆仍以四个用户轮次为批次；如该轮使用过文件读取，记忆提取不带入那一轮的 assistant 正文。
