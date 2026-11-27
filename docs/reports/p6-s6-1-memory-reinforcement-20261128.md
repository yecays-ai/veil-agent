# P6-S6-1 记忆动态评价、强化与保护性衰减 · 施工与隔离验收报告

> 工单 462 · 分支 `ticket/462-p6-s6-1-memory-reinforcement` · 基线 `main = 81dba35`（已合流 S6-2 / PR #463）
> 数据边界：本报告不含真实聊天、真实记忆正文、密钥或本机绝对路径中的用户数据；实验使用本地只读快照的隔离副本与合成记忆。**未合并、未部署、未启停正式 Peter。**

## 1. 结论摘要

- 送达记账做成了**派生状态 + 幂等事件账本**：只在 `Chat.send()` 确认回复已写入并显示后更新；同一 `(request_id, memory_id)` 重放不重复计数；失败、取消、回滚、重启、重生成都不产生虚假强化。
- 长期重要度与活跃度**分开存储**；重评建议带原因码、权重、具体证据引用和策略版本，应用时写单份 `versions`，可撤回；锁定/置顶条目不自动调整，降权永远先进入待审，因此本单不会自动降权重要共同经历。
- 保护性衰减只对 `importance <= 6` 且未置顶的**活跃度投影**生效；`importance >= 7` 与 `pinned` 永不衰减；衰减不改重要度、不删除、不隐藏。
- 活跃度排序是**可选信号**，默认关闭。理由：#455 的 `AB-R5b` 只有 `+1/53` 且无冷门重要样本，证据不足；本单只保证信号可靠，等真实多日送达数据积累后再决定。
- 隔离真实链路（VEIL Lab，mock，¥0）已验证：送达后更新、重复确认幂等、回滚不增加、重启保留、60 天后低保真衰减而高重要度不动、5→6 自动重评并写版本。
- 完整 Python 套件失败集合与 `origin/main` 基线一致（见第 8 节）；新增 47 项 Python 测试与 3 项桌面测试，合流 S6-2 后桌面全套 64/64。

## 2. 范围、前置与并行边界

### 2.1 前置确认

| 前置 | 结论 | 证据 |
| --- | --- | --- |
| S5 送达契约（工单 460 / PR #461） | 已合并 `504c0a7` | 五层状态与 `delivery_listeners` 在 `veil/brain/memory_agent.py` |
| S6 五项开工门禁（工单 461 / PR #462） | 已合并 `ead71b6` | `docs/tickets/461-p5-s6-gates.md` |
| S6-2 自主闲暇联想（工单 463 / PR #463） | 已合并 `81dba35`；默认关闭、未部署 | `docs/tickets/463-p6-s6-2-leisure-association.md`；与本单共享的 `veil_agent._finalize_recall_delivery`、`confirm_delivery`、`brain_retrieval`、`brain_p4_api`、`workspace-brain.js` 已合流；合流后 84 项定向通过、Lab 重验通过 |
| S5 延期项"隐式工作 trap 联合门控" | **仍未关闭**，不在本单范围 | 本单只消费最终注入 ID，不改变联想/判官门控 |
| #455 实验决策 | 已落盘并合并 | `docs/reports/p6-s4-decision-table-20261126.md` |

### 2.2 与 S6-2 / S6-3 的边界

- S6-2（自主闲暇联想）：本单不改 `natural_association.py`、自主唤醒、自主态提示词；共享文件只做最小衔接（`send()` 交付参数、`library()` 投影、记忆监控页）。S6-2 合流时保留本单的送达记账、权重面板与记忆详情区域。
- S6-3（认知巩固）：统一入口 `MemoryWeights.propose(...)`；同事务、同唯一待审约束、同版本写入、同样尊重锁定与自动上限。本单不启动夜间定时器，不与其抢预算。

## 3. 设计与不变量

### 3.1 三层分离

