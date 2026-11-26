# HANDOFF

> 更新时间：2026-11-26T11:30:00Z

## 当前停点

- **P6-S2 / P6-S1**：均已合并并通过加固 PR（#450/#451/#454/#452/#453），CI 全绿。正式 `data/peter` 未动。
- **P6-S4**：`ticket/454-p6-s4-ablations`（worktree `E:\Peter-Agent\VEIL\worktrees\454-p6-s4-ablations`）PR #455 已完成接手施工与全部 24/24 实验交付：
  - 状态：**24 completed / 0 partial / 0 blocked**；
  - 预算：累计 **¥2.490224 / ¥20**（Tier 0 ¥0、Tier 1 ¥1.784595、Tier 2 ¥0.705629，821 条 append-only 结算记录，含真实 Peter 人设重跑）；
  - 验证：合成单测 **39/39 通过**，Baseline-1 门禁 **20/20 通过**，隐私/路径/密钥/人设泄露扫描 0 命中；
  - 本地匿名 A/B 回复对照：`.local-tmp/s4/lan-blind-review.md`（46 组真实人设回复，83/92 英文，不进 git），供 Lan 直接阅读体验。
  - 按工单规则，**达到完整交付且可合并状态后停止，不自行合并，不启动 S3/S5/S6/S7**。

## 下一个窗口第一步

1. 确认 PR #455 最新 HEAD 对应 CI 全绿及审查无阻断意见；请 Lan 抽读本机 `.local-tmp/s4/lan-blind-review.md` 与 `docs/reports/p6-s4-decision-table-20261126.md` 并拍板是否合并。
2. S4 合并后按决策表分阶段施工：
   - **S3**：四区独立存储预算（AB-A3）+ S2 新证据链下严格溯源晋升（AB-A2，循环自证率=0）；
   - **S5**：中文停用词过滤（AB-R1b），判官链维持现状（可选灰度 K=12）；Qwen3-Reranker 待本地化/异步方案；
   - **S7**：HEAD 前缀纯化（AB-C1）+ 非首轮省略空提示（AB-C2a）+ 短闲聊上限 Top-2+1（AB-C2b）；不做 C3 替换相关长事件全文；
   - **S6**：夜间认知巩固（AB-E1）与闲暇联想（AB-A1b）仅在带独立开关与日预算顶前提下小步灰度。
3. 继续遵守红线：不执行未授权的正式 `data/peter` 历史恢复；不启停正式 Peter。

## 维护窗口红线

- 正式恢复必须先创建协同 bundle，再 dry-run 给 Lan 看目标与追加预览；只有 Lan 明确确认后才执行 apply。
- 代码回退不得用旧 DB 覆盖新增记忆；恢复必须走 `restore_bundle`，并检查 0 重复、0 悬空引用。
- 换 Embedding 模型必须使用 `scripts/embedding_reindex.py` 显式重建（先 dry-run、再 `--apply`），不得依赖后台重试自动改模型。
