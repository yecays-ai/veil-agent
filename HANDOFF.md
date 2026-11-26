# HANDOFF

> 更新时间：2026-11-26T03:58:00Z

## P6-S2 · `ticket/450-p6-s2-write-contract-and-recovery`

- **停点**：已完成 CodeRabbit 二轮全部 14 项（11 条行内 + 3 条 diff 外）定点修复及新增回归测试，已推送至 PR #450 并关闭本轮已解决讨论串，等待 CI 与审查。正式目录和真实记忆未写入。
- **核心交付**：
  - `BrainAuthority.commit_event_bundle()` 已接入 `EventMemory.record()`、`revise()` 与 `retry_pending()`；`record()` 在生成故事前取基线并保留 `locked` 分支的 `last_story_at`；`revise()` 未提供新标题时不覆盖面板标题；`retry_pending()` 缺 `memory_id` 时同步创建投影与 `quotes`。
  - `brain_writer` 将 `ensure_schema()` DDL 与 `retry_pending_vectors()` 状态变更均与 `apply_digest()` 纳入同一 SQLite 事务；`apply_update_memory(..., archive_raw=True)` 在 Brain `hold` 重写时由单条版本保存完整旧 Markdown（`restore_version` 安全剥离 front matter）。
  - `event_memory` 统一 `facts()` 与 `_date_parts()` 对含年份中文日期的提取，阶段已知年份时严格要求匹配日期同年份（不再被编号等独立数字拼凑），`timeline()` 对模型可见证据边界统一脱敏 API Key（`chat.jsonl` 原文不变）。
  - `ExperienceMemory.__init__` 为常驻 `self.events` 绑定 `self.sidecar.locked`；`_save_update` 区分目标缺失与 `'locked'`。
  - `brain_backup._merge_new_rows` 对缺失 `sidecar_events` 跳过合并；`scripts/p6_s2_recover_history.py` 追加恢复 marker 前检查末尾行完整性，缺少换行时自动补换行、末行损坏则拒绝写入。
- **验证证据**：`test_p6_s2_review_fixes`、`test_p6_s2_write_and_recovery`、`test_event_memory`、`test_experience_memory`、全部 `brain_*` 与 `memory_*`、`test_p3_4_cleanup` 共 15 个套件 **291/291 通过**。

## 下一个窗口第一步

1. 读取 PR 最新 CI 与审查结论，只处理功能、数据完整性、隐私或安全方面的实质问题。
2. 本单只要求提交 PR 后停止，不得合并；也不得对正式 `data/peter` 执行历史恢复。
3. 合并后再从最新 `main` 开 P6-S1 独立 worktree。

## 维护窗口红线

- 正式恢复必须先创建协同 bundle，再 dry-run 给 Lan 看 58 条消息的目标与追加预览；只有 Lan 明确确认后才执行 apply。
- 代码回退不得用旧 DB 覆盖新增记忆。恢复必须走 `restore_bundle`，并检查 0 重复、0 悬空引用。
- `.local-tmp/p6-s2-history-preview/` 是 worktree 内忽略的隔离副本，不提交；其中含真实数据副本，worktree 清理时一并删除，不得发布到 PR。
