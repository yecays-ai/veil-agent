# 交接

## 当前进度

- PR #122、#123 和 #124 均已合并；PR #124（工单 080）合并提交为 `f4c6d3a5b14d9b40c058095689a2b0ed4a530faf`，当前最新 main 与本次开发 worktree 的基点一致。
- PR #124 记录了 Electron 缓存打包和 Start Desktop 本地启动检查通过；正式 `update-main.cmd` 更新与 Peter 实际聊天的 Windows 体验仍待 Lan 验收。
- 当前工单 081 使用 `E:\Peter-Agent\VEIL\veil-agent-codex` 独立 worktree，分支为 `ticket/081-formal-dev-directory`；改动范围为 `AGENTS.md`、`STATUS.md`、`HANDOFF.md` 和工单文件，不改 Update Main 实现。

## 下一步

完成工单 081 的文档复核，提交并推送一个 PR，处理实质评审与必需检查。之后请 Lan 完成 PR #124 的 Windows 体验验收，再按 `STATUS.md` 另行安排桌面 UI 整合改版。

## 仍然成立的边界

- `E:\Peter-Agent\VEIL\veil-agent` 是正式运行目录，固定使用 `main`；开发和提交使用独立 worktree。
- 正式目录继续经现有 `update-main.cmd` 更新；不要要求 PR 合并后人工同步，也不要修改这套更新机制来完成本工单。
- 任何更新失败都必须保留本地修改和用户数据。正式 Update Main 和 Peter 实际聊天仍需 Lan 在 Windows 上验收。
