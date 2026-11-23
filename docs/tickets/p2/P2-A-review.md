# P2-A 自审（2026-11-24 02:10，施工中）

对照 `docs/tickets/p2/P2-A.md` 逐条：

| 工单要求 | 状态 | 证据 |
|---|---|---|
| 事件 69 故事 / 69 阶段无损入库 | 完成 | `brain_events.py` + 测试；隔离库 stories=69/stages=69 |
| 事件未知字段 payload_json | 完成 | 测试保留未来字段并导出 |
| 事务写入 + 三态反馈 | 完成（存储层） | `brain_writer.py` 6 条测试；隔离检查 saved/saved_index_pending |
| 向量 pending/重试 | 完成 | 重试测试与隔离检查 retried=1 saved=1 |
| 硬删除只留最小 tombstone | 完成 | 测试断言 tombstone 不含正文 |
| 可见性保持 hidden/locked/archived | 完成（存储层） | 状态测试；未接页面 |
| 所有写入入口接管 | **部分** | hold/trace 已接；sidecar/versions 经 BrainStore 已接；删除请求/事件 JSONL/面板直写未接 |
| 隔离 Peter 端到端 | **部分** | 真实 Memory 实例 hold 已验；未跑完整聊天/删除/事件流程 |
| 同 ID 导出 | 完成（导出物） | 回导 285 记忆 / 282 版本 / 172 sidecar / 0 problems |
| Ombre 实际启动回退 | 归 P2-B | 未做 |
| authority/shadow 互斥 | 完成 | attach 抛错测试 + Memory 初始化校验 |
| 备份恢复 | 未完成 | 仅导出回导，未做完整备份恢复 |
| 自审/PR | **未完成** | 本文件即自审；未开 PR |

结论：P2-A **未完成，不得合并、不得进入 P2-B**。下一步必须先接 `Memory.call('hold'/'trace')`
与所有 sidecar/versions/events 直写，再跑隔离 Peter 端到端与完整写入矩阵。
