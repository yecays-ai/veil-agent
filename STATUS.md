# 当前状态

> 更新时间：2026-11-26T03:58:00Z（P6-S2 二轮审查修复完成）

## P6-S2 · 统一写入事务、防丢稿、证据链与协同恢复

- **当前目标**：让事件记忆在写入中断、经历整理失败和备份恢复后仍保持正文、故事、阶段、版本与用户原话证据一致，且可幂等恢复历史丢稿。
- **现状**：分支 `ticket/450-p6-s2-write-contract-and-recovery` 已完成 CodeRabbit 首轮 29 项与二轮全部 14 项审查修复并推送更新至 PR #450。二轮补齐：缺失 `sidecar_events` 兼容、`ensure_schema` DDL 与 `apply_digest` 单事务提交、`retry_pending_vectors` 同事务更新摘要、`record()` 生成故事前采集基线且 `locked` 不推进 `last_story_at`、`revise()` 无新标题时不覆盖面板标题、`retry_pending()` 首创投影同步写入 `quotes`、`facts()`/`_missing_facts()` 统一中文含年份日期与严格年份归属、`timeline()` 模型可见证据脱敏、`hold` 重写由单条 Brain 版本保存完整旧 Markdown、`scripts/p6_s2_recover_history.py` 末行完整性保护，以及 `ExperienceMemory` 常驻锁绑定与 `'locked'` 状态区分。
- **隔离验收**：P6-S2 专项、审查回归、事件、经历、Brain（writer/read/native_hold/backup/store/export）及 P3-4 等 15 个核心测试套件共 **291/291 全部通过**。
- **下一步**：等待 PR #450 最新 CI 与复审确认；不得自行合并 PR，也不得对正式 `data/peter` 执行恢复，必须另开维护窗口并由 Lan 确认 dry-run 预览。
- **验收步骤**：维护窗口创建协同 bundle；先 dry-run 核对 58 条目标消息和追加预览；确认后 apply，并在记忆详情/时间线检查 11 月 25 日经历连续且无重复；相同命令二次执行须返回 `already_recovered`。

## P6 路线

`P6-S0` 已由 `#449` 合并；本单 P6-S2 完成 PR 后，按定稿顺序进入 `P6-S1`（检索、卡片与元数据确定性修复），再进入 `P6-S4` 消融实验。P6 不改 M2 的 `input_token_ratio/tool_groups`、不改 M3 的缓存协议，也不削减瞄一眼/弹幕连续性。

## 正式底座

- P3-0 至 P3-4 已合并并完成正式维护部署；全局管理中心首批 `#448` 已合并。
- 正式运行目录固定跟随 `main`。本单未向正式 Peter 发消息、未启停正式进程、未修改正式源码或真实记忆；所有写入测试与历史恢复均在独立 worktree 隔离副本完成。
