# P6-S6-1｜记忆动态评价、强化与保护性衰减

- **工单编号**：462（分支 `ticket/462-p6-s6-1-memory-reinforcement`，worktree `worktrees/462-p6-s6-1`）
- **基线提交**：`main = ead71b6`（PR #462 / 工单 461 已合并；S5 PR #461 合并为 `504c0a7`）
- **产品格**：A（陪伴核心：长期记忆、关系连续与检索）
- **做完后 Lan 能多做的一件事**：她不用记着"这条记忆当初标了几分"，Peter 会在真实送达后记录一条记忆最近有多活跃，并根据后来反复发生的事、共同经历、个人意义和关系变化，提出有证据、可回看、可撤回的重要度调整；她常聊的事会自然变得更靠前，久未提起的重要共同经历仍照常想起、且不会被自动降权或遗忘。
- **她怎么验收**：Brain → 记忆监控里直接改"记忆权重与保护性衰减"的开关和参数，聊天几轮后打开一条记忆，看"活跃度 / 送达次数 / 跨天会话 / 重评历史"，对建议点接受或撤回；全程不需要看代码或跑测试。

## 一、目标

把 S6-1 的三件事分别做实，并且**互不混淆**：

1. **当前活跃度（activation）**：只在实际送达后更新 `last_active` 与强化计数；并发、取消、失败、重试、重生成、重启下不产生虚假强化或重复计数。
2. **长期重要度（importance）**：与活跃度独立管理；依据跨会话重复、事件关联、个人意义与关系认知，生成**有证据、可追溯、版本化**的重评建议；尊重 Lan 的锁定与置顶，不自动降权重要共同经历。
3. **保护性衰减**：只对 `importance <= 6` 且未置顶的**活跃度分数**做温和衰减；`importance >= 7` 与 `pinned` 永不衰减；衰减不改重要度、不删除、不隐藏。

## 二、并行施工边界

### 与 S6-2（自主闲暇联想）的边界

- 本单**独占**：`veil/brain/memory_weights.py`（新文件）、`tests/test_p6_s6_memory_weights.py`、`desktop/tests/memory-weights.test.cjs`。
- 本单**主改**（S6-2 不应改同一函数）：
  - `veil/brain/storage/brain_writer.py`：新增三张派生表与硬删除清理；
  - `veil/brain/memory_agent.py`：`confirm_delivery()` 的送达记账钩子、`search_candidates()` 的检索设置合并（不碰联想候选池、判官、冷却逻辑）；
  - `veil/brain/brain_p4_api.py`：`library()` 的激活/重评投影、权重与建议 API；
  - `desktop/workspace-brain.js`：Brain 首页的权重面板与记忆详情里的活跃度/重评区域。
- 与 S6-2 **共享但只做最小衔接**：`veil/core/veil_agent.py`（仅在 `send()` 交付确认时传会话键与重生成标记）、`veil/brain/recall/brain_retrieval.py`（只在既有评分后追加可选信号，默认关闭）、`veil/brain/storage/brain_authority.py`（工具检索复用同一可选设置）、`STATUS.md` / `HANDOFF.md` / `docs/ACCEPTANCE.md`。
- 并行规则：S6-2 合流时保留本单的送达记账、权重面板与记忆详情区域；本单不修改 S6-2 的 `natural_association.py`、自主唤醒路径或提示词。若 S6-2 也要改 `confirm_delivery()` 或 `library()`，先在本 PR 评论中写明并 rebase，不覆盖已合并内容。

### 与 S6-3（认知巩固 / consolidation）的契约

`MemoryWeights.propose()` 是统一入口，S6-3 不需要绕开任何策略：

    weights.propose(memory_id, trigger='consolidation', proposed_importance=6,
                    confidence=0.82, reasons=[...], evidence=[...], auto_apply=False)

- 与本地证据走同一条 `_reevaluate()`：同一事务、同一唯一待审约束、同一版本写入；
- `pinned` / 锁定条目不自动应用；降权永远先进入待审；自动增量不超过 `reeval_max_auto_importance`；
- 返回 proposal（含 `proposal_id` / `status` / `version_seq`）供 S6-3 记录来源；
- S6-3 的批量巩固可在其自己的日预算与调度内调用，本单**不**启动夜间定时器，避免与 S6-3 抢后台调用和预算。

## 三、当前状态与复用调研

### 3.1 施工前确认

