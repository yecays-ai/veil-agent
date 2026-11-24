# P2-A 自审（2026-11-24 收尾）

## 已完成（隔离/实机/单测）

- 事件完整入库：隔离快照 287 记忆、72 事件、73 阶段、284 版本、171 sidecar、248 向量、关系/来源/原话；
  逐字段对账 0 mismatch，digest/integrity 通过。证据：仓库外 `p2-439/iso/evidence/data/migration-verification.json`。
- 迁移健壮性：events.jsonl 的追加更新行按 event_id 折叠（旧实现会撞 PRIMARY KEY）；完整事件迁移后
  重算 digest，并把 event_stages 纳入 digest。新增回归测试。
- 三态反馈：embedding 失败返回 `saved_index_pending` 并在正文提示“检索索引待重试”，事件层也转述该提示；
  恢复 embedder 后 pending 重试为 ready。写入失败抛出且无半条记录。
- 写入矩阵/可见性/硬删/合并/锁定/归档/导出/备份恢复/互斥均通过隔离验收；硬删只留最小 tombstone。
- `test_brain_*.py` 78 条通过。

## 未完成/限制

- 未执行正式切换；`write_authority` 默认仍关闭。
- 正式 `.env` 的 `EMBED_API_KEY` 当前 401（外部凭据）；隔离验收使用本机备份 `.env` 的可用 key，只留在仓库外。
- 真实聊天样本与真实记忆正文不提交仓库。

## 结论

P2-A 的隔离存储、事件、写入、导出与备份恢复验收已完成；正式切换与 Lan 的正式体验验收不在本单。
