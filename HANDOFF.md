# HANDOFF

> 更新时间：2026-11-26T08:20:00Z

## P6-S2 · 统一记忆写入事务（已合并；合并后加固 PR #451 待合并）

- **停点**：PR #450 已合并为 `774b4bd6`；独立核验 43 项审查意见全部修复，264/264 相关测试通过，CI 全绿。
- **合并后加固**：PR #451（分支 `ticket/453-p6-s2-lock-recovery-hardening`，基于 S1 合并后的 main 变基）修复 2 处低风险问题：① `commit_event_bundle()` 在 `BEGIN IMMEDIATE` 后复核 `sidecar_state.locked`，锁定时不覆盖正文/标题/向量/版本，改为提交阶段+已审故事+`pending_review`/`event-review` 证据；`revise()` 命中锁返回 `locked`；`retry_pending()` 锁内保持 pending；`retry_dirty()` 锁内不重建/不保存并保留待处理标志。② 恢复脚本对缺失/无法解析时间戳直接拒绝整批，不再静默丢弃后提交 `source_key`。新增 6 项回归（先红后绿），P6-S2 定向 338/338 通过。**下一步**：等 PR #451 CI 与实质审查通过后合并，再进入 P6-S4。
- **正式数据红线未变**：不得对正式 `data/peter` 执行历史恢复；维护窗口必须先 bundle + dry-run，经 Lan 确认后再 apply；代码回退不得用旧 DB 覆盖新增记忆。

## P6-S1 · 检索、卡片与元数据确定性修复（已合并）

- **合并**：PR #452 已合并为 `fd59f7a6`；工作树 `E:\Peter-Agent\VEIL\worktrees\452-p6-s1`（`ticket/452-p6-s1-retrieval-determinism`）已完成使命。
- **已完成**：
  - `recall_judge.split_card()/card_text()` 兼容 LF/CRLF/BOM；`brain_writer._with_metadata()` 原位更新 CRLF front matter，不批量改写历史记忆（G-A04）。
  - `Memory.context()` 将 `judge.prefetch()` 的 query_vector 共享给 `search_candidates()`，同轮一次 Embedding、一次预留/结算；判官未启用时检索自己 Embedding（G-A05）。
  - `memory_native` → `BrainAuthority.breath_search` → `brain_retrieval.search_text` 全链路传 date_from/date_to，按创建日含边界过滤；权重读 `[memory.retrieval]`（G-A09）。
  - 解除 `dim==1024` 硬编码；meta.embedding_model 记录/校验模型身份，维度或模型不兼容时跳过/降级并留诊断，写入侧拒绝混写向量、pending 恢复保留（G-A14）。
  - 判官 embedding 与模型调用分离计时，诊断区分正常空召回/embedding 失败/模型超时/降级回退（G-A12）。
  - 移除 `reuse_recall()` 与随机 drift 死分支及相关诊断；保留 followup carry、cooldown、is_followup、last_injected_refs、轮内去重与 relationship_base 去重（G-B01/G-A08）。
  - 新增 `tests/test_p6_s1_deterministic_fixes.py`（17 项）与 `scripts/p6_s1_baseline.py`（Baseline-1，18/18 项检查通过），报告见 `docs/reports/p6-s1-baseline-1-20261126.md`。
- **验证**：相关历史套件失败集与 `main` 基线完全一致（无本单引入回归）；新增测试全绿；Baseline-1 隔离、零付费、可复跑。
- **下一个窗口第一步**：先完成 P6-S2 合并后加固 PR #451 的 CI/审查与合并，再从最新 main 开 P6-S4 独立 worktree。
- **S4 前置**：P6-S4 的 eval-v2 候选材料已离线准备（隔离副本、AI 辅助标注、未经 Lan 人工确认），位于 `.local-tmp/s4-prep`，未进 git；S4 开工时按工单重新核对标注独立性，再提交脱敏材料。