- S5（工单 460 / PR #461，`504c0a7`）与 S6 开工门禁（工单 461 / PR #462，`ead71b6`）均已合入 `main`；五项门禁关闭见 `docs/tickets/461-p5-s6-gates.md`。
- S5 延期的"隐式工作 trap 联合门控"仍开放，但它只影响联想是否被允许进入判官；本单的记账只读取 `finish_recall()` 最终注入的 ID，不依赖联想如何被选中，因此可以并行，且不把该延期项算作已关闭。
- #455 决策：`AB-R5a` 静态 importance 已最优；`AB-R5b` 的 `last_active` 近期加分只有 `+1/53` 且没有冷门重要样本；`AB-R5c` 情感加分否决；保护性衰减离线补测 `imp<=6` 时 Recall@8 `+1.12pp`，但无保护衰减会伤重要旧经历。结论：**活跃度排序默认仍可关闭**，本单先把真实送达信号做成可靠数据源。

### 3.2 仓库内复用

- 送达/回滚五层状态（`retrieved -> judged -> injected -> delivered -> effective_used`）与 `delivery_listeners` 钩子：工单 460 / PR #461 已交付，`confirm_delivery()` 已明确"只在真正送达后"的语义，本单直接接在它后面，不另造事件源。
- `versions` 单份版本、`apply_update_memory()` 原子写与 `hard_delete()`：P6-S2 底座（`veil/brain/storage/brain_writer.py`），本单只新增派生表与清理。
- 关系认知 `confirmed/uncertain/outdated` 与 `event:` / `memory:` 证据：P6-S3（工单 459）已交付；本单只读引用作为"关系变化"证据，不写关系认知。
- 弃用/保护字段盘点：`docs/reports/p6-2-relationship-weight-evolution-audit-20261126.md` 已证实 `last_active` 0 读取、`pinned` 未参与重评，正是本单入口。

### 3.3 成熟开源实现核对（不复制代码）

| 项目 | 可借鉴点 | 本单取舍 |
| --- | --- | --- |
| Ombre / Ombre-Brain（MIT） | 改进 Ebbinghaus 遗忘曲线、`last_active` 字段 | 只借"衰减是派生分数"的思路；VEIL 不采用情感 valence/arousal 通道（P6-S4 已否决），也不删除重要记忆 |
| Mem0（Apache-2.0） | ADD-only 写入 + 元数据过滤 + 混合检索 | 借"追加式事件账本"；不引入其向量库/云服务 |
| Zep / Graphiti（Apache-2.0） | 双时态边、版本化认知 | 借"每次重评留版本、可追溯"；本单用 `versions` + proposal 表，不引入图数据库 |
| Letta / MemGPT（Apache-2.0） | `sleep-time` 后台整理 | 借"把重评做成可审计的独立管线"；定时/批量留给 S6-3，本单不做夜间模型调用 |
| MemOS（Apache-2.0） | 激活态记忆与调度 | 只借"激活与长期重要度分离"的概念；不引入其推理引擎依赖 |

结论：没有可直接替换的成熟实现；本单在现有 `brain.db` 上做**零新依赖**的派生状态与版本化建议，符合 `reuse-first`。

## 四、实现

### 4.1 数据（brain.db，派生、不含正文副本）

- `memory_delivery_events`：`(event_key, memory_id, request_id, session_key, delivered_at, delivered_day, kind, counted, regenerated)`；`event_key = sha256(request_id|memory_id)`，主键 + `INSERT OR IGNORE` 保证一次投递最多记账一次。
- `memory_activation`：每条记忆一行；`delivery_count`、`distinct_days`、`distinct_sessions`、`first/last_delivery_at`、`activity_score`（衰减后的暴露计数）、`score_updated_at`。
- `memory_importance_proposals`：版本化建议；`status = pending / applied / rejected / reverted / superseded`，保存 `current/proposed_importance`、`confidence`、`trigger`、`policy_version`、`reasons_json`、`evidence_json`、`metrics_json`、`version_seq`；`status='pending'` 上的部分唯一索引保证一条记忆最多一个待审建议。
- 三张表是**派生状态**，不进入 `canonical_digest`（导入/校验只比对记忆内容）；但 `hard_delete()` 在同一事务里按 `memory_id` 删除它们，用户删除不留下行为痕迹。备份/恢复随 `brain.db` 一起走现有 S2 契约。

### 4.2 记账（只在实际送达后）

