# HANDOFF

> 更新时间：2026-11-26T13:40:00Z

## 当前停点

- **P6-S2 / P6-S1**：均已合并并通过加固 PR（#450/#451/#454/#452/#453），CI 全绿。正式 `data/peter` 未动。
- **P6-S4**：`ticket/454-p6-s4-ablations`（worktree `E:\Peter-Agent\VEIL\worktrees\454-p6-s4-ablations`）PR #455 已完成全部 24/24 实验、Lan 全量 46 场景人工盲评与 P6-0（41 项）+ P6（20 项）原始需求覆盖审计（含 `混合检索+Rerank+判官` 4 路对照、`AB-COMB` 多机制端到端回复对照及时间衰减保护补测）：
  - 状态：**24 completed / 0 partial / 0 blocked**；
  - 预算：累计 **¥3.084122 / ¥20（15.4%）**（Tier 0 ¥0、Tier 1 ¥2.160207、Tier 2 ¥0.923915，1,006 条 append-only 结算记录）；
  - 验证：合成单测 **39/39 通过**，Baseline-1 门禁 **20/20 通过**，隐私/路径/密钥/人设泄露扫描 0 命中；
  - 交付文档：`docs/reports/p6-s4-ablations-20261126.md`、`docs/reports/p6-s4-decision-table-20261126.md`、`docs/reports/p6-s4-requirements-coverage-audit-20261126.md`；本机匿名对照 `.local-tmp/s4/lan-blind-review.md`（已由 Lan 全量审阅，不进 git）。
  - 按工单规则，**达到完整交付且可合并状态后停止，不自行合并，不启动 S3/S5/S6/S7**。

## 下一个窗口第一步

1. 确认 PR #455 最新 HEAD 对应 CI 全绿后，由 Lan 合并 PR #455。
2. S4 合并后按决策表与 `p6-s4-requirements-coverage-audit-20261126.md` 分阶段施工：
   - **S3**：四区独立存储预算（AB-A3）+ S2 新证据链下严格溯源晋升（AB-A2，循环自证率=0）+ 1500B 满载优先级压缩与 `stale_ref` 降级；
   - **S5**：中文停用词过滤（AB-R1b），判官链维持现状（可选灰度 K=12），优化工作/技术 `trap` 误报抑制，设计带情境相关性约束的聊天自然联想；Qwen3-Reranker 待本地化/异步方案（且须保留向量兜底 + 前置分数门控）；
   - **S7**：HEAD 前缀纯化（AB-C1）+ 非首轮省略空提示（AB-C2a）+ 预留 `p7.experience_hints` 槽位；不做 C2b 短闲聊硬截断与 C3 替换相关长事件全文；
   - **S6**：激活 `last_active` 与长期重要度动态重评（保护 `imp>=7`/`pinned` 零衰减且永不删除）；先修自主态旁白/无工具扮演再灰度 `AB-A1b`；`AB-E1` 认知巩固带开关与日预算顶做多日灰度并衔接 P7。
3. 继续遵守红线：不执行未授权的正式 `data/peter` 历史恢复；不启停正式 Peter。

## 维护窗口红线

- 正式恢复必须先创建协同 bundle，再 dry-run 给 Lan 看目标与追加预览；只有 Lan 明确确认后才执行 apply。
- 代码回退不得用旧 DB 覆盖新增记忆；恢复必须走 `restore_bundle`，并检查 0 重复、0 悬空引用。
- 换 Embedding 模型必须使用 `scripts/embedding_reindex.py` 显式重建（先 dry-run、再 `--apply`），不得依赖后台重试自动改模型。
