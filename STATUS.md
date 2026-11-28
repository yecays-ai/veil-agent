# 当前状态

> 更新：2026-11-28 +08:00 · `main = c8bc8bb`（工单 464 / PR #465 · S6-3 已合并）· **工单 465 / PR #466（P6-S7 上下文纯化、缓存优化与跨阶段接口收口）**在 `ticket/465-p6-s7-context`，实现与 VEIL Lab 完整生产快照隔离验证已完成，**待 CI、独立审核与 Lan 体验；未合并、未部署**。
> **待你验收的事项与唯一验收状态统一在 [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md)。** 本文只写进度，不另存验收状态。

## 一句话现状

P3、P4/P5 与 P6-S0～S6-3 均已合入 `main`；最新是 **工单 464 / PR #465 · S6-3，合并提交 `c8bc8bb`，默认关闭、未部署、待验收**。**工单 465 / P6-S7（本单）**落地 #455 已通过的 AB-C1/AB-C2a：稳定能力规则与动态能力状态分层、后续工具请求省略空记忆说明、P7 AUXILIARY 标准槽位，以及请求快照/P5 健康探针/缓存统计收口；不实施被否决的 AB-C2b/AB-C3，不启用 P7 行为。

## 近期工单真实状态

工单编号与 PR 编号不是一回事，下表按实际 PR 号记录。