- `Chat.send()` 在 `delivered = sent_messages or reply_row_saved` 时调用 `Memory.confirm_delivery(request_id, session_key=..., regenerated=...)`；失败、取消、无回复走 `rollback_recall()`，不触发记账。
- `confirm_delivery()` 把最终注入的 `injected_ids` / `opening_ids` / `association_ids` / `carried_ids` 交给 `MemoryWeights.apply_delivery()`；同一 `(request_id, memory_id)` 重放返回 `duplicate`，不改计数。
- 重生成：仍记录"真实曝光"并推进 `last_active`，但 `counted=0`，不增加强化次数/活跃度分数。
- 记账在一个 `BEGIN IMMEDIATE` 事务内完成；SQLite 跨进程串行化，`INSERT OR IGNORE` 负责幂等。任何异常只写诊断、不抛回聊天。
- 只统计仍可见、未归档、未删除的记忆；不做跨请求内存共享。

### 4.3 保护性衰减（派生投影，不是写库衰减）

- `effective_score(score, updated_at, importance, pinned, half_life_days, now)`：`pinned` 或 `importance >= 7` 原样返回；其余按半衰期 `0.5 ^ (days/half_life)` 衰减；默认半衰期 30 天，可在界面改（7–365）。
- `last_active` 始终是"最后一次真实送达"的事实时间；衰减只影响用于展示/可选排序的 `activity_score` 投影，不改 `importance`、`bucket`、`is_deleted`、`is_tombstone`、`is_hidden`，也不会删除/归档/隐藏。
- 衰减投影在读取时计算（UI、API、可选排序），不因读操作写库；下一次实际送达时先把分数按半衰期折到当前，再加权，避免"忘记衰减"。

### 4.4 长期重要度重评（有证据、版本化、尊重锁定）

证据（每项都有具体引用，产生原因码与权重）：

| 证据 | 码 | 来源 |
| --- | --- | --- |
| 实际送达次数 | `delivered` | `memory_activation` + 最近 request id |
| 跨天 | `cross_day` | `distinct_days >= 2` |
| 跨会话 | `cross_session` | 事件表 `session_key` 去重 |
| 共同经历 | `event` | `events.memory_id` |
| 关系认知引用 | `relationship` | `learned-context.json` 的 `confirmed` 条目 `event:` / `memory:` 引用 |
| 个人意义 / 为什么记住 | `meaning` / `why` | `memories.meaning` / `why_remembered` |
| 长期层 / 首次经历 | `bucket` / `first_of_kind` | `memories.bucket` / `first_of_kind` |
| 新近性 | `new` | 创建 ≤ 14 天 |

策略：

- 至少要有一项"共同经历"证据（事件、关系引用，或跨天重复送达），才会提出调整；
- `importance <= 5` 需 `strength >= 0.45`，`= 6` 需 `>= 0.60`，`7..9` 需 `>= 0.80` 且至少三类证据；
- **自动**只允许提升，最多 +1，结果不超过 `reeval_max_auto_importance`（默认 7）；`pinned` / 锁定条目不自动应用；
- 降权、进入 8+ 的重评、以及锁定/置顶条目一律进入 `pending`，等 Lan 在记忆详情或"重评建议"里确认；
- 每次应用写一条普通 `versions`（`operation_reason = importance-reevaluation: ...`），可撤回；拒绝后默认 168 小时不再自动提同类建议；
- 每个 `(request_id, memory_id)` 只产生一次；同一记忆同一时刻最多一个待审建议；冷却默认 24 小时；应用后若证据没有新增，不再重复提。
- 版本策略标识 `policy_version = 's6-1.v1'` 随每条建议落盘。

### 4.5 模块协作与界面

- 检索：`brain_retrieval.load_settings()` 新增 `activation_ranking_enabled` / `activation_weight` / `activation_half_life_days` / `activation_decay_enabled`；默认 `False`。开关打开时只在既有向量+BM25+importance 分数之后追加 `activation_weight * score/(score+2)`，诊断里记录 `activation_ranking` / `activation_ranked`；没有送达记录时严格无操作（单元测试验证）。
- 判官、联想、召回预算：不改其选择逻辑；只消费 `finish_recall()` 最终注入 ID，避免预算丢弃/联想回滚产生误记账。
- 界面（复用现有 Brain 记忆页，不新建页面）：
  - 记忆监控首页新增"记忆权重与保护性衰减"面板：送达激活、保护性衰减、活跃度排序（实验）、动态重评、自动应用安全增量五个开关；半衰期、排序权重、重评冷却、自动上限四个参数；保存到 `data/<role>/memory-weights.json`，下一轮生效、重启保留；
  - 记忆档案库详情新增：最近活跃、活跃度百分比、送达次数、跨天天数/会话数、保护状态；"待你确认的重评"卡片（接受/拒绝）；"重评历史"（含自动/拒绝/撤回与证据原因）；"重新评估"按钮；
  - 库过滤新增"有待重评建议"，排序新增"活跃度"。

