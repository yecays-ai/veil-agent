# HANDOFF

> 更新时间：2026-11-25T22:45:00Z

## P6-S2 · `ticket/450-p6-s2-write-contract-and-recovery`

- **停点**：实现、专项测试、相关回归、两类故障恢复演练及真实历史隔离副本预演已完成；下一步仅是 PR 的 CI/审查处理。正式目录和真实记忆未写入。
- **核心交付**：
  - `BrainAuthority.commit_event_bundle()` 已接入 `EventMemory.record()` 与 `revise()`，memory/version/event/stage/evidence/quotes/write journal/canonical digest 单事务提交；相同正文不再制造空版本或破坏向量。
  - `ExperienceMemory` 区分并发冲突、事实不匹配和未修复草稿；事实不匹配转 `update-pending-review` 并安全追加阶段，未修复草稿进入 `manual-review`。
  - `_capture_evidence` 按通道配额保留用户原话与全部批次 message IDs；`_merge_many` 将新阶段融入现有故事。
  - `brain_backup` 协同备份 Brain 与 companion 状态；恢复合并保留备份后新对象及既有对象的正文、版本、阶段、关系、quotes 和 ledger。
  - `scripts/p6_s2_recover_history.py` 默认只预览，apply 受角色写锁、单事务、锁定检查与 `source_key` 唯一键保护。
- **验证证据**：专项 16 项、事件 28 项、经历整理 54 项、Brain 53 项通过。隔离目标批次为 58 条消息；首次 apply 新增 1 stage/1 version/1 recovery key，二次为 `already_recovered`，SQLite quick check 与 canonical digest 均通过。

## 下一个窗口第一步

1. 读取 PR 最新 CI 与审查结论，只处理功能、数据完整性、隐私或安全方面的实质问题。
2. 本单只要求提交 PR 后停止，不得合并；也不得对正式 `data/peter` 执行历史恢复。
3. 合并后再从最新 `main` 开 P6-S1 独立 worktree。

## 维护窗口红线

- 正式恢复必须先创建协同 bundle，再 dry-run 给 Lan 看 58 条消息的目标与追加预览；只有 Lan 明确确认后才执行 apply。
- 代码回退不得用旧 DB 覆盖新增记忆。恢复必须走 `restore_bundle`，并检查 0 重复、0 悬空引用。
- `.local-tmp/p6-s2-history-preview/` 是 worktree 内忽略的隔离副本，不提交；其中含真实数据副本，worktree 清理时一并删除，不得发布到 PR。
