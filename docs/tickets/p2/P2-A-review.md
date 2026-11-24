# P2-A 自审（2026-11-24，复查修正版）

对照 `docs/tickets/p2/P2-A.md` 逐条：

| 工单要求 | 状态 | 证据 |
|---|---|---|
| 事件 69 故事 / 69 阶段无损入库 | 完成 | `brain_events.py` + 测试；隔离库 stories=69/stages=69 |
| 事件未知字段 payload_json | 完成 | 测试保留未来字段并导出 |
| 事务写入 + 三态反馈 | 完成（存储层） | `brain_writer.py` 6 条测试；隔离检查 saved/saved_index_pending |
| 向量 pending/重试 | 完成 | 重试测试与隔离检查 retried=1 saved=1 |
| 硬删除只留最小 tombstone | **修复** | 原实现遗漏 `sidecar_events` 与 `events.memory_id`；本 PR 已补 |
| 可见性保持 hidden/locked/archived | 完成（存储层） | 状态测试；未接页面 |
| 所有写入入口接管 | **完成（隔离）** | hold/trace/erase/panel lock-edit-delete 走 authority；sidecar/versions/events/delete-requests 走 BrainStore；event_memory record 端到端通过 |
| 隔离 Peter 端到端 | **部分** | 真实 Memory/NativeMemoryTools 的 hold/trace/erase/panel/event 已验；未跑真实聊天循环与备份恢复 |
| 同 ID 导出 | 完成（导出物） | 回导 285 记忆 / 282 版本 / 172 sidecar / 0 problems |
| Ombre 实际启动回退 | 归 P2-B | 未做（P2-B rollback_check 已实际启动 Ombre） |
| authority/shadow 互斥 | 完成 | attach 抛错测试 + Memory 初始化校验 |
| 备份恢复 | 完成（组件） | `brain_backup.py` 备份/恢复测试，digest/integrity/count 一致 |

## 复查修正说明（2026-11-24）

- **硬删除缺口已修复**：原 `hard_delete` 未清除 `sidecar_events`（包含每次状态变更的原始 raw_json），
  也未清除 `events` 表中 `memory_id` 外键引用，可能保留敏感关联内容。本 PR 在 `brain_writer.py` 中：
  1. 追加 `DELETE FROM sidecar_events WHERE memory_id=?`
  2. 追加 `UPDATE events SET memory_id=NULL WHERE memory_id=?`（仅断开关联，保留事件故事）

- **原结论行已更正**：原结论"可以开 PR 供审，不自动合并、不进入 P2-B。下一步必须先接 Memory.call('hold'/'trace')
  与所有 sidecar/versions/events 直写，再跑隔离 Peter 端到端与完整写入矩阵"——
  该步骤已在 #434 中完成，但硬删除缺口在本 PR（fix/p2-review-corrections）中修复。

**P2-A 交付状态：写入/事件/导出/备份完整性已修复并通过测试；硬删除涵盖所有敏感路径（含 sidecar_events）。**
**正式聊天循环与 Ombre 回退验收属 P2-B 范畴，仍待 Lan 验收。**
