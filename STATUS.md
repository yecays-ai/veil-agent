# 当前状态

> 更新时间：2026-11-26T08:20:00Z（P6-S2 合并后加固 PR #451 待合并；P6-S1 已合并）

## P6-S2 · 统一记忆写入事务、防丢稿、证据链与协同恢复（已合并 + 合并后加固 PR #451）

- **合并**：PR #450 已合并为 `774b4bd6`（squash），分支已删除；main 最新 CI 全绿（audit、offline-chat ubuntu/windows ×3.11/3.12、scanner-report-smoke）。
- **交付核验**：独立只读核验 43 项 CodeRabbit 意见全部能在 HEAD 定位到修复与回归测试；P6-S2 专项、事件、经历、Brain 与 P3-4 相关 264/264 测试通过（隔离、无付费调用）。
- **合并后加固（PR #451，分支 `ticket/453-p6-s2-lock-recovery-hardening`，待合并）**：独立审查发现 2 处低风险问题，已独立 hotfix 修复，不混入 P6-S1：
  1. 锁状态事务内复核：`record()/revise()` 的 `locked` 检查在事务外，`commit_event_bundle()` 只在事务内复核 `seen_projection`；现在 bundle 在 `BEGIN IMMEDIATE` 后按目标 `memory_id` 复核 `sidecar_state.locked`，锁定时绝不覆盖正文/标题/向量/版本，新阶段照常提交并保留已审故事与 `pending_review`/`event-review` 证据；`revise()` 命中锁返回 `locked`；`retry_pending()` 锁内保持 pending、解锁可补提交；`retry_dirty()` 锁内不重建/不保存并保留待处理标志。事务原子性、单版本与 digest 逻辑未变。
  2. 恢复时间戳严格化：`scripts/p6_s2_recover_history.py` 对缺失/无法解析的消息时间戳直接拒绝整批，不再静默丢弃后提交 `source_key`；修好数据后重跑可补回。
- **验证**：新增 `tests/test_p6_s2_hotfix_453.py` 6 项回归（修复前 5/5 与 1/1 红，修复后全绿）；P6-S2 专项、事件/经历、Brain 与 P3-1/P3-2/P3-3 等 338/338 通过；全量 `unittest discover` 2305 项中 22 项失败/错误与基线 `774b4bd` 干净 worktree 完全一致（本机缺 python-docx、无 opencode 服务、maintenance/delivery 环境限制），非本单引入。PR #451 最终提交 `666277b` CI 全绿（offline-chat ubuntu/windows × py3.11/3.12、audit、scanner-report-smoke），CodeRabbit 1 条 Major 已在本 PR 修复，Cubic 额度不可用；等待合并。
- **正式数据红线**：未对正式 `data/peter` 执行恢复；维护窗口仍需先 bundle + dry-run 并由 Lan 确认。

## P6-S1 · 检索、卡片与元数据确定性修复（已合并）

- **合并**：PR #452 已合并为 `fd59f7a6`；交付 6 项代码修复、17 项新增回归测试与隔离 Baseline-1（18/18 检查通过，报告 `docs/reports/p6-s1-baseline-1-20261126.md`），相关历史套件失败集与 main 基线一致（22 个既有环境/历史失败，无本单引入回归）。
- **验收步骤**：问一件 10 月底至 11 月中旧事的结尾细节，确认能接上完整经过；让他按日期范围主动检索；P4 召回诊断中每轮仅一次 Embedding、判官失败能区分 embedding/model 原因。

## P6 路线

`P6-S0` (#449)、`P6-S2` (#450)、`P6-S1` (#452) 已合并；P6-S2 合并后加固由 #451 收尾。之后进入 `P6-S4`（¥20 硬上限消融实验）。P6 不改 M2 的 `input_token_ratio/tool_groups`、不改 M3 的缓存协议，也不削减瞄一眼/弹幕连续性。

## 正式底座

- P3-0 至 P3-4 已合并并完成正式维护部署；全局管理中心首批 `#448` 已合并。
- 正式运行目录固定跟随 `main`。P6-S2/P6-S1 均未向正式 Peter 发消息、未启停正式进程、未修改正式源码或真实记忆；所有写入与恢复演练均在独立 worktree 隔离副本完成。
