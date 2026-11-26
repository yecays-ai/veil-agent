# HANDOFF

> 更新时间：2026-11-26T10:25:00Z

## 当前停点

- **P6-S2**：`#450` 合并 `774b4bd`；合并后加固 `#451` 合并 `beb5fa3`；补充加固 `#454` 合并 `588701c`（`retry_pending()` 锁定阶段跳过且不占批次名额，避免批次饿死；7 条回归先红后绿，定向 363/363）。三者 CI 全绿。正式数据未动，维护窗口恢复仍需 bundle + dry-run + Lan 确认。
- **P6-S1**：`#452` 合并 `fd59f7a`；合并后加固 `#453` 合并 `b3d14c2`（换模型待索引 0 付费/0 写入 + `scripts/embedding_reindex.py` 显式重建、诊断 API 透出、日期反转校验；7 项回归 + Baseline-1 20/20）。桌面 UI 展示归 P6-S7。
- **P6-S4**：`ticket/454-p6-s4-ablations`（worktree `E:\Peter-Agent\VEIL\worktrees\454-p6-s4-ablations`）已开 PR #455，24 个实验 ID 状态 17 completed / 5 partial / 2 blocked；总花费 ≈¥1.62（Tier1 ≈¥1.54、Tier2 ≈¥0.08、Tier0 ¥0）。CodeRabbit 6 条 Major 已按实质问题在本分支修复中（CLI 选择/--skip-paid/账本互斥/judge 缺 usage/embedding 预扣/故事门控口径），修完重跑受影响实验与 CI 后停止，**不自行合并**。

## 下一个窗口第一步

1. 看 P6-S4 PR #455 最新 CI 与 CodeRabbit 实质意见；确认修复后 HEAD 的 CI 全绿、报告/覆盖表/预算一致，交 Lan 决定是否合并（不得自行合并）。
2. S4 合并后按白话决策表执行：S5 只做 AB-R1b 停用词过滤并维持判官现状；S3 做 AB-A3 四区独立预算（先迁移演练），晋升扩权先补 chat 证据链；S6 暂缓；S7 用真实请求复测 C1 缓存保留率与 C2/C3 幻觉/自然度。
3. 不执行正式 `data/peter` 历史恢复；不启停正式 Peter。S4 未修改正式运行环境与真实数据。

## 维护窗口红线

- 正式恢复必须先创建协同 bundle，再 dry-run 给 Lan 看目标与追加预览；只有 Lan 明确确认后才执行 apply。
- 代码回退不得用旧 DB 覆盖新增记忆；恢复必须走 `restore_bundle`，并检查 0 重复、0 悬空引用。
- 换 Embedding 模型必须使用 `scripts/embedding_reindex.py` 显式重建（先 dry-run、再 `--apply`），不得依赖后台重试自动改模型。