## 五、验证证据

- 单元/集成：`tests/test_p6_s6_memory_weights.py`（26 项）覆盖送达幂等、4 线程同请求、重启、重生成、取消/回滚、缺失/归档记忆、开关关闭、保护性衰减、锁定/置顶、降权待审、接受/撤回、硬删除清理、排序默认关闭与无操作、API/路由接线。
- 桌面：`desktop/tests/*.test.cjs` 62/62 通过（含新增 2 项）。
- 回归：完整 Python 套件与 `origin/main` 基线失败集合对比（见 `docs/reports/p6-s6-1-memory-reinforcement-20261128.md`）。
- VEIL Lab 真实链路：run `s6-1-activation-20261128T004338`，快照 `peter-20261127T170245 / r3`，代码 `code_sha256=44ddcdffcb71cab718a36ae52cccc4a566459d8e5f2d25254d21c808ca52a869`，mock、¥0；`run verify` 通过。合成记忆 `s61laba/b/c` 证明：送达后 `last_active`/计数/分数更新；重复确认与回滚不增加；重启保留；60 天后 `imp6` 分数 1.0→0.2441、`imp8` 保持 1.0；`s61laba` 5→6 自动应用并写版本；重要度、可见性、存在性不变。报告见 run 的 `reports/latest.md` 与本单报告。

## 六、验收步骤（Lan 在 Windows 上体验）

1. **面板可改可留**：Brain → 记忆监控 → "记忆权重与保护性衰减"。把"活跃度参与排序（实验）"打开、把半衰期改成 14，保存；关闭程序重开，值仍在；再关掉排序。面板上应明确写着"`importance>=7` 与置顶永不衰减、衰减不删除不隐藏"。
2. **常聊主题有活跃度**：正常聊几轮（或按报告用隔离实验副本），打开聊过的那条记忆：详情里有"最近活跃 / 活跃度 / 送达 N 次 / 跨 N 天 N 会话"；若它同时有事件、个人意义或关系引用且证据足够，会出现"待你确认的重评"或已自动 +1 的"重评历史"，能点"撤回这次重评"。
3. **重要旧经历不被遗忘**：找一条 `importance >= 7` 或置顶的记忆，隔几天再看：详情显示"保护: 重要或置顶，永不衰减"，重要度没有下降，仍能被检索/联想；低重要度记忆的"活跃度"会随时间下降，但记忆仍存在、可编辑、可检索。

隔离数据下另可运行 `veil-lab` run `s6-1-activation-20261128T004338` 的 `run verify` 与 `run chat`，避免用真实共同经历做首次试验。

## 七、剩余能力边界

- 活跃度排序默认关闭：`AB-R5b` 证据不足 + 本机尚无长期真实送达历史，不能声称排序净收益；本单只保证信号可靠、可开关、无记录时不动结果。
- 重评只在"送达后 + 手动/接口"触发，不做夜间定时批处理；多日灰度、跨会话批量巩固与花费由 S6-3 决定。
- 自动调整只覆盖"提升到 ≤7"；8+、降权、锁定/置顶条目都需 Lan 确认，因此本单不会自动降权任何重要共同经历。
- 衰减是展示/可选排序的派生分，不会写回 `importance`；不做"遗忘删除"。
- 投递事件默认保留 20000 条（可调 1000–200000），超出后按时间淘汰最旧事件；聚合计数不因淘汰回退；提案暂不自动清理，保留审计。
- `brain_backup` 的备份后新增记录合并只覆盖记忆内容表；派生激活/提案在恢复后从下一次真实送达重新积累，不伪造历史。
- 界面参数只对当前角色生效；S6-2 若同时改记忆监控页，合流时需保留本单面板与详情区域。
- 本单未改正式 `data/peter`、未启停正式 Peter、未部署。
