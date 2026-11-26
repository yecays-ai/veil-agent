# 453 · P6-S2 合并后加固：锁状态事务内复核与恢复时间戳严格化

- 产品线：B（长期记忆与共同经历连续性）；P6-S2（#450）合并后的独立 hotfix，不混入 P6-S1 的检索/卡片改动。
- 做完 Lan 能多做的一件事：她在记忆页面给某条经历上锁后，即使自动故事正好在同一时刻准备写入，锁定的正文/标题/检索向量也不会被覆盖一次；历史恢复遇到坏时间戳会直接拒绝整批，修好后重跑能把漏掉的消息补回，而不是永远显示“已恢复”。
- 验收：隔离环境新增回归测试先红后绿；P6-S2 专项、事件、经历与 Brain 写入相关套件全绿；PR CI 全绿且实质审查意见处理完；不写正式 `data/peter`、不启停正式 Peter。

## 目标

修复独立审查在 #450 合并后发现的两处低风险真实问题，保持 P6-S2 的事务原子性、单版本、digest 与既有 locked 分支语义不变：

1. **D1（数据完整性）**：`EventMemory.record()`（`event_memory.py:604` 附近）与 `revise()`（`event_memory.py:760` 附近）只在事务外检查 `locked`，`BrainAuthority.commit_event_bundle()` 的事务内只复核 `seen_projection`。如果面板在“检查之后、bundle 拿到写锁之前”锁定该记忆，自动故事仍会覆盖锁定正文一次。修复：bundle 在 `BEGIN IMMEDIATE` 之后按 projection 目标 `memory_id` 复核 `sidecar_state.locked`；锁定则绝不覆盖正文/标题/向量，改为提交阶段、保留已审故事、写 `pending_review` 与 `event-review` 证据（与既有“锁定事件新增阶段”语义一致）。`revise()` 在同样窗口命中锁时整体拒绝并返回 `locked`。`retry_pending()` 在锁定期间不提交、阶段保持可重试，解锁后可正常补提交。审查补充：`retry_dirty()` 的锁定路径同样会保存重建故事并清 `dirty`，导致解锁后新阶段永不投影；现锁定时保留已审故事与 `pending_review`/`story_pending`/`dirty`，解锁后再重建投影。
2. **D2（恢复脚本）**：`scripts/p6_s2_recover_history.py` 的 `_load_chat_slice()` 在消息时间解析失败时 `continue` 静默丢弃，但批次仍提交 `source_key`；重跑返回 `already_recovered`，漏掉的消息永远补不回。修复：窗口内任何时间戳无法解析（含缺失）即 `raise`，拒绝提交该批，与已有 strict JSON 解析一致。

## 当前状态

- 基线：`origin/main` 774b4bd（PR #450 P6-S2 已合并）。分支 `ticket/453-p6-s2-lock-recovery-hardening` 仅含本 hotfix。
- 改动文件：`brain_authority.py`（事务内锁复核、`ProjectionLocked`、`locked_event`/`locked_ledger_rows`/`abort_on_locked` 与结果 `locked` 标志）、`event_memory.py`（已审故事视图、`record()` 晚到锁定分支、`revise()` 锁冲突、`retry_pending()` 不提交锁定目标）、`scripts/p6_s2_recover_history.py`（严格时间戳）。
- 新增回归：`tests/test_p6_s2_hotfix_453.py`，5 条测试在未修复代码上全部失败（红证据），修复后全部通过（绿）。未删除或放宽任何既有测试。定向套件 248/248 通过；全量 `unittest discover` 2304 项中 22 项失败/错误与基线 `774b4bd` 干净 worktree 完全一致（本机缺 python-docx、无 opencode 服务、maintenance/delivery 环境限制），本 PR 触及测试全部通过。
- 取舍说明（D1）：优先“绝不覆盖锁定正文 + 阶段与证据不丢 + 解锁后可恢复”。因此锁内新阶段按既有语义照常提交并留 `pending_review`/`event-review` 证据；`retry_pending()` 的重试行在锁内保持 pending（不提交事件故事），解锁后重试可正常提交并经 `retry_dirty()` 投影。锁定期间不产生任何正文/标题/向量/版本写入。

## 验收步骤

1. 在隔离环境跑 `python -m unittest tests.test_p6_s2_hotfix_453 -v`：5 条回归全部通过（未修复代码上同一命令 5 条全红）。
2. 跑 P6-S2 专项与事件/Brain 相关套件（`test_p6_s2_review_fixes`、`test_p6_s2_write_and_recovery`、`test_event_memory`、`test_event_continuation_421`、`test_experience_memory`、`test_brain_*`），确认无回归。
3. 查看 PR CI 全绿（offline-chat ubuntu/windows × py3.11/3.12、audit、scanner-report-smoke）；不改正式 `data/peter`、不启停正式进程、不调用正式聊天。
