# 工单 469 · P7-S1 生产经验 Provider、证据新鲜度与协同恢复

- **产品格子**：A 陪伴核心。
- **做完后 Lan 能多做的一件事**：以后开启 P7 后，Peter 能从自己的已确认经历中安全取得与当前情境有关的经验，同时不读到其他角色或已失效、已否决的经验。
- **Lan 怎么验收**：部署后的即时体验由 P7-S5 统一造数回放；本阶段先在隔离假数据中核对默认关闭、角色隔离、证据失效即停、故障降级及备份恢复。

## 目标

按已批准并合并的 P7-0 规划 §7.2 完成 P7-S1，不提前施工 S2 的语义匹配、冲突裁决或多入口策略。

## 当前状态

- 基线：`main = c7f23ed`（工单 467 · PR #468 已合并，CI 与安全检查通过；文档单不涉及部署或用户体验验收）。
- 分支 / worktree：`ticket/469-p7-s1-experience-provider` / `worktrees/469-p7-s1`。
- 直接复用：`CognitiveConsolidator.query_experience_candidates()`、永久否决账本、S7 `AUXILIARY` 槽位、S5 `context_signal`、同轮查询向量、现有 `brain_backup.py` 与角色目录；不建立平行记忆系统。
- 复用规划调研：Nocturne 只借 Conditional Disclosure 思路，不引入新依赖；本单无需重复外部调研。
- 独占文件：`veil/brain/experience_provider.py`、`veil/brain/cognitive_consolidation.py`、`veil/core/veil_agent.py`、`veil/brain/storage/brain_backup.py`、`veil/core/config_registry.py`、`veil/server/web_agent.py`、`tests/test_p7_s1_experience_provider.py`、`tests/test_chat.py` 及本单状态/验收文档。

## 验收步骤

1. 在隔离数据中证明 P7 默认关闭时真实 Chat 请求为 0 Token、0 候选查询，provider 异常不阻断回复。
2. 开启后用 Peter/Ghost 假数据证明只注入本角色 `confirmed` 经验；逐类删除或改写 `memory/event/chat/autonomy/tool/long_task` 证据后立即停止注入。
3. 并发写配置与备份、恢复旧备份，证明 bundle generation 一致、永久否决不丢失、不复活、恢复后无悬空注入。
