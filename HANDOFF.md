# HANDOFF

> 更新时间：2026-11-26T02:51:00Z

## P6-S2 · `ticket/450-p6-s2-write-contract-and-recovery`

- **停点**：已完成 CodeRabbit 审查 29 项定点修复、新增 30 项审查专项测试（`tests/test_p6_s2_review_fixes.py`）及全量基线对照，已推送至 PR #450 等待 CI 与审查。正式目录和真实记忆未写入。
- **核心交付**：
  - `BrainAuthority.commit_event_bundle()` 已接入 `EventMemory.record()`、`revise()` 与 `retry_pending()`，事务内通过 `seen_projection` 复核并发冲突（`ProjectionConflict` / `version_conflict`）；`_retry_pending` 写回前复核 `body` + `vector_state` 且不向外抛异常；`ensure_schema()` 建空表时同步刷新 `meta.digest`。
  - `ExperienceMemory` 区分 `applied / locked / fact_mismatch / conflict`，事实不匹配整份草稿仅进 `update-pending-review` 不直接入时间线；日期年份严格校验（`2026-11-25` 不匹配 `2025-11-25` 或无年份 `11月25日`，仅日期区间内数字豁免数量校验，`11-250` 不误匹配）；锁定事件传已提交正文不污染可读缓存。
  - `_capture_evidence` 限制时间上界不晚于阶段时刻并保留用户原话通道配额；`_merge_many` 通过 `carried` 保留累计与本批事实；`memory_native` Brain 路径改标题时额外归档完整旧 Markdown 版本。
  - `brain_backup` 强制清单路径与 sha256 校验、物理行 JSONL 读写（兼容 U+2028）、保留 `recall-rotation.json` 与既有状态、将 companion 文件纳入同一次恢复回滚快照、校验失败还原安全副本，且子表复制失败向外传播回滚。
  - `scripts/p6_s2_recover_history.py` 按时刻解析边界并归一化 `source_key`（兼容旧格式键）、先查已提交恢复键再查源消息、回退读取 `message_id`，并将审计 marker 写入纳入 `write_lock`。
- **验证证据**：`tests/test_p6_s2_review_fixes.py`（30 项）+ `tests/test_p6_s2_write_and_recovery.py`（19 项）共 49 项全过；全量 134 个测试文件与 `dd9a1da` 基线逐文件比对，120 个文件全过，14 个沙盒/环境类失败与基线完全一致（零新增回归）。

## 下一个窗口第一步

1. 读取 PR 最新 CI 与审查结论，只处理功能、数据完整性、隐私或安全方面的实质问题。
2. 本单只要求提交 PR 后停止，不得合并；也不得对正式 `data/peter` 执行历史恢复。
3. 合并后再从最新 `main` 开 P6-S1 独立 worktree。

## 维护窗口红线

- 正式恢复必须先创建协同 bundle，再 dry-run 给 Lan 看 58 条消息的目标与追加预览；只有 Lan 明确确认后才执行 apply。
- 代码回退不得用旧 DB 覆盖新增记忆。恢复必须走 `restore_bundle`，并检查 0 重复、0 悬空引用。
- `.local-tmp/p6-s2-history-preview/` 是 worktree 内忽略的隔离副本，不提交；其中含真实数据副本，worktree 清理时一并删除，不得发布到 PR。
