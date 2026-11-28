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

## 审查修复（PR #470 · 2026-11-28）

按 PR #470 的 18 条审查意见在 S1 范围内集中修复，不提前施工 S2 语义匹配：

- **否决与并发**：状态读取/修改/保存统一在 `mutex + brain-write.lock` 边界；保存时与磁盘状态合并治理字段；Provider 返回前与 Chat 渲染前两次同步复核；Profile 否决账本不可读时 fail-closed。
- **证据有效性**：形成期逐引用内容指纹、`delete_requests` 缓存失效、事件需有效关联记忆或来源、自主/工具日志撤回标记、`partial_stale` 有效核心规则、临时不可用不降级、长任务 512KB + 150ms 预算、缓存封顶。
- **备份恢复**：旧备份不撤销更晚的显式恢复决定；恢复 P7 配置 revision 单调递增；恢复期临时不可用不降级。
- **内容安全**：经验按不可信数据区块呈现，权限/审批/预算/参数校验仍由程序独立执行。
- **审查收口**：HANDOFF 只链接 STATUS；200 条候选池硬截断登记为 P7-S2 `P7-MATCH-02`；两条 SQL 扫描告警核实为固定 SQL 片段 + `?` 参数绑定的误报，已改为纯字面量参数化查询，不引入 ORM、不重构数据库层。

验收步骤与结果见 `docs/reports/p7-s1-experience-provider-acceptance-20261128.md`。
