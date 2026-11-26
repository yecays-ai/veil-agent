# HANDOFF

> 更新时间：2026-11-26T07:10:00Z

## P6-S2 · 统一记忆写入事务（已合并，独立加固进行中）

- **停点**：PR #450 已合并为 `774b4bd6`；独立核验 43 项审查意见全部修复，264/264 相关测试通过，CI 全绿。
- **合并后加固**：另发现 2 处低风险问题——`EventMemory.record()/revise()` 的锁状态在事务外检查（面板加锁与 bundle 提交之间的毫秒窗口可能让自动故事覆盖锁定正文一次）；恢复脚本对无法解析时间戳静默跳过却仍提交 source_key。二者由独立 hotfix 分支/PR 处理（不与 P6-S1 混提），完成后更新本文件。
- **正式数据红线未变**：不得对正式 `data/peter` 执行历史恢复；维护窗口必须先 bundle + dry-run，经 Lan 确认后再 apply；代码回退不得用旧 DB 覆盖新增记忆。

## P6-S1 · 检索、卡片与元数据确定性修复（施工中）

- **工作树/分支**：`E:\Peter-Agent\VEIL\worktrees\452-p6-s1`，`ticket/452-p6-s1-retrieval-determinism`。
- **已完成**：
  - `recall_judge.split_card()/card_text()` 兼容 LF/CRLF/BOM；`brain_writer._with_metadata()` 原位更新 CRLF front matter，不批量改写历史记忆（G-A04）。
  - `Memory.context()` 将 `judge.prefetch()` 的 query_vector 共享给 `search_candidates()`，同轮一次 Embedding、一次预留/结算；判官未启用时检索自己 Embedding（G-A05）。
  - `memory_native` → `BrainAuthority.breath_search` → `brain_retrieval.search_text` 全链路传 date_from/date_to，按创建日含边界过滤；权重读 `[memory.retrieval]`（G-A09）。
  - 解除 `dim==1024` 硬编码；meta.embedding_model 记录/校验模型身份，维度或模型不兼容时跳过/降级并留诊断，写入侧拒绝混写向量、pending 恢复保留（G-A14）。
  - 判官 embedding 与模型调用分离计时，诊断区分正常空召回/embedding 失败/模型超时/降级回退（G-A12）。
  - 移除 `reuse_recall()` 与随机 drift 死分支及相关诊断；保留 followup carry、cooldown、is_followup、last_injected_refs、轮内去重与 relationship_base 去重（G-B01/G-A08）。
  - 新增 `tests/test_p6_s1_deterministic_fixes.py`（17 项）与 `scripts/p6_s1_baseline.py`（Baseline-1，18/18 项检查通过），报告见 `docs/reports/p6-s1-baseline-1-20261126.md`。
- **验证**：相关历史套件失败集与 `main` 基线完全一致（无本单引入回归）；新增测试全绿；Baseline-1 隔离、零付费、可复跑。
- **下一个窗口第一步**：提交 PR → 请求 CodeRabbit 审查并修实质问题 → CI 全绿后合并 → 同步 main，从最新 main 开 P6-S4。
- **S4 前置**：P6-S4 的 eval-v2 候选材料已离线准备（隔离副本、AI 辅助标注、未经 Lan 人工确认），位于 `.local-tmp/s4-prep`，未进 git；S4 开工时按工单重新核对标注独立性，再提交脱敏材料。
