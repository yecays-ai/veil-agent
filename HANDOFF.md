# HANDOFF

## P2 收尾（2026-11-24，复查修正版）

- 三单均已合并：P2-A `0f6028d`、P2-C `affea8c`、P2-B `e4306ad`；默认不改变正式 Peter。
- P2-B 已完成隔离 Ombre 回退（canary 新增/修改可见、删除不复活）与同库 A/B；正式切换未做。
- P2-C 只在隔离副本执行清理；正式库未清理。
- 复查修正（fix/p2-review-corrections）：hard_delete 补清 sidecar_events/events.memory_id；
  Brain 写入封堵自由 tags；review.md 结论与 acceptance.md 已更正。
- **下一步（待 Lan 授权）**：
  1. P2-B 三项关键条件（人工真值黄金集 30 条、事件去重真实聊天验收、切换协议端到端演练）补完后，
     Lan 验收；授权后按 `docs/tickets/p2/P2-B-runbook.md` 执行切换/回退；之后 P3。
  2. 标签正式清理（`tag_governance.py --write`）需 Lan 授权后执行。
  3. 145 条旧记忆核实清单需 P2-B 读取切换完成后，Lan 授权逐条确认。

## P2-B 待补项（阻塞 Lan 验收）

- **黄金问题集**：30 条人工真值问题，含无答案情形，验证相关召回/漏召回/错误召回。
  不能以旧 Ombre 注入结果（`memory_refs`）充当正确答案。
- **事件/碎片不重复注入**：在隔离 Peter 中实际聊天，确认同一经历不以事件和碎片两种形式同时出现。
- **切换协议端到端演练**：在隔离环境实际执行备份→冻结→对账→锁→切换→验证；故意制造差异验证会停止。

## P1 试运行状态（已合并 #433）

- `characters/peter/character.toml` 的 `backend = "brain"` 已打开（P1 阶段）。
- 写入权威仍是 Ombre，影子同步继续跑；Brain 只读召回。
- 仅 2 轮真实召回（2547/3172 ms，0 失败），旧候选日志仅 17 条；不能作为质量基线。
- 回退：改 `backend = "ombre"` 并重启 VEIL，不动 `brain.db` 与 Ombre 数据。

## 工单 433 交接（P1 试运行：Brain 召回；2026-11-24）

- 工单：`docs/tickets/433-p1-brain-trial.md`；分支 `ticket/431-p1-brain-backend`，从 `main` `f2f68c7` 开。
- 只改 `characters/peter/character.toml` 的 `[memory.retrieval] backend = "brain"`；写入权威仍是
  Ombre，影子同步继续跑。
- 回退：改回 `backend = "ombre"` 并重启 VEIL，不动 `brain.db` 与 Ombre 数据。
- 下一步：真实使用观察漏召回/错召回/延迟/费用；收尾后开发 P2 读写接管。

## 工单 425 交接（经历页 GET 路由；已合并 #425）

- 根因：`do_GET` 只转发 catalog/read，未转发 events/event；点开「记忆·经历」页一直 404。
- 改法：`web_agent.py` GET 分支把 `/api/memory/events` 和 `/api/memory/event` 一起转给 `_get_memory`。
- 验证：73 条 test_web 测试通过（新增 1 条路由+鉴权+404 回归）。

## 工单 424 交接（事件/碎片不重复注入；已合并 #424）

- 根因：事件故事与对应旧碎片曾同时注入（实测 39/67 个事件存在重复），改后 0 重复。
- 改法：`memory_agent.py` 的 `_event_ledger()` + `unique`/`owners`/`duplicate()`/`resolved()` 逻辑。
- 共享来源不再任取第一篇故事，无故事事件不被折叠，坏账本不崩召回。
- Lan 对三条 V36 决定（`f8c23ac29f8d`→转归 V09、`c13e3ebd3a84`→独立保留、`2fd52c0b984b`→dont_surface）
  的记录在 `tmp/step5/` 目录（.gitignore，不入库）。

## 工单 429 / 430 交接（P0-B 影子同步 / P1 检索；已合并）

- P0-B（`b1e51f1`）：`brain_sync.py` 影子同步已合并；正式 `data/peter/brain.db` 在实际切换前由
  `python brain_sync.py init` 初始化。
- P1（`f2f68c7`）：`brain_retrieval.py`（向量 + jieba/BM25）已合并；`backend = "brain"` 已在 P1 打开。
