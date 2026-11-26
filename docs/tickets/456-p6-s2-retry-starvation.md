# 456 · P6-S2 加固补充：retry_pending 锁定阶段批次公平性

- 产品线：B（长期记忆与共同经历连续性）；P6-S2 合并后加固（#451）的审查补充，独立小 PR，不动 P6-S1/P6-S4 代码。
- 做完 Lan 能多做的一件事：她锁定某条经历后，后台待提交阶段里即使积压了多条指向锁定记忆的任务，其他未锁定经历的中断阶段也能继续补提交，不会因为前 10 条都被锁挡住而永远卡在 pending。
- 验收：隔离环境新增回归先红后绿；P6-S2 专项与事件/Brain 相关套件全绿；PR CI 全绿且实质审查意见处理完；不写正式 `data/peter`、不启停正式 Peter。

## 目标

修复 #451 合并后 CodeRabbit 复审发现的实质问题：`EventMemory.retry_pending()` 在循环前先把 `chosen` 截取为前 `limit`（默认 10）条，再逐条提交；#451 起锁定目标会以 `ProjectionLocked` 拒绝提交并保留 pending。若最早的 10 条 pending 都指向已锁定记忆，每轮都只重试这 10 条且全部失败，第 11 条未锁定阶段永远没有机会恢复（批次饿死）。

修复：改为按顺序遍历全部 pending，提交前先检查目标记忆是否锁定；锁定的阶段直接跳过且**不占用** `limit` 批次名额（保持 pending，解锁后重试），未锁定的阶段继续恢复。

## 当前状态

- 基线：`origin/main` `beb5fa3`（P6-S2 #450、合并后加固 #451、P6-S1 #452 均已合并）。
- 改动文件：`event_memory.py`（`retry_pending()` 增加 `attempted` 计数与锁定目标预检跳过；锁定阶段不占批次名额）、`tests/test_p6_s2_hotfix_453.py`（新增批次公平性回归：10 条锁定 pending + 1 条未锁定，未锁定必须被恢复且锁定保持 pending）、`STATUS.md`、`HANDOFF.md`。
- 回归证据：新增测试在未修复代码上失败（`retry_pending()` 返回 0），修复后通过（返回 1，未锁定阶段 committed，10 条锁定阶段仍 pending）。
- 未删除或放宽任何既有测试；不改变 #451 的锁状态事务内复核、事务原子性、单版本与 digest 语义。

## 验收步骤

1. 在隔离环境跑 `python -m unittest tests.test_p6_s2_hotfix_453 -v`：7 条回归全部通过（新增批次公平性用例在未修复代码上为红）。
2. 跑 P6-S2 专项与事件/Brain 相关套件（`test_p6_s2_review_fixes`、`test_p6_s2_write_and_recovery`、`test_event_memory`、`test_event_continuation_421`、`test_experience_memory`、`test_brain_*`），确认无回归。
3. 查看 PR CI 全绿；不改正式 `data/peter`、不启停正式进程、不调用正式聊天。
