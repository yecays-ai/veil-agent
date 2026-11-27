# P6-S6-1 记忆动态评价、强化与保护性衰减 · 施工与隔离验收报告

> 工单 462 · 分支 `ticket/462-p6-s6-1-memory-reinforcement` · 基线 `main = ead71b6`
> 数据边界：本报告不含真实聊天、真实记忆正文、密钥或本机绝对路径中的用户数据；实验使用本地只读快照的隔离副本与合成记忆。**未合并、未部署、未启停正式 Peter。**

## 1. 结论摘要

- 送达记账做成了**派生状态 + 幂等事件账本**：只在 `Chat.send()` 确认回复已写入并显示后更新；同一 `(request_id, memory_id)` 重放不重复计数；失败、取消、回滚、重启、重生成都不产生虚假强化。
- 长期重要度与活跃度**分开存储**；重评建议带原因码、权重、具体证据引用和策略版本，应用时写单份 `versions`，可撤回；锁定/置顶条目不自动调整，降权永远先进入待审，因此本单不会自动降权重要共同经历。
- 保护性衰减只对 `importance <= 6` 且未置顶的**活跃度投影**生效；`importance >= 7` 与 `pinned` 永不衰减；衰减不改重要度、不删除、不隐藏。
- 活跃度排序是**可选信号**，默认关闭。理由：#455 的 `AB-R5b` 只有 `+1/53` 且无冷门重要样本，证据不足；本单只保证信号可靠，等真实多日送达数据积累后再决定。
- 隔离真实链路（VEIL Lab，mock，¥0）已验证：送达后更新、重复确认幂等、回滚不增加、重启保留、60 天后低保真衰减而高重要度不动、5→6 自动重评并写版本。
- 完整 Python 套件失败集合与 `origin/main` 基线一致（见 `8）；新增 26 项 Python 测试与 2 项桌面测试，桌面全套 62/62。

## 2. 范围、前置与并行边界

### 2.1 前置确认

| 前置 | 结论 | 证据 |
| --- | --- | --- |
| S5 送达契约（工单 460 / PR #461） | 已合并 `504c0a7` | 五层状态与 `delivery_listeners` 在 `veil/brain/memory_agent.py` |
| S6 五项开工门禁（工单 461 / PR #462） | 已合并 `ead71b6` | `docs/tickets/461-p5-s6-gates.md` |
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

- 事件键：`sha256(request_id + '|' + memory_id)`，主键；`INSERT OR IGNORE` 后 `rowcount==0` 即重放。
- 事务：`BEGIN IMMEDIATE`；SQLite 跨进程串行化；聚合更新与事件插入同一事务。
- 重生成：`regenerated=True` 时记录曝光（`counted=0`）并推进 `last_active`，但不增加强化计数/分数。
- 失败/取消/回滚：不调用 `confirm_delivery()`，不写任何派生行；`rollback_recall()` 只回滚冷却，不触碰记账。
- 重启：`pending_deliveries` 只在内存；重启后旧的确认不会重放；账本主键仍防重复。
- 提案：待审状态上的部分唯一索引 `memory_proposals_pending`；冷却按最近一次提案时间；应用后若证据未增加不再重复提。

### 3.3 保护守卫

- `is_protected(importance, pinned) = pinned or importance >= 7`。
- 自动应用条件：方向为提升、单步、结果 ≤ `reeval_max_auto_importance`（默认 7）、置信度 ≥ `reeval_min_confidence`、非 `pinned`、非 `locked`。
- 降权、进入 8+、锁定/置顶：只生成 `pending`，由 Lan 决定。
- 任何应用都只写 `importance` 字段，不写正文；`apply_update_memory()` 同时写 `versions` 与 frontmatter。
- `hard_delete()` 在同事务删除三张派生表里该记忆的行。

## 4. 实现位置

| 文件 | 改动 |
| --- | --- |
| `veil/brain/memory_weights.py`（新） | 设置校验/持久化、送达记账、衰减投影、证据评估、提案/决策/撤回、统一 `propose()` 接口 |
| `veil/brain/storage/brain_writer.py` | 三张派生表 DDL、索引、待审唯一约束；硬删除清理 |
| `veil/brain/memory_agent.py` | 构造 `weights`；`confirm_delivery(request_id, session_key, regenerated)` 接送达；`search_candidates()` 合并可选排序设置；生命周期诊断写入 `effective_used/activity` |
| `veil/core/veil_agent.py` | `send()` 交付确认时传会话键与重生成标记；异常向后兼容 |
| `veil/brain/recall/brain_retrieval.py` | 可选 `activation_ranking\(_enabled\)` 排序项与诊断；默认关闭 |
| `veil/brain/storage/brain_authority.py` | 工具检索复用同一组 UI 设置 |
| `veil/brain/brain_p4_api.py` | 记忆库投影 `activation/reeval`、活跃度排序、权重状态/设置、提案列表/决策、手动重评 |
| `veil/server/web_agent.py` | `GET/POST /api/brain/memory-weights`、`GET /memory-proposals`、`POST /memory-proposal`、`POST /memory-reevaluate` |
| `desktop/workspace-brain.js` | 记忆监控权重面板、记忆详情活跃度/待审/历史/撤回、过滤与排序 |
| `tests/test_p6_s6_memory_weights.py`（新） | 26 项 |
| `desktop/tests/memory-weights.test.cjs`（新） | 2 项 |

## 5. 单元与集成证据

    python -m unittest tests.test_p6_s6_memory_weights -v          # 26 项通过
    python -m unittest tests.test_p6_s5_retrieval_judge_association \
      tests.test_brain_retrieval tests.test_brain_writer tests.test_brain_store \
      tests.test_p4_p5_brain_routes tests.test_p6_s6_chat_evidence_budget \
      tests.test_p6_s6_idempotent_writes tests.test_p3_1_brain_only     # 98 项通过
    node --test desktop/tests/*.test.cjs                            # 62/62 通过

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

- Run：`s6-1-activation-20261128T004338`（`veil-lab/runs/s6-1-activation-20261128T004338`）
- 快照：`peter-20261127T170245 / r3`（2026-11-28 00:42 刷新，正式目录无变化）
- 代码：branch worktree 的 `veil/` 复制到 run 内 `code/`，`run.json.code_root` 指向它；`code_sha256 = 44ddcdffcb71cab718a36ae52cccc4a566459d8e5f2d25254d21c808ca52a869`
- 模式：mock（不调用模型、不花钱）；实验变量：`memory.recall_judge.enabled=false`、离线检索 `weight_vector=0.0 / weight_keyword=1.0`（让合成的 `S61LAB` 记忆可被检索；不影响送达/记账逻辑）
- 合成数据（仅实验副本）：记忆 `s61laba`（imp5，事件+意义+为什么）、`s61labb`（imp8）、`s61labc`（imp5，无事件/意义）；事件 `s61lab-ev-a`；关系条目 `s61lab-rel`（`confirmed`，引用 `event:s61lab-ev-a`）
- 代码版本漂移说明：快照钉住的代码是 `2a264a7`，早于 S3；本分支的 S3 证据校验不再注入快照里那条无来源的 `uncertain` 关系条目，导致首次 `run verify` 缺少 `relationship_base`。为让核验对照成立，在实验副本里额外加了一条 `source='lan'`、`locked=true` 的合成关系条目（`s61-lab-rel`），之后 `run verify` 通过。这是快照代码与当前分支的差异，不是本单引入的运行回归；详见 `7。

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

