# 工单 129 能力记录按本地时间显示

## 目标
Peter 查能力记录时看到的是本地时间，不会再把刚上线的新能力当成昨晚的旧能力。

## 现状
`capabilities.json` 里的 `at` 用 UTC 保存（`+00:00`）。`capability_history` 把它原样交给 Peter，他按北京时间去读：2026-10-26 早上 06:20 刚上线的「瞄一眼」显示成 `2026-10-25T22:20`，他回答 Lan「没有新工具」，还为此存了一条自责记忆。

## 改动
- 保存格式不变，仍是 UTC。
- 交给 Peter 时转换成本机时区，格式 `2026-10-26 06:20 local time`（`capability_state.local_stamp`，`tools_agent.FilesystemTools.capability_record` 和 `Chat.capability_history_record` 都改用它）。
- 解析不了的值（比如只有日期的补录项）原样显示。

## 验收
更新后问 Peter 最近有什么新能力，他的能力记录里时间是本地时间，最新一项是今天的。
