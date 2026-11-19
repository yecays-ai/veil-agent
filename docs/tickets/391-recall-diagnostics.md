# 391 召回逐候选诊断

属于 A（陪伴核心）。做完 Lan 能多做的一件事：她用不同说法问一件旧事、Peter 没想起来时，她能打开「实时上下文」看到这一轮记忆是在哪一步丢的——Ombre 没返回、被归档/隐藏过滤、判官没选、去重/冷却，还是长度裁掉；不用再靠猜。

## 目标

每一轮普通聊天留下一条只读诊断：候选记忆的 id、在哪一步被排除、判官看了多少条选了几条、最终注入了哪些。**不改变任何召回结果、判官判断和记忆内容。**

## 当前状态

- 自动想起现在会在会话内存里记 `last_recall_skipped` 计数，但只给测试看，不落盘、不上页面。
- `context-log.jsonl` 只记最终 `memory_ids`，没有候选与排除原因；判官只记数量和耗时。
- Ombre 返回不带逐条分数，VEIL 不掌控它的排序；判官只读标题/标签/关键词卡片。
- 复用：现有 `Memory.context` 的各阶段、`RecallJudge.choose`、`context-log` 的 request_id 关联、上下文页的轮次列表。不新增模型调用、不改召回与判官、不碰记忆文件。

## 验收步骤

1. 更新并重开 Peter。用一句记忆里明确有的旧事、换一种说法问他；点开「实时上下文」里对应那一轮：多一行「记忆：Ombre N 条；不可见…；判官…；注入 N 条」。
2. 点「候选明细（默认收起）」：能看到 Ombre 返回的 id、不可见的 id 和原因、判官候选/选中、去重/冷却、最终注入；不展开时不显示任何记忆 id。
3. 闲聊一句（记忆里没有相关事）：显示「判官看过，没有直接相关」或 Ombre 0 条，而不是空白。
4. 打开 `data/peter/recall-diagnostics.jsonl`：只有 id、原因、计数和这句的长度/哈希，没有聊天原文或记忆正文。

## 影响-行动矩阵

| 用户场景 | 代码证据 | 当前行为 | 预期行为 | 行动与验证 |
| --- | --- | --- | --- | --- |
| 换说法问旧事没想起 | `Memory.context` → `last_recall_debug` | 只有内存计数 | 记录 Ombre 候选与每一步排除 | `tests/test_recall_diagnostics.py` |
| 归档/隐藏/来源条目 | `Memory.visibility_reason()` | 静默丢弃 | 标出 archived/tombstone/dont_surface/deleted/source/hidden | `test_an_archived_hit_is_explained_instead_of_silently_dropped` |
| 判官选空 | `RecallJudge.last_choice` | 只记数量 | 记候选 id、选中 id、empty/failed/skipped | `tests/test_recall_judge.py` 新增断言 |
| 去重/冷却 | `Memory.context` 的 kept 循环 | 只累加计数 | 记录被丢 id 和原因 | `test_...`（现有召回测试覆盖路径） |
| 预算/长度裁掉记忆 | `Chat._turn` 的 pop 循环 | 只撤销冷却 | 诊断里单列 budget_dropped | 代码路径 + 现有召回测试 |
| 页面显示 | `web_agent.live_context` + `desktop/chat.js` | 只有记忆条数 | 一行摘要 + 默认收起的候选明细 | desktop UI 测试 + 手工页面 |
| 隐私 | 诊断文件 | 无 | 只存 id/原因/长度/哈希，不存正文 | `test_the_file_has_ids_and_reasons_but_no_query_or_memory_text` |
| 滚动保留 | `save_recall_debug()` | 无 | 最多 200 行 | `test_the_diagnostics_file_is_rolling` |
| 关闭/回滚 | 无新增调用与配置 | 无 | 删掉 `recall-diagnostics.jsonl` 即恢复空白，不影响召回 | 手工删除验证 |

## 接入检查（新能力）

- 入口：普通聊天每轮；自主活动、瞄一眼、Codex 提醒不记录（没有 `Memory.context` 的普通路径或没有 UI 用途）。
- 停止与恢复：无新流程；诊断写入失败只丢这一条，不影响聊天。
- 权限：只读；不新增工具、不授予权限、不改召回与判官。
- 费用：不新增模型调用；每轮多几 KB 本地文件写入，保留 200 行。
- 数据：新文件 `data/<角色>/recall-diagnostics.jsonl`；只有记忆 id、原因码、计数、查询长度/哈希；不含聊天或记忆正文。
- 状态可见：上下文页每轮摘要，展开看 id。
- 升级回滚：删除该文件即清空；无迁移、无配置。
- 真实环境：Windows 本机；文件损坏时页面按无诊断显示。