| 层 | 存什么 | 何时写 | 不做什么 |
| --- | --- | --- | --- |
| 活跃度 | `last_active`、`delivery_count`、`distinct_days`、`distinct_sessions`、`activity_score` | 仅 `confirm_delivery()` 后 | 不改重要度、不删记忆、不跨请求共享 |
| 活跃度衰减 | 读取时的派生投影 `effective_score()` | 不写库 | `imp>=7`/`pinned` 永不衰减；不删除/隐藏 |
| 长期重要度 | `memory_importance_proposals` + `versions` | 送达后自动评估 / 手动 / S6-3 接口 | 不自动降权；锁定/置顶不自动改；不覆盖正文 |

### 3.2 幂等键与竞态

- 事件键：`sha256(request_id + '|' + memory_id)`，主键；明细在 `memory_delivery_events`，不参与淘汰的 `memory_delivery_receipts` 负责长期幂等。
- 事务：`BEGIN IMMEDIATE`；时间戳在拿到写锁后捕获；聚合/事件/摘要刷新同一事务；`distinct_sessions` 与 `distinct_days` 是持久聚合，不从可裁剪明细重算。
- 重生成：`regenerated=True` 时记录曝光（`counted=0`）并推进 `last_active`，但不增加强化计数/分数；首次曝光也会留下零计数的激活行，UI 能显示最近活跃。
- 失败/取消/回滚：不调用 `confirm_delivery()`，不写任何派生行；`rollback_recall()` 只回滚冷却，不触碰记账。自主唤醒只有内部回复、没有真正发给 Lan 时 `activation=False`，S5 冷却照旧但 S6-1 不记账。
- 重启/瞬时锁：`pending_deliveries` 只在内存；已确认但写库失败的送达进入有界 JSON 重试队列，启动/成功送达时重放，receipt 防重复；队列保留原 `delivered_at`、写回前重读以免覆盖新条目、停用期间不丢弃；`reeval_pending` + `activation_seq` 让崩溃后的重评在启动时补做，且只清除本次实际评估的那一代标记。
- 最终裁剪：请求因长度最终去掉的记忆 ID 会从本轮待确认交付中移除，不计数；跨天天数用持久 `last_delivered_day` 判断，不依赖可裁剪明细。
- 恢复合并：墓碑删除派生行；同名提案把当前库的接受/拒绝/撤回决定同步回恢复库，避免恢复出已拒绝的待审建议。
- 提案：待审状态上的部分唯一索引 `memory_proposals_pending`；冷却按最近一次提案时间；拒绝冷却从拒绝时刻算；应用后若证据未增加不再重复提；重评在后台线程完成，不占用发送锁。

### 3.3 保护守卫

- `is_protected(importance, pinned) = pinned or importance >= 7`。
- 自动应用条件：方向为提升、固定 +1、结果 ≤ 7（配置只能在 5–7 调低）、置信度 ≥ `reeval_min_confidence`、非 `pinned`、非 `locked`，外部建议还必须有已验证的共同经历证据。
- 降权、进入 8+、锁定/置顶：只生成 `pending`，由 Lan 决定。
- 任何应用都只写 `importance` 字段，不写正文；`apply_update_memory()` 同时写 `versions` 与 frontmatter；应用/撤回/送达都在同一事务刷新 `meta.digest`。
- 外部（S6-3）建议必须有存活且**属于该记忆**的引用（event 必须挂在该记忆上，relationship 必须引用该记忆/事件，memory 必须是自身或已有 relation，delivery 必须是该记忆的 receipt）；没有已验证证据直接拒绝，降权/锁定/置顶永远待审，全局自动开关可再收紧。
- `hard_delete()` 与恢复合并按 `memory_id` 删除四张派生表与该记忆的行。

## 4. 实现位置