### 6.2 真实链路验证命令（可复现）

    cd E:\Peter-Agent\VEIL\veil-lab
    ..\veil-agent\.venv\Scripts\python.exe -m veil_lab run verify s6-1-activation-20261128T004338
    ..\veil-agent\.venv\Scripts\python.exe -m veil_lab run chat s6-1-activation-20261128T004338 --message "S61LAB ..."

（实验脚本在 `veil-lab/.tmp/s61_*.py`，只留本地，不提交。）

## 7. 与 #455 基线对照与排序决定

#455 的相关结论（`docs/reports/p6-s4-decision-table-20261126.md` `S6` 第 7 条、`p6-s4-ablations-20261126.md` `AB-R5b`）：

- 静态 `importance` 加分 `+0.05` 已是最优；
- `last_active` 近期加分：近 7 天只有 `+1/53` 命中，离线库缺少 >30 天冷门重要样本，**无法证明保底**；
- 保护性衰减离线补测：无衰减 `0.5444`、朴素衰减 `0.5278`、仅 `imp<=6` 衰减 `0.5556`；重要旧经历 `3/9 → `2/9`（朴素）/ `4/9`（保护）。

本单的处理：

1. **不把 `AB-R5b` 直接上线**：`activation_ranking_enabled` 默认 `False`，UI 里标"实验"；没有送达记录时开关严格无操作（单测断言前后顺序一致）。
2. **先让数据源可靠**：真实送达才更新；事件账本幂等；并发/重启/回滚都有测试；界面能看送达次数与跨天/会话。
3. **用已有 importance 通道承接"常聊主题自然延续"**：有证据的重复经历会把 5→6→7（自动上限 7），继续走现有的 `importance +0.05` 与长尾池，不依赖未证明的活跃度排序。
4. **重要旧经历保底**：`imp>=7`/`pinned` 不衰减、不自动降权、不删除；降权需 Lan 确认。
5. **净收益何时能评估**：等真实多日送达事件积累（本单之后），用同一 `activation_ranking` 开关在隔离副本做 on/off 对照；当前**不声称**排序净收益为正。

## 8. 完整回归

- 本分支完整套件：`python -m unittest discover -s tests -v`，**2527 项，18 失败 + 3 错误 + 29 跳过**。
- `origin/main = ead71b6` 同命令基线：**2501 项，18 失败 + 3 错误 + 29 跳过**。
- 两边失败/错误名称集合 **21/21 完全一致**（`comm` 双向差集为空）；多出的 26 项全部来自本单新增测试且通过。

| 项 | 本分支 | origin/main 基线 |
| --- | --- | --- |
| 测试数 / 失败 / 错误 / 跳过 | 2527 / 18 / 3 / 29 | 2501 / 18 / 3 / 29 |
| 失败/错误名称集合 | 21 个（maintenance / OpenCode / 备份 / APPDATA 桌面数据类） | 21 个，同名同序 |
| 记忆/检索/Brain 相关失败 | 0 | 0 |
| 新增测试 | +26（全部通过） | — |

已知既有失败类别（与 #461 记录一致）：Maintenance CRLF、OpenCode 未安装、APPDATA desktop-data、备份组件清单。本单未改这些模块。

## 9. 剩余能力边界与未验证项

- 活跃度排序净收益未证明（#455 证据不足 + 无长期真实送达历史）；保持可选、默认关。
- 重评触发器只有"送达后"和"手动/API"；没有夜间批量、没有跨会话模型巩固（S6-3）。
- 自动提升上限为 7；8+、降权、锁定/置顶均需 Lan 确认。
- 衰减只作用于活跃度投影，不写 `importance`；没有"遗忘删除"。
- 投递事件有界（默认 20000 条，可调），淘汰只删最旧事件，不改聚合计数；重评提案为保留审计暂不自动清理，长期运行需按实际节奏再评估上限。
- `brain_backup` 的“备份后新增记录合并”只合并记忆内容相关表；派生激活/提案不会从源库补进恢复后的库，恢复后从下一次真实送达重新积累，不伪造历史。
- 实验脚本与合成数据只在 veil-lab 本地；未提交、未外发。
- 未验证/未覆盖：真实主聊天模型下 judge 与 S6-1 的联合行为（本单不改 judge）；S6-2 合流后的记忆监控页布局；多日真实日常下的重评节奏；Windows 真机 UI 手工体验（由 Lan 验收）。

## 10. 交接

- 分支/worktree：`ticket/462-p6-s6-1-memory-reinforcement` / `worktrees/462-p6-s6-1`
- PR：#463（待开）；不自行合并、不部署。
- 状态文档：`STATUS.md` / `HANDOFF.md` / `docs/ACCEPTANCE.md`（B-11）。
