# P2-A 自审（2026-11-24，复查完成版）

对照 `docs/tickets/p2/P2-A.md` 逐条核查：

| 工单要求 | 状态 | 证据 |
|---|---|---|
| 事件 69 故事 / 69 阶段无损入库 | 完成 | `brain_events.py` + 测试；隔离库 stories=69/stages=69 |
| 事件未知字段 payload_json | 完成 | 测试保留未来字段并导出 |
| 事务写入 + 三态反馈 | 完成（存储层） | `brain_writer.py` 6 条测试；隔离检查 saved/saved_index_pending |
| 向量 pending/重试 | 完成 | 重试测试与隔离检查 retried=1 saved=1 |
| 硬删除只留最小 tombstone | **完成（已修复）** | `brain_writer.py` 补齐清除 `sidecar_events` 与 `events.memory_id` 关联映射；测试覆盖敏感信息不留存 |
| 可见性保持 hidden/locked/archived | 完成（存储层） | 状态测试通过 |
| 所有写入入口接管 | 完成（隔离） | hold/trace/erase/panel lock-edit-delete 走 authority；sidecar/versions/events/delete-requests 走 BrainStore |
| 隔离 Peter 端到端 | **完成** | `scripts/p2_isolated_peter_real_e2e.py` 在独立子进程、独立端口上使用真实 DeepSeek 模型运行 6 阶段完整流程（启动→防注入写入→Brain召回→事件引用去重→多轮追问→进程退出与全新服务重启恢复）全部通过，日志在 `docs/tickets/p2/evidence/isolated_peter_real_e2e.log` |
| 同 ID 导出 | 完成（导出物） | 回导 285 记忆 / 282 版本 / 172 sidecar / 0 problems |
| Ombre 实际启动回退 | 归 P2-B | `scripts/p2b_rollback_check.py` 实际启动 Ombre 验证通过 |
| authority/shadow 互斥 | 完成 | attach 抛错测试 + Memory 初始化校验 |
| 备份恢复 | 完成（组件） | `brain_backup.py` 备份/恢复测试，digest/integrity/count 一致 |

## 复查修改详情

1. **硬删除补齐敏感数据清理**：
   - 原 `hard_delete` 未清除 `sidecar_events` 中的原始操作 json（含有修改前后完整正文片段），也未断开 `events` 中的 `memory_id`。
   - 本次在 `brain_writer.py` 中增加了 `DELETE FROM sidecar_events WHERE memory_id=?` 和 `UPDATE events SET memory_id=NULL WHERE memory_id=?`，并在单元测试中增加了断言验证。
2. **端到端流程验证**：
   - 编写并执行了 `scripts/p2_isolated_peter_real_e2e.py`，使用独立子进程、独立端口与真实 DeepSeek 模型，完整覆盖服务启动、自然防注入与 Brain 独立写入、聊天召回验证、事件引用去重、多轮连续追问、完整服务退出与全新进程重启恢复 6 个场景，证明各模块在真实模型与真实协议下真正连通。
