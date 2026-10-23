# 工单 081：正式运行目录与开发 worktree 规则

## 目标

明确正式运行目录固定使用 `main`，开发使用独立 worktree，并保留现有 Main Update 更新流程及失败保护边界。

## 当前状态

`AGENTS.md` 尚未说明正式运行目录与开发 worktree 的边界。PR #124（工单 080）已合并至 `f4c6d3a5b14d9b40c058095689a2b0ed4a530faf`；其正式 Windows 更新和 Peter 聊天体验仍待用户验收。

## 验收步骤

1. 查看 `AGENTS.md`，确认正式目录、独立 worktree、现有 `update-main.cmd` 和失败保留数据的规则清楚，且未重复 PR、安全和验收流程。
2. 确认本 PR 只改规则与状态交接文档，没有修改 Update Main 实现；正式目录继续保持 `main`。
3. PR 合并后确认只清理本工单创建且无未提交修改的 worktree；有修改时保留并报告，不触碰正式目录或其他正在使用的 worktree。