| 工单 | PR | 合并提交 | 合并时间（UTC） | 部署 | 验收入口（以 ACCEPTANCE 为准） |
| --- | --- | --- | --- | --- | --- |
| 465 · P6-S7 上下文纯化、缓存优化与跨阶段接口收口 | #466 | — | — | 未部署 | [§3 B-14](docs/ACCEPTANCE.md#b-14-p6-s7-上下文纯化缓存优化与-p7-插槽工单-465) |
| 464 · P6-S6-3 后台认知巩固与经验提炼 | #465 | `c8bc8bb` | 2026-11-28 00:58 UTC | 未部署 | [§3 B-13](docs/ACCEPTANCE.md#b-13-p6-s6-3-后台认知巩固与经验提炼工单-464) |
| 462 · P6-S6-1 记忆动态评价、强化与保护性衰减 | #464 | `90846bd` | 2026-11-27 23:15 UTC | 未部署 | [§3 B-12](docs/ACCEPTANCE.md#b-12-p6-s6-1-记忆动态评价强化与保护性衰减工单-462--pr-464) |
| 463 · P6-S6-2 自主闲暇自然联想 | #463 | `81dba35` | 2026-11-27 17:05 UTC | 未部署 | [§3 B-11](docs/ACCEPTANCE.md#b-11-自主闲暇自然联想p6-s6-2--工单-463) |
| 461 · P5 健康巡检补齐 + S6 开工门禁 | #462 | `ead71b6` | 2026-11-27 15:38 UTC | 未部署 | [§3 B-9 / B-10](docs/ACCEPTANCE.md#b-9-p5-健康巡检补齐工单-461) |
| 460 · P6-S5 检索、判官与情境相关自然联想升级 | #461 | `504c0a7` | 2026-11-27 12:59 UTC | 未部署 | [§3 B-8](docs/ACCEPTANCE.md#b-8-检索判官与情境相关自然联想升级工单-460--pr-461) |
| 459 · P6-S3 关系底座解耦、证据链晋升与认知治理 | #460 | `f979ca2` | 2026-11-27 11:33 UTC | 未部署 | [§3 B-7](docs/ACCEPTANCE.md#b-7-关系底座四区独立预算证据链晋升与认知治理工单-459) |
| P4/P5 记忆中心返工与复审修复 | #459 | `2b03006` | 2026-11-27 06:33 UTC | 未确认部署 | [§1 A-9](docs/ACCEPTANCE.md#a-9-p4p5-记忆中心返工与诊断加固pr-459) |
| 458 · 仓库目录结构重整（`veil/` 源码包与离线回滚） | #458 | `2a264a7` | 2026-11-27 05:40 UTC | 未部署 | [§8 G-4](docs/ACCEPTANCE.md#g-4-仓库目录结构重整工单-458) |
| 457 · 文档、状态与待验收清单归集 | #457 | `d78a8b7` | 2026-11-27 00:00 UTC | 文档类 | — |
| 451 · P4/P5 管理中心、全局工作区、日常聊天 | #456 | `95dc26a` | 2026-11-26 22:35 UTC | 已部署 | [§1 A-1 ~ A-8](docs/ACCEPTANCE.md#1-桌面与全局工作区工单-451--pr-456) |
| 454 · P6-S4 消融实验与决策 | #455 | `a1f2255` | 2026-11-26 14:18 UTC | 已部署 | [§4 C-1 ~ C-2](docs/ACCEPTANCE.md#4-p6-s4-消融实验的结论工单-454--pr-455) |
| 456 · P6-S2 批次公平性补丁 | #454 | `588701c` | 2026-11-26 10:18 UTC | 已部署 | [§3 B-2](docs/ACCEPTANCE.md#b-2-统一写入事务与防丢稿工单-450--pr-450加固-工单-453--pr-451公平性-工单-456--pr-454) |
| 455 · P6-S1 换模型待索引与诊断 | #453 | `b3d14c2` | 2026-11-26 09:43 UTC | 已部署 | [§3 B-1 / B-4](docs/ACCEPTANCE.md#b-1-检索卡片与元数据确定性工单-452--pr-452加固-工单-455--pr-453) |
| 452 · P6-S1 检索确定性 | #452 | `fd59f7a` | 2026-11-26 07:43 UTC | 已部署 | [§3 B-1](docs/ACCEPTANCE.md#b-1-检索卡片与元数据确定性工单-452--pr-452加固-工单-455--pr-453) |
| 453 · P6-S2 锁状态与恢复时间戳 | #451 | `beb5fa3` | 2026-11-26 09:12 UTC | 已部署 | [§3 B-2](docs/ACCEPTANCE.md#b-2-统一写入事务与防丢稿工单-450--pr-450加固-工单-453--pr-451公平性-工单-456--pr-454) |
| 450 · P6-S2 写入事务、证据链、协同恢复 | #450 | `774b4bd` | 2026-11-26 04:38 UTC | 已部署 | [§3 B-2 / B-3](docs/ACCEPTANCE.md#b-2-统一写入事务与防丢稿工单-450--pr-450加固-工单-453--pr-451公平性-工单-456--pr-454) |
| 449 · P6-S0 基线同步 | #449 | `56abe3f` | 2026-11-25 21:16 UTC | 已部署 | 文档类，随 #456 一并覆盖 |
| 448 · 只读模型配置总览 | #448 | `2408bb6` | 2026-11-25 17:55 UTC | 已部署 | [§2 A-9](docs/ACCEPTANCE.md#a-9-模型配置总览工单-448--pr-448) |
| 446 · 全局配置审计 | #446 | `0a688ad` | 2026-11-25 15:19 UTC | 已部署 | [§2 A-10](docs/ACCEPTANCE.md#a-10-全量配置清单工单-446--pr-446) |
| P3-4 · 维护清理与卸载 | #445 / #447 | `6efd35c` / `160b0ec` | 2026-11-25 UTC | 已部署 | [§8 G-3](docs/ACCEPTANCE.md#g-3-p3-4-清理后复验) |

更早的 P3-0 ~ P3-3（#441–#444）、P2 系列（#434–#440）都已合并部署，验收状态见 `docs/ACCEPTANCE.md` §10 与 §13。

## 当前开发阶段

- 产品阶段：A 陪伴核心（记忆注入与精修）施工中；B 的权限底座已具备；C1 手机、V 语音未进入正式施工。
- 工程阶段：P6-S0～S6-3 已合入 `main`；**S7（本单，工单 465 / PR #466）实现与隔离验证完成，待 CI / 审核**。S5 延期的一条隐式工作 `trap` 联合门控仍未关闭；P7 行为层尚未开工。
- 正式底座：`main`，本机正式目录跟随 `main`，用 `update-main.cmd` 一键更新；本单未启停正式 Peter、未写正式记忆、未部署。

