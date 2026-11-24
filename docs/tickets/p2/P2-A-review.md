# P2-A 自审（2026-11-24）

## 已完成（隔离/单测）

- 事件 69 故事 / 69 阶段入库；未知字段 payload_json；迁移幂等。
- 事务写入三态、版本、向量 pending 重试、合并、备份/恢复。
- 硬删除已补齐：清 sidecar_events、event_sources、共享事件来源计数、受影响事件/阶段；旧库缺表时
  不崩溃；只留最小 tombstone；SQLite 异常不再被吞掉。
- 同 ID 导出回导 0 problems；authority/shadow 互斥。
- 测试：`test_brain_*.py` 60 条通过；memory panel 8 条通过。

## 未完成/限制

- 未在仓库提交任何真实聊天、真实记忆正文；真实环境证据不落仓库。
- 未执行正式切换；`write_authority` 默认关闭。
- 未单独跑真实聊天循环；P2-B 才做读取/切换验收。

结论：P2-A 存储与写入在隔离环境通过；正式切换与 Lan 体验验收未做。
