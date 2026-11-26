# 当前状态

> 更新时间：2026-11-26T09:20:00Z（P6-S2 与 P6-S1 已合并；P6-S1 合并后加固进行中；P6-S4 进行中）

## P6-S2 · 统一记忆写入事务、防丢稿、证据链与协同恢复（已合并 + 合并后加固）

- **合并**：PR #450 squash 合并为 `774b4bd`；合并后加固 PR #451 squash 合并为 `beb5fa3`（锁状态事务内复核、恢复时间戳严格化、retry_dirty 锁定路径），CI 全绿；加固后 P6-S2 定向套件 338/338 通过。
- **核验**：独立只读核验确认 #450 的 43 项审查意见全部能在对应代码/回归中定位；#451 的 1 条 CodeRabbit 实质意见（retry_dirty 锁定路径）已在 PR 内修复并重新通过 CI。
- **正式数据红线**：未对正式 `data/peter` 执行恢复；维护窗口仍需先 bundle + dry-run，经 Lan 确认后再 apply。

## P6-S1 · 检索、卡片与元数据确定性修复（#452 已合并；合并后加固进行中）

- **合并**：PR #452 squash 合并为 `fd59f7a`，CI 全绿；交付 6 项修复、17 项新增回归、Baseline-1（20/20）、定向回归 450/450；全量套件失败集与 `main` 基线逐条一致（无本单引入回归）。
- **合并后独立审查**：发现 2 个阻断项与若干诊断缺口——① 换 Embedding 模型后 `retry_pending_vectors` 先付费 embed 再拒绝，pending 永久不可恢复并重复扣费；② 新结构化诊断未透出到 P4/P5 读取的 API 白名单。另有判官失败候选数被覆盖为 0、共享 Embedding 等待时间未记录、关键词降级原因丢失等。
- **加固单**：分支 `ticket/455-p6-s1-postmerge-hardening` 已修复上述问题：重试前置模型一致性校验（0 付费/0 写入）、`scripts/embedding_reindex.py` 显式重建流程、API 透传 retrieval/degraded、诊断修正与日期反转校验；新增 7 项回归、Baseline-1 扩为 20 项全通过。桌面 UI 展示按 P6-4 职责边界归 P6-S7。
- **验收步骤**：见 `docs/tickets/455-p6-s1-postmerge-hardening.md` 与 `docs/tickets/452-p6-s1-retrieval-determinism.md`。

## P6-S4 · 统一消融实验（进行中）

- 分支 `ticket/454-p6-s4-ablations`，隔离 `recall-lab` harness + eval-v2（73 条，AI 辅助标注、未经 Lan 人工确认）+ 预算台账（¥20 硬上限）。
- 当前进度与最终报告以该 PR 的 `docs/reports/p6-s4-*` 为准；S4 达到可合并状态后停止，不自行合并、不启动 S3/S5/S6/S7。

## P6 路线

`P6-S0`（#449）→ `P6-S2`（#450 +#451）→ `P6-S1`（#452 + 本加固）→ `P6-S4` 进行中；之后按 S4 结论实施 S3/S5/S6/S7。P6 不改 M2 的 `input_token_ratio/tool_groups`、不改 M3 的缓存协议，也不削减瞄一眼/弹幕连续性。

## 正式底座

- P3-0 至 P3-4 已合并并完成正式维护部署；#446/#448 已合并。
- 正式运行目录固定跟随 `main`。P6 各单未向正式 Peter 发消息、未启停正式进程、未修改正式源码或真实记忆；所有写入与恢复演练均在独立 worktree 隔离副本完成。