| 文件 | 改动 |
| --- | --- |
| `veil/brain/memory_weights.py`（新） | 设置校验/持久化、送达记账、衰减投影、证据评估、提案/决策/撤回、统一 `propose()` 接口 |
| `veil/brain/storage/brain_writer.py` | 四张派生表 DDL、索引、待审唯一约束、`reeval_pending` 迁移；硬删除清理 |
| `veil/brain/storage/brain_backup.py` | 恢复合并按墓碑删除派生行，并为保留记忆同步派生状态 |
| `veil/brain/memory_agent.py` | 构造 `weights`；`confirm_delivery(request_id, session_key, regenerated, activation)` 接送达；预算最终裁剪同步移除待确认 ID；`search_candidates()` 合并可选排序设置；生命周期诊断写入 `effective_used/activity` |
| `veil/core/veil_agent.py` | `_finalize_recall_delivery()` 交付确认时传会话键、重生成标记与用户可见标志；异常向后兼容 |
| `veil/brain/recall/brain_retrieval.py` | 可选 `activation_ranking\(_enabled\)` 排序项与诊断；默认关闭 |
| `veil/brain/storage/brain_authority.py` | 工具检索复用同一组 UI 设置 |
| `veil/brain/brain_p4_api.py` | 记忆库投影 `activation/reeval`、活跃度排序、权重状态/设置、提案列表/决策、手动重评 |
| `veil/server/web_agent.py` | `GET/POST /api/brain/memory-weights`、`GET /memory-proposals`、`POST /memory-proposal`、`POST /memory-reevaluate` |
| `desktop/workspace-brain.js` | 记忆监控权重面板、记忆详情活跃度/待审/历史/撤回、过滤与排序 |
| `tests/test_p6_s6_memory_weights.py`（新） | 47 项 |
| `desktop/tests/memory-weights.test.cjs`（新） | 3 项（含实际渲染记忆详情） |

