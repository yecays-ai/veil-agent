# 当前状态

## 工单 426 · 四个 Ombre 功能的数据迁移（本 PR，待 Lan 审核）

- 现状：迁移脚本 `simplify_memory.py` 与 7 个用例已完成，正式库**尚未执行**；脚本默认 dry-run，
  `--apply` 前先做完整备份，并要求 VEIL 与 Ombre 都不在运行。
- 下一步：本 PR 合并后，先在正式库跑 dry-run 对账（预期 4 封信、5 条计划、7 条 feel、3 条 I），
  再跑 `--apply --skip-plan ef34e23f2dc5`（跳过已被 growth 收下的花园桌游规则）。
- 验收：`docs/tickets/426-memory-simplify.md` 第 4 步——生命记录出现 5 条已完成计划、
  `workspace/lab/letters/` 有 4 封信、记忆页不再有 feel/I；原信件与原计划文件仍在。

## 工单 425 · 经历页的两个 GET 路由没有接上（本 PR，待 Lan 审核）

- `/api/memory/events`、`/api/memory/event` 从 #407 起就没被 `do_GET` 转发，桌面「记忆 · 经历」页
  列表和详情一直 404；事件层默认关闭时没人用，第五步迁移后才发现。
- 改法：`web_agent.py` 的 GET 分支把这两个路径一起转给 `_get_memory`，其余不动。`tests/test_web.py`
  新增一条路由/鉴权/404 回归。

## 第五步 · 69 篇正式迁移（已完成，等 Lan 体验）

- 正式库已迁入 69 篇（定稿副本 `tmp/step5/final-prepared.json`），独立索引检查 matched 69、
  missing 0、stale 0、ready true；备份在 `tmp/step5/backup-preapply-*`。
- 正式迁移按工单停在「旧碎片重复注入」那关由 #424 修完并通过复演；本 PR 是冒烟时发现的页面路由缺陷。
- 下一步：本 PR 合并并重启后，Lan 按 `docs/tickets/425-event-timeline-endpoints.md` 验收经历页；
  自然聊天体验按第五步报告里的观察项继续。

## 工单 424 · 导入事件和它的旧碎片只算同一次回忆（已合并，PR #424 / `d8eacc2`）

- 一个旧 memory ID 只被一篇事件认领时：旧碎片不再和完整事件同时注入，只召回碎片时给定稿故事。
- 被两篇事件共享时：保留碎片原文、不借任何一篇故事；两篇分别召回也不串故事。有故事的事件不能折叠
  没故事的共享阶段；模型后来改写的故事不替换碎片；坏 `source_memory_ids` 和临时读盘失败都不会弄坏召回。
- 产品代码只改 `memory_agent.py`；不删、不归档、不改写旧记忆，不新增模型调用。
- 卡点：第五步 69 篇正式迁移继续暂停——预演发现旧碎片会与完整事件重复注入，本 PR 复演通过后才继续。
- 下一步：等 CI 与复审；合并后我用新 main 重跑第五步 C（隔离复演）→ D（真实模型自然聊天）→ E（正式迁移）。
