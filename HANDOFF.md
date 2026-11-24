# HANDOFF

## P2 收尾（2026-11-24，分支 `ticket/439-p2-followup`，PR #439）

- 已完成：embedder 边界、config-root `.env` 隔离、事件迁移折叠/digest、只读读取降级、pending 三态与
  重试、归档页读 Brain、合并/锁定/sidecar 修复；隔离完整验收（数据/桌面/备份/失败/连续性/旧机制/
  黄金集/切换回退）均通过，矩阵见 `docs/tickets/p2/P2-acceptance-matrix.md`。
- 实机证据：仓库外 `p2-439/iso/evidence/`；仓库只提交脱敏报告与逐条 ID 清单。
- 未做：正式切换（需 Lan 授权）、正式库标签清理（需维护窗口）、Lan 正式体验验收。
- 阻塞：正式 `.env` 的 `EMBED_API_KEY` 当前 401；页面隐藏按钮归 #431；黄金集 4/50 Ombre-only 待复核。
- 下一步第一步：Lan 复审 PR #439；更新正式 EMBED key 后，按 `docs/tickets/p2/P2-B-runbook.md` 申请切换窗口。
- 禁止：本 PR 不由 agent 合并；未完成正式体验验收前不得进入 P3。
