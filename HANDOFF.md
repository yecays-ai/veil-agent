# HANDOFF

> 更新时间：2026-11-26T09:35:00Z

## 当前停点

- **P6-S2**：`#450` 合并 `774b4bd`；合并后加固 `#451` 合并 `beb5fa3`（锁状态事务内复核、恢复时间戳严格化、retry_dirty 锁定路径）。CI 全绿，338/338 定向测试通过。另有复审补充 PR #456（分支 `ticket/456-p6-s2-retry-starvation`）：`retry_pending()` 锁定阶段跳过且不占批次名额，避免 10 条锁定 pending 饿死后续未锁定阶段；新增第 7 条回归先红后绿，定向 363/363，待 CI 与实质审查通过后合并。正式数据未动，维护窗口恢复仍需 bundle + dry-run + Lan 确认。
- **P6-S1**：`#452` 合并 `fd59f7a`。合并后独立审查发现换模型待索引重复付费/不可恢复与诊断 API 未透出两个阻断项；加固分支 `ticket/455-p6-s1-postmerge-hardening`（worktree `E:\Peter-Agent\VEIL\worktrees\455-p6-s1-postmerge`）已修复并补 7 项回归 + Baseline-1 20/20，待提交 PR/CI/审查/合并。
- **P6-S4**：`ticket/454-p6-s4-ablations`（worktree `E:\Peter-Agent\VEIL\worktrees\454-p6-s4-ablations`）进行中：recall-lab harness、eval-v2（73 条 AI 辅助标注）、Tier 0/1/2 实验与预算台账；达到可合并状态后停止，不合并、不启动 S3/S5/S6/S7。

## 下一个窗口第一步

1. 合并 P6-S2 锁定批次公平性补充 PR #456（CI 全绿、实质意见处理完后）。
2. 若 P6-S1 加固 PR 未合并：读最新 CI 与实质审查意见，修完合并。
3. 若已合并：同步 `main`，让 P6-S4 分支 rebase 到含 #451/#455/#456 的最新 main，再完成 S4 报告/PR 与 CI。
4. 不执行正式 `data/peter` 历史恢复；不启停正式 Peter。

## 维护窗口红线

- 正式恢复必须先创建协同 bundle，再 dry-run 给 Lan 看目标与追加预览；只有 Lan 明确确认后才执行 apply。
- 代码回退不得用旧 DB 覆盖新增记忆；恢复必须走 `restore_bundle`，并检查 0 重复、0 悬空引用。
- 换 Embedding 模型必须使用 `scripts/embedding_reindex.py` 显式重建（先 dry-run、再 `--apply`），不得依赖后台重试自动改模型。