## 本单（工单 465 · P6-S7 上下文纯化、缓存优化与跨阶段接口收口）

- **AB-C1**：`system.facts` 不再包含会随工具展开变化的能力描述；复用现有能力快照作为 `capability_state` 运行态块，每个工具步骤按实际 schema 更新。VEIL Lab 同结构对比稳定前缀 19,850 → 15,256 chars，请求消息+工具结构 85,426 → 81,789 chars。
- **AB-C2a**：第一次请求保留空记忆边界；同轮 `step > 0` 只省略精确匹配的空召回/开场说明，真实记忆、完整事件故事与聊天历史不动。
- **P7 标准槽位**：`p7.experience_hints` 位于 AUXILIARY 的短期便签后、撤回候选前，独立 600 tokens / 4 条预算，只接收 confirmed；S7 默认不安装 provider，0 Token，不实现正式行为触发。
- **诊断与维护**：请求快照新增两类标题、模块字符数、稳定前缀/工具 schema 哈希和请求结构字节；上下文诊断显示缓存 tokens、空提示省略与 P7 状态；P5 H-8 汇总实际发送、缓存报告与预算行为。
- **隔离验证**：VEIL Lab `peter-20261127T170245/r4` run `p6-s7-r4-20261128-20261128T091129`（mock，¥0）最终 `run verify` 通过；正式数据无变化。真实模型缓存命中、费用和延迟未追加付费验证。
- 报告：`docs/reports/p6-s7-context-purification-20261128.md`；验收入口：`docs/ACCEPTANCE.md` B-14。

## 更早（工单 460 · PR #461 · P6-S5）已合并

- 默认配置维持 `stopwords_enabled=False`、`candidate_k=8`、S5 情境判官启用、`association_enabled=True`、`association_contextual_enabled=False`。
- VEIL Lab 正式只读快照 `peter-20261127T170245/r2` + 日用模型 `gemini-4-hydrogen` 三路盲评（3 组 9 回复，¥1.10，`all_checks_ok=true`）：第 1 组平局、第 2 组情境版胜、第 3 组弱偏好。
- 在「VEIL Brain → 记忆监控」补齐自然联想总开关与情境相关联想开关，经 `/api/brain/layer-toggle` 持久化，下一轮生效、重启保留。
- 与后续工单 461/462 共享 `desktop/workspace-brain.js`、`veil/brain/brain_p4_api.py`、`veil/core/veil_agent.py` 与三份状态文档；S5 已合流并完成联合回归。

## 上一单（工单 461 · PR #462 · P5 健康巡检 + S6 开工门禁）已合并

- 合并提交 `ead71b6`（2026-11-27 15:38 UTC），**未部署、没有 Lan 验收记录**；验收见 `docs/ACCEPTANCE.md` B-9 / B-10。

### P5 健康巡检

- H-3 改为“悬空引用 vs 真正无关联记录”：sidecar 链接、合并指针、relations 外键为悬空；无事件/来源/引述/sidecar 关联/关系链接的记忆与无记忆/来源/阶段/证据的事件为“无关联”。逐条显示对象、类别、判断依据，只报告，不删除、不合并。
- H-7 删除 1024 维硬编码：读取 `meta.embedding_model` 与角色配置的 Embedding 模型身份，按库内 `vectors.dim` 分布、`length(vector)` 与索引状态逐条对账；同一次扫描的采样余弦也使用记录维度。
- 新增指定日期段的 `period`：记忆/事件/阶段/版本/认知版本增长，期间重复、碎片、悬空引用、索引异常；每个检查带 `scope`，明确区分全库当前与期间变化。
- 历史状态只依据既有 `health-snapshots.jsonl`；无快照写 `no_snapshot_in_range`，不虚构历史。导出/复制报告包含期间段落与数据范围。
- 接入现有 Brain 健康页（期间卡片、范围标签、判断依据）、定期巡检调度与增长报告。

### S6 开工门禁