## 5. 单元与集成证据

    python -m unittest tests.test_p6_s6_memory_weights -v          # 47 项通过
    python -m unittest tests.test_p6_s5_retrieval_judge_association \
      tests.test_brain_retrieval tests.test_brain_writer tests.test_brain_store \
      tests.test_p4_p5_brain_routes tests.test_p6_s6_chat_evidence_budget \
      tests.test_p6_s6_idempotent_writes tests.test_p3_1_brain_only     # 98 项通过
    node --test desktop/tests/*.test.cjs                            # 64/64 通过

覆盖的关键场景：

| 场景 | 断言 |
| --- | --- |
| 未发送/仅注入 | 不写激活；`finish_recall` 后仍为空 |
| 回滚 | `rollback_recall` 成功但不增加计数 |
| 同请求重复确认 | 第二次 `duplicate`，计数不变 |
| 4 线程并发同一请求 | 最终 `delivery_count == 1` |
| 进程重启后重复确认 | 仍 `duplicate` |
| 重生成 | 只更新 `last_active`，计数与分数不变 |
| 跨天/跨会话 | `distinct_days/distinct_sessions` 正确 |
| 缺失/归档记忆 | 忽略，不产生行 |
| 开关关闭 | 停止新写入，历史保留 |
| `imp=6` 60 天 | 分数 1.0 → 约 0.25 |
| `imp=8` / `pinned` 60 天 | 分数不变，重要度不变，记录仍存在 |
| 自动重评 5→6 | 有事件/关系/意义证据时自动 +1，写 `versions` |
| `pinned`/`locked` | 只生成 `pending`，不改重要度 |
| 降权 | 只 `pending`；接受后才改，可撤回 |
| 硬删除 | 三张派生表该记忆行清零 |
| 排序 | 默认关；无送达记录时开关前后结果完全一致；有送达记录时只启用的一路加分 |

## 6. VEIL Lab 真实运行链路

- Run：`s6-1-activation-20261128T004338`（`veil-lab/runs/s6-1-activation-20261128T004338`，含回滚/重启/冷却长链）与最终干净 run `s6-1-final-20261128T023357`（`veil-lab/runs/s6-1-final-20261128T023357`，最终代码）
- 快照：`peter-20261127T170245 / r3`（2026-11-28 00:42 刷新，正式目录无变化）
- 代码：branch worktree 的 `veil/` 复制到 run 内 `code/`，`run.json.code_root` 指向它；合流 S6-2 前 `code_sha256 = 44ddcdff...`，合流 `main = 81dba35` 后为 `8488cfb2...`，最终干净 run 为 `b59a57bb5dcc4c00a208eb87cbb99c4a40631967dd37ef7345339644c120452e`；四次 `run verify` 均通过。
- 模式：mock（不调用模型、不花钱）；实验变量：`memory.recall_judge.enabled=false`、离线检索 `weight_vector=0.0 / weight_keyword=1.0`（让合成的 `S61LAB` 记忆可被检索；不影响送达/记账逻辑）
- 合成数据（仅实验副本）：记忆 `s61laba`（imp5，事件+意义+为什么）、`s61labb`（imp8）、`s61labc`（imp5，无事件/意义）；事件 `s61lab-ev-a`；关系条目 `s61lab-rel`（`confirmed`，引用 `event:s61lab-ev-a`）
- 代码版本漂移说明：快照钉住的代码是 `2a264a7`，早于 S3；本分支的 S3 证据校验不再注入快照里那条无来源的 `uncertain` 关系条目，导致首次 `run verify` 缺少 `relationship_base`。为让核验对照成立，在实验副本里额外加了一条 `source='lan'`、`locked=true` 的合成关系条目（`s61-lab-rel`），之后 `run verify` 通过。这是快照代码与当前分支的差异，不是本单引入的运行回归；详见第 7 节。

### 6.1 实际观察到（脱敏，无正文）

    run verify: 通过（唯一提醒为 Lan 人设文件为空等既有项）
    activation rows: 13（含生产快照里的真实记忆 10 条 + 合成 3 条，均只记录 ID/计数）
    s61laba: deliveries=1, days=1, sessions=1, score=1.0, display=33, importance 5→6
    s61labb: deliveries=1, days=1, sessions=1, score=1.0, protected=true, importance 8
    s61labc: deliveries=1, days=1, sessions=1, score=1.0, importance 5
    duplicate apply: status=duplicate, counted_ids=[]
    rollback (finish_recall 后未 confirm): 返回 true，计数不变
    60 天后: s61laba/c score=0.2441 display=11 decayed=true; s61labb score=1.0 protected=true
    重要度: 6 / 8 / 5；is_deleted=0、is_tombstone=0；versions(s61laba)=1
    proposal: applied, 5→6, confidence 0.66, trigger delivery, auto_applied=true, version_seq=287
    重启（新进程 open_session）: 计数/分数/提案/设置全部保留

    合流 S6-2 后重跑（code_sha256=8488cfb2...，verify 通过）:
    再次送达（冷却 3 轮后）: s61laba/b/c delivery_count 1→2；score a/c 1.244（衰减后 0.244+1）、
                             b 2.0（imp8 保护，不衰减）；重要度仍 6/8/5；当日无重复提案（冷却生效）
    新增真实记忆激活: activation 行 13→28，均只记 ID/计数，未读取或输出正文

    最终干净 run s6-1-final-20261128T023357（code_sha256=b59a57bb...，verify 通过）:
    digest_valid=true；duplicate 重放 status=duplicate、计数不变；proposal applied 5→6（version 287）；
    60 天后 s61fa/c score=0.2437 display=11 decayed=true，s61fb score=1.0 protected=true；
    重要度 6/8/5，is_deleted=0/is_tombstone=0，versions(s61fa)=1，receipts=9

### 6.2 真实链路验证命令（可复现）

    cd veil-lab        # 从工作区根目录进入实验环境
    ..\veil-agent\.venv\Scripts\python.exe -m veil_lab run verify s6-1-activation-20261128T004338
    ..\veil-agent\.venv\Scripts\python.exe -m veil_lab run chat s6-1-activation-20261128T004338 --message "S61LAB ..."

（实验脚本在 `veil-lab/.tmp/s61_*.py`，只留本地，不提交。）

## 7. 与 #455 基线对照与排序决定

#455 的相关结论（`docs/reports/p6-s4-decision-table-20261126.md` `S6` 第 7 条、`p6-s4-ablations-20261126.md` `AB-R5b`）：

- 静态 `importance` 加分 `+0.05` 已是最优；
- `last_active` 近期加分：近 7 天只有 `+1/53` 命中，离线库缺少 >30 天冷门重要样本，**无法证明保底**；
- 保护性衰减离线补测：无衰减 `0.5444`、朴素衰减 `0.5278`、仅 `imp<=6` 衰减 `0.5556`；重要旧经历 `3/9 → 2/9`（朴素）/ `4/9`（保护）。

本单的处理：

1. **不把 `AB-R5b` 直接上线**：`activation_ranking_enabled` 默认 `False`，UI 里标"实验"；没有送达记录时开关严格无操作（单测断言前后顺序一致）。
2. **先让数据源可靠**：真实送达才更新；事件账本幂等；并发/重启/回滚都有测试；界面能看送达次数与跨天/会话。
3. **用已有 importance 通道承接"常聊主题自然延续"**：有证据的重复经历会把 5→6→7（自动上限 7），继续走现有的 `importance +0.05` 与长尾池，不依赖未证明的活跃度排序。
4. **重要旧经历保底**：`imp>=7`/`pinned` 不衰减、不自动降权、不删除；降权需 Lan 确认。
5. **净收益何时能评估**：等真实多日送达事件积累（本单之后），用同一 `activation_ranking` 开关在隔离副本做 on/off 对照；当前**不声称**排序净收益为正。

## 8. 完整回归

- 本分支完整套件（已合流 S6-2）：`python -m unittest discover -s tests -v`，**2560 项，18 失败 + 3 错误 + 29 跳过**。
- `origin/main = 81dba35` 同命令基线：**2513 项，18 失败 + 3 错误 + 29 跳过**。
- 两边失败/错误名称集合 **21/21 完全一致**（`comm` 双向差集为空）；多出的 47 项全部来自本单新增测试且通过。

| 项 | 本分支（合流后） | origin/main = 81dba35 基线 |
| --- | --- | --- |
| 测试数 / 失败 / 错误 / 跳过 | 2560 / 18 / 3 / 29 | 2513 / 18 / 3 / 29 |
| 失败/错误名称集合 | 21 个（maintenance / OpenCode / 备份 / APPDATA 桌面数据类） | 21 个，同名同序 |
| 记忆/检索/Brain 相关失败 | 0 | 0 |
| 新增测试 | +47（全部通过） | — |

已知既有失败类别（与 #461 记录一致）：Maintenance CRLF、OpenCode 未安装、APPDATA desktop-data、备份组件清单。本单未改这些模块。

## 9. 剩余能力边界与未验证项

- 活跃度排序净收益未证明（#455 证据不足 + 无长期真实送达历史）；保持可选、默认关。
- 重评触发器只有"送达后"和"手动/API"；没有夜间批量、没有跨会话模型巩固（S6-3）。
- 自动提升上限为 7；8+、降权、锁定/置顶均需 Lan 确认。
- 衰减只作用于活跃度投影，不写 `importance`；没有"遗忘删除"。
- 投递明细有界（默认 20000 条，可调），淘汰只删最旧明细；幂等 receipt 与 `distinct_days/distinct_sessions` 聚合不随明细淘汰，旧请求重放不会重复计数；receipt 长期增长（约每条送达一行），需按实际节奏再评估上限。
- 重评提案为保留审计暂不自动清理；`reeval_pending` 标记最多由启动时前 5 条补做，剩余等后续送达；后台线程不保证进程被杀时立即完成。
- 瞬时存储失败进入最多 100 条的本机重试队列；队列本身写失败（磁盘满/目录只读）或超过 100 条时只记录容量诊断（含丢弃条数/请求 ID），该次激活可能丢失但不产生虚假强化；不静默当作已记账。
- 已知窗口：回复已经显示、`confirm_delivery()` 尚未执行时进程被杀，这轮不更新激活；这是“宁可不记也不虚记”的取舍，要零丢失需发送前持久化 outbox，留给后续工单。
- `brain_backup` 的“备份后新增记录合并”对保留的记忆同步当前库的激活汇总/receipts/提案决定，使 `last_active`、重要度与撤销历史一致；墓碑对应的派生行删除，不伪造历史。
- 重评提案的拒绝冷却从拒绝时刻计算；接受时若记忆已被手动改动，待审建议会标记 `superseded`（需要重新评估），不会静默覆盖用户改动。
- 自主闲暇（S6-2）只有真正发给 Lan 的消息才计入本单活跃度；内部回复不计数。
- 实验脚本与合成数据只在 veil-lab 本地；未提交、未外发。
- 未验证/未覆盖：真实主聊天模型下 judge 与 S6-1 的联合行为（本单不改 judge）；S6-2 合流后的记忆监控页布局；多日真实日常下的重评节奏；Windows 真机 UI 手工体验（由 Lan 验收）。

## 10. 交接

- 分支/worktree：`ticket/462-p6-s6-1-memory-reinforcement` / `worktrees/462-p6-s6-1`
- PR：#464（待审核、CI、体验）；不自行合并、不部署。
- 状态文档：`STATUS.md` / `HANDOFF.md` / `docs/ACCEPTANCE.md`（B-12）。

## 11. 实际使用场景复核（做完后的效果）

按工单要求，交付前不只核对字段/接口/单测，而是逐条问“这个效果在真实使用里成立吗”。结论如下，未达到的部分明确写出。

| 目标效果 | 现有实现的真实表现 | 是否已达到 | 未达到/待验证 |
| --- | --- | --- | --- |
| Peter 能区分一段记忆有多重要、最近多活跃 | 记忆详情显示重要度、最近活跃、活跃度、送达/跨天/会话；活跃度只来自真实送达，不会因打开页面变化 | **是（界面与数据链路）** | 真实多日聊天下的数值节奏未观察；UI 真机点击待 Lan |
| 根据新的共同经历重新评价价值 | 有事件/关系/意义证据时自动 5→6（lab 实测，写 versions）；降权/8+/锁定/置顶进待审；可撤回 | **是（隔离真实链路）** | 真实日常中多久触发一次、Lan 是否愿意接受建议未验证 |
| 常聊的事情自然延续 | 重复送达+跨天/事件/意义把重要度提升到 7；继续走现有 importance 加成与长尾池；可选活跃度排序默认关 | **部分** | 排序净收益未证明（#455 AB-R5b 样本不足）；importance 静态加成上限只有 0.05；真实“自然延续”需多日对照才能下结论 |
| 冷门的重要共同经历仍能正常召回 | imp>=7/pinned 不衰减、不自动降权；长尾池包含；新增离线测试：开启活跃度排序时冷门 imp8 仍出现在结果里 | **部分/保底机制是** | 实际召回取决于 query/judge，#455 无 >30 天冷门重要样本；不能承诺任意提问都能召回 |
| 重要共同经历不会因久未提及被遗忘 | 衰减只作用于活跃度投影，不改重要度、不删除/隐藏；lab 模拟 60 天后 imp8 仍 1.0 | **是（机制层）** | 60 天是注入时间戳模拟，不是真实等待数月；真实长期观察待后续 |
| 界面可改开关/参数并持久化 | API + jsdom 渲染测试；保存文件、下一轮生效；重启读取 | **是（结构/接口）** | Windows 真机鼠标点击、关闭重开由 Lan 按 B-12 验收 |
| 并发/失败/取消/重试/重启不产生虚假强化 | 单测覆盖 4 线程、回滚、重放、重启、锁失败队列、最终裁剪、重生成；Lab 实测回滚/重启 | **是（测试与隔离链路）** | 真实 Windows 多进程竞争下仍以 2s 有界等待 + 重试队列兜底，未做长时压测 |

**结论**：本单把“送达才记账、活跃度与重要度分离、保护性不衰减、版本化重评”做成了可运行的真实链路，并在隔离环境验证；但**“常聊主题自然延续”的净收益和“冷门重要经历长期召回率”没有足够真实数据，不能宣称已验证**。因此活跃度排序默认关闭，等待 Lan 在多日真实使用后决定；若体验没有改善，应保留现有 importance 通道、不打开排序，而不是用单测结果替代体验验收。

