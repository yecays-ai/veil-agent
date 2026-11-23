# 当前状态

## 工单 424 · 导入事件和它的旧碎片只算同一次回忆（本 PR，待 Lan 审核）

- 根因：`Memory._event_map()` 只映射事件自己的 `memory_id`，导入的 `source_memory_ids` 从来不进
  召回路径；`event-import-withdrawn.jsonl` 只是账本，运行时既不读它也不归档旧 md。于是同一个共同
  经历会以「完整事件 + 旧碎片」两份进请求。
- 改法：事件账本里的 `source_memory_ids` 也映射到该事件（多归属用集合判断）；旧碎片被召回时给事件
  完整故事；没有归属的旧记忆、没有事件账本的角色行为不变。映射按 `events.jsonl` mtime 失效。
- 不删除、不归档、不改写任何旧记忆；不改进口器、召回条数／预算／冷却／判官。
- 隔离库 67 个有来源事件：修前 39 个同时注入旧碎片，修后 0；只召回碎片时注入完整故事。

## 第五步 · 69 篇正式迁移（按工单暂停，等本 PR 合并）

- 2026-11-23 已完成：只读审计（69 篇、156 个来源位置、146 个唯一旧 ID、224 个旧 md、`[memory.events]`
  在 main 上已为 `true`）；完整备份 3852 个文件 / 100,696,910 字节（2 个运行期锁文件当时被进程锁住、恢复时重建）并做精确恢复演练（逐文件哈希一致）；
  隔离副本预演（69 篇 `create`、0 conflict、`--check-index` matched 69 / missing 0 / stale 0 /
  ready true）。
- 卡点：预演发现 39/67 个事件会与旧碎片同时注入请求，工单要求此时停止正式迁移。修复见本 PR。
- 本轮没有写正式库、没有停 VEIL、没有动 69 篇定稿、聊天记录、关系底色和备份。

## 已验证

- 工单 424：`tests/test_event_source_recall_424.py` 6 条（在 main 上 4 条失败）；同一组 17 个模块
  713 条在 main 与修复后都通过；`test_event_import`、全部 `test_recall_*`、`test_relationship_422`、
  `test_chat`、`test_autonomy`、`test_learned_context*`、`test_experience_memory`、`test_memory_363`
  均在其中。
- 工单 422（已合并，PR #423 / `8504250`）：关系底色与自然联想的 62 条隔离测试、全部 `test_recall_*`
  234 条、桌面守卫 21 条通过；真实模型抽查 0 次。

## 下一步

- 本 PR：等 CI 与 Lan 审核；合并后我用新 main 重跑第五步 C（隔离复演）→ D（真实模型自然聊天验收）
  → E（正式迁移 + 独立索引检查 + 重启），再交报告、证据和恢复命令。