- 聊天历史索引：增量解析、400 条有界文本缓存、旧证据按 ID 按需查询；删除/截断/替换/同长度改写使缓存失效；重复与召回消息不作为证据。
- 证据核验：`event_ledger` / `quotes` / `sources` 有界分页，超出窗口标记 `unavailable` 而非“证据不存在”；一次核验共享墙钟预算（默认 1200ms），SQLite 争用超限保守降级，不误晋升、不阻断聊天。
- 反证：按规范化文本去重。不超过 120 条时整批送审（含无共同文字的跨语言否决）。超过 120 条时只分批核验触及该认知或证据的反证；无关积累不停止其他晋升；中间批次通过不晋升。更正索引在淘汰后回扫日志。
- 跨语言否决：无共同文字锚点的翻译候选经受预算约束的语义核验；真实模型实验中翻译候选 `contradicted` 不注入，无关正例 `supported` 并注入；Lan 显式恢复旧认知有确定性测试。
- 幂等：facts/growth 追加与修订、relationship 追加与修订、ongoing 新增/更新/完成/重开使用持久化回执；丢响应、并发重试、进程重启后同操作只落一次，返回原结果与原 ID，不额外写版本；Lan 删除/完成后旧回执不再伪装成功。

## 上一单（工单 462 · PR #464 · P6-S6-1）已合并

- 合并提交 `90846bd`（2026-11-27 23:15 UTC），**未部署、没有 Lan 验收记录**；验收见 `docs/ACCEPTANCE.md` B-12。
- **送达记账（只在实际送达后）**：`veil/brain/memory_weights.py` 新增 `memory_delivery_events`（`request_id|memory_id` 幂等键）与 `memory_activation` 聚合；`Memory.confirm_delivery(request_id, session_key, regenerated)` 在 S5 确认送达后调用；失败/取消/回滚不写；重生成只更新最近活跃、不增加强化计数。
- **保护性衰减与版本化重要度重评**：`effective_score()` 只对 `importance<=6` 且未置顶的活跃度投影按半衰期衰减；`memory_importance_proposals` 经统一接口 `MemoryWeights.propose()` 记录原因码、证据与版本，支持撤回；活跃度排序默认关闭。

## 本单（工单 464 · P6-S6-3 后台认知巩固与经验提炼）做了什么

- **后台巩固引擎**：新增 `veil/brain/cognitive_consolidation.py` 与系统提示词 `veil/prompts/defaults/memory.consolidation.prompt.md`（`memory.consolidation.prompt`），采用近因+价值均衡增量选批、关联历史锚点、UTF-8 字节自适应裁剪、批次指纹去重与跨日状态持久化，默认关闭（`enabled=False`）。
- **认知演进与 S3 严格治理**：零平行认知存储，全部新认知与冲突修订经 S3 `LearnedContext.submit_candidate()` / `reverify_relationship_evidence()` 与 `RelationshipSemanticWorker` 落库；幻觉引用与重复总结拦截、S6-2 不洁自主输出过滤、纯自主无用户原话降级 `uncertain`（防循环自证）、锁定与用户主权硬保护、冲突修订保留 `outdated` + `superseded_by` 历史版本。
- **跨模块协作与 P7 接口**：接通 S6-1 `MemoryWeights.propose()`（含去重与拒绝尊重，默认不自动应用）、读取 S6-2 审计后 `autonomy.jsonl`，巩固结果经 `LearnedContext` 进入后续聊天与自主活动上下文；预留 `p7.experience.v1` 结构化情境经验接口与确认/否决治理（不注入 `HEAD`）。
- **调度、预算、恢复与界面**：独立 `PeriodicBackgroundTick`、共享 `Budget` 类目 `cognitive_consolidation`（默认 `¥0.05/日`、24h/2次日上限，精确费率结算并在 `/cost` 单列）、`in_flight` 崩溃恢复与指数退避、脱敏运行日志；复用 Brain 首页/「关系与成长」页与费用管理页及 Electron `main.cjs` 路由白名单，配置持久化。
- **验证**：新增 `tests/test_p6_s6_3_cognitive_consolidation.py`（14 项端到端与复查回归场景）与 `desktop/tests/cognitive-consolidation.test.cjs`（6 项）；定向 15 个 Python 测试模块 **269/269** 通过，桌面 **71/71** 通过；VEIL Lab 完整生产快照 `peter-20261127T170245/r3` 隔离 run `p6-s6-3-r3-20261128T084025`（mock，¥0，零触碰正式数据）完成跨日全链路演练且 `veil-lab run verify` 通过；报告见 `docs/reports/p6-s6-3-cognitive-consolidation-20261128.md`。

## S6 开工门禁与 S6 子阶段状态

| 工程事项 | 状态 | 证据 |
| --- | --- | --- |
| 聊天历史增量索引、有界缓存、旧证据按需查询 | **已关闭** | `tests/test_p6_s6_chat_evidence_budget.py`；5 万条基准 0.256s 首建 / 0.435ms 重复 / 0.824ms 追加 |
| 实时证据统一墙钟预算、分页与安全降级 | **已关闭** | 同上（真实 SQLite 排他锁 + 旧 200 行窗口外证据 + 截断降级） |
| 追加重试持久化幂等 | **已关闭** | `tests/test_p6_s6_idempotent_writes.py`（丢响应/重启/4 线程并发/状态守卫） |
| 反证长期积累有界、不丢相关否决 | **已关闭** | 超限按相关性分批；无关积累不阻断其他晋升；相关否决不丢 |
| 任意跨语言同义否决与无关正例 | **已关闭（能力边界内）** | 单测 + DeepSeek 合成实验 `docs/reports/461-cross-language-live-20261127.md` |
| 情境自然联想隐式 `trap` 穿透 + 同轮判官联合门控 | **未关闭**（S5 延期项，不在本单范围） | 见 `docs/tickets/460-p6-s5-retrieval-judge-association.md`；仍待专项关闭 |
| S6-1 送达激活、保护性衰减与版本化重评（工单 462 / PR #464） | 已合并 `90846bd`，未部署、待验收 | 见 `docs/ACCEPTANCE.md` B-12；报告 `docs/reports/p6-s6-1-memory-reinforcement-20261128.md` |
| S6-2 自主闲暇联想（工单 463 / PR #463） | 已合并 `81dba35`，默认关闭；未部署、待验收 | 见 `docs/ACCEPTANCE.md` B-11 |
| S6-3 后台认知巩固与经验提炼（工单 464 / PR #465） | 已合并 `c8bc8bb`，默认关闭；未部署、待验收 | 269/269 Python + 71/71 桌面；VEIL Lab run `p6-s6-3-r3-20261128T084025`；报告 `docs/reports/p6-s6-3-cognitive-consolidation-20261128.md` |
| S7 上下文纯化与接口收口（工单 465 / PR #466） | **实现与隔离验证完成，待 CI / 审核** | Python 2,548 pass / 29 skip / 3 个主线同现非回归；桌面 71/71；VEIL Lab run `p6-s7-r4-20261128-20261128T091129`；报告 `docs/reports/p6-s7-context-purification-20261128.md` |

## 等 lan 定的事

1. 体验 `docs/ACCEPTANCE.md` 中 B-8 ~ B-14 与既有 A/B 项；B-14 是 S7 的稳定前缀、空提示省略与诊断可见性。
2. P7 经验影响行为层的开工节奏，以及是否先关闭 S5 延期的隐式工作 `trap` 联合门控。
3. B-3/B-4/H-1：三项**待授权**操作是否在下一个维护窗口执行。
4. 本单 PR 审核/合并/部署顺序；本单不自行合并或部署。

## 下一步

- 本单（工单 465）：推送分支创建 PR，等待独立审核 + CI；通过后停止，不自行合并或部署。
- 合并并部署后：按 `docs/ACCEPTANCE.md` B-13/B-14 实测多日巩固质量、上下文连续性与真实缓存 usage，再视体感决定 P7 行为接入节奏；另行专项关闭 S5 延期的隐式工作 `trap` 联合门控。

## 验收入口

[docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) —— 按模块分组，每项都有复选框和操作步骤。
