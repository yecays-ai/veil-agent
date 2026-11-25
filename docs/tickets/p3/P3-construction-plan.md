# P3 施工方案（修订版 v2）

> 依据 Lan 2026-11-25 的 13 项决策与三项施工原则；本文件是 P3 的全局方案，具体范围/验收以各工单为准。
> 当前状态：P3-0 前置；P2 正式体验验收通过前不启动 P3-1。

## 1. 13 项决策落点

| # | 决策 | 落点 |
|---|---|---|
| 1 | 解决更新覆盖 Brain 接管配置 | P3-0：过渡配置提交 main + 测试锁定；P3-2 删过渡开关，Brain 唯一 |
| 2 | 取消运行时 Ombre 回退，仅留备份/离线导出/恢复 | P3-1 删启动回退与 Ombre 启动；P3-2 删代码/依赖；P3-4 验收离线备份 |
| 3 | 39 条墓碑彻底清除正文/raw/版本/向量/残留，仅留最小防复活标记 | P3-4 dry-run → 备份 → 执行 → 导出/恢复复验 |
| 4 | 隐藏与 digested 全部恢复可见；取消旧衰减标记；不按旧标记自动删普通记忆 | P3-3：取消 hidden 行为/迁移唯一行；digested 等保留数据、取消行为 |
| 5 | letters/plans 核对迁移完整后删除记忆库重复记录 | P3-3 核对导出；P3-4 执行删除 |
| 6 | 页面 全部/最近/经历/已归档；取消 domain 依赖；P3 只做兼容，整体页面留 P4 | P3-3 页面小改 + API 兼容；domain 数据保留只读 |
| 7 | 不可逆清理允许维护窗口执行，但需全量备份/恢复演练/dry-run/名单；Ombre 安装包留仓库外离线备份 | P3-0 预检备份/离线恢复演练；P3-4 runbook；安装包离线保留 |
| 8 | P6 边界：动态重要度/强化/衰减/智能召回升级不在 P3 | 各工单“不做”清单；只保留字段与人工 pinned/importance |
| 9 | 删空转 dream 工具；保留事件维护与后台经验整理 | P3-2 删工具/权限/描述；保留 event 维护与 experience_memory |
| 10 | 历史文档/脚本有价值归档，无价值删除，不留正式运行路径 | P3-2 归档/删除 |
| 11 | 事件附账本统一迁入 brain.db，备份恢复导出覆盖完整生命周期 | P3-2 schema + 迁移 + 导出 + 备份恢复验收 |
| 12 | 切换期 hard_delete 核实是否 canary/误删 | P3-0 已核实：真实旧记忆被误删；Lan 已批准定点恢复并完成；报告留仓库外 |
| 13 | pinned 保留并修复；pinned/importance 写入/修改/读取/排序生效；动态权重留 P6 | P3-1 修复 + 端到端测试 |

## 2. 三项施工原则

### 2.1 保留 P6 认知机制所需数据

- **保留列/值**：`meaning`、`valence`、`arousal`、`first_of_kind`、`last_active`、`importance`、`pinned`、`relations`、`sources`、`quotes`、`raw_markdown`、`frontmatter_raw`、`extra_json`、`tag_migration`、`category`、`domain_json`、`tags_json`。
- **保留旧语义值**：`digested`/`resolved`/`protected`/`anchor` 不再参与行为，但值保留；必要时迁移到只读 legacy 字段。
- **不得丢的 raw/frontmatter 字段**：`activation_count`、`recall_cues`、`relation_links`、`source_links`/`source_refs`、`quotes`、`change_log`、`i_*`、`weight`/`status`、`author`/`lock_type`/`locked_by`/`writer_name` 等；P3-2 建字段清单与迁移校验。
- **不得以“当前没有读取方”删除** `relations`/`sources`/`quotes`/`meaning`/`valence`/`arousal`/`first_of_kind` 等；P3 只取消其 UI/检索依赖。
- 只删控制逻辑与冗余副本：hidden/dont_surface 过滤、旧衰减行为、Ombre 衰减引擎、dream 空转、tags/domain UI/检索依赖、影子同步、旧 JSONL/vault 镜像（先确认数据已在 Brain 或离线备份）。

### 2.2 统一记忆系统

- **权威**：`brain.db` 是记忆与事件生命周期唯一权威：memories/versions/vectors/events/event_stages/event_sources/sidecar_*/delete_requests/write_journal + 事件证据/版本/修订/操作/投影/审查 + legacy 元数据。
- **非真源**：diagnostics/context-log/chat/tools/autonomy/experience 运行记录；可独立保存，但不得成为记忆读取/恢复/导出的必需品。
- **统一方式**：沿用 `BrainStore` 的“旧路径 → Brain 表”模式扩展事件附账本；一次性迁移 + 幂等校验 + 备份/恢复/导出覆盖；不新建第二套存储。
- **离线可恢复**：brain.db + 配置 + 必要日志即可恢复记忆与事件；旧 vault/JSONL 只作历史归档。

### 2.3 不做无价值重构

- 不改 `brain_authority`/`brain_store`/`memory_sidecar` 等名称；不移动文件；不重写 P2 已验收的事件生成/判官/去重/冷却/预算/经验整理。
- 只改：真实依赖、重复职责（state/write-reconciliation/影子/旧 JSONL 读取）、功能缺陷（pinned/importance/删除申请/判官/事件缓存/检索降级）、数据完整性（事件附账本/墓碑/备份）、维护问题（CI/文档/配置）。
- 任何顺手重构必须先证明与当前工单直接相关，否则不做。

## 3. 配置持久化决定（P3-0）

- 正式切换后问题：`update-main` 会 stash + reset tracked 文件，工作区的 `write_authority=true/shadow=false` 下一次更新丢失。
- **不采用 `character.local.toml` 覆盖层**：VEIL 已有四层配置来源；再加 TOML 深合并会形成第二真源与永久维护面；P3 完成后 Brain 是默认且唯一，3 个过渡键不再需要按机器覆盖。
- **不采用 env 覆盖或改 update-main 特判**：额外临时/永久机制，难解释。
- **采用：把过渡配置提交 main**，作为 P2 正式切换的明确默认值；`tests/test_brain_defaults.py` 锁定；P3-2 删除 `[memory.brain]` 过渡表并让 Brain 唯一。
- 已知边界：P3-0 后到 P3-1/2 前，全新 checkout 无 `brain.db` 时会明确失败（不自动建空库，避免掩盖数据丢失）；正式目录已有 brain.db；全新机器初始化由 P3-1/P3-2 的 Brain-only 启动方案处理。

## 4. 事件附账本统一（P3-2）

- 现状：events/event-stages 已进 brain.db；stage-evidence/event-versions/event-revisions/event-ops/event-projections/event-review/event-dream 仍是文件真源。
- 方向：新增统一事件账本表（推荐 `event_ledger(seq, at, kind, event_id, stage_id, memory_id, payload_json)` + 索引，或按类型分表，P3-2 定稿）；`BrainStore` 映射这些虚拟路径；一次性幂等迁移旧文件；读写全走 Brain；文件转只读归档后删除。
- `brain_export` 扩展完整事件生命周期；`brain_backup` 覆盖 brain.db；恢复后 digest/计数校验；备份/导出文档写清“brain.db + data 目录 = 完整记忆”。
- 不丢字段：迁移时保留每类账本原字段（JSON payload 无损）；迁移脚本 dry-run + 计数/哈希对账。

## 5. 工单顺序与 gate

- **P3-0** 前置：配置持久化 + 方案/工单 + 备份/恢复预检 + 误删核实（已完成定点恢复；仓库外报告）。
- **P3-1** 启动解耦 + Brain-only + 修复 P2 缺陷 + 停用 Ombre（合并原 P3-1/P3-2；观察期作为本单验收）。
- **P3-2** 旧系统清理 + 事件附账本统一 + 备份/导出/诊断。
- **P3-3** 取消隐藏 + 去标签/domain 依赖 + 页面兼容。
- **P3-4** 维护窗口清理 + 物理卸载 + 最终验收（不扩大开发范围）。
- **P2 gate**：P2 正式体验通过前不启动 P3-1。
- 每单：独立分支/PR；隔离实机验收由开发方完成；Lan 只负责正式使用体验与不可逆操作授权；PR 等复审，不自行合并。

## 6. 备份 / 恢复 / 导出

- 在线备份：`brain_backup.py`（SQLite backup API，WAL-safe）每次代码单前后各一份。
- 冷备：停 Peter 后用 `backup_restore.py create` 到 `VEIL_BACKUP_DIR`（正常权限/维护窗口），含 data/characters/persona/.env；sha256/manifest；独立目录实际启动恢复验证。
- 导出：`brain_export` 扩展事件生命周期；Ombre-可读格式只作历史归档，不再作运行回退。
- 恢复演练：P3-0 已完成离线 restore drill；P3-4 做实际启动恢复；每次不可逆删除前再演练。
- 离线保留：Ombre 安装包复制到仓库外离线备份一个版本周期；P2 备份不删除。
- 仓库只提交脱敏报告；预检备份、真实数据、聊天、密钥、本机路径不提交。

## 7. 验收矩阵

### 7.1 Lan 体验场景

1. 正常聊 20 轮；保存提示；重启后仍在；改/版本恢复/锁定/归档/解档/删除可用。
2. 问 10 件旧事（不同措辞/事件/无答案）；该想起的想起、不硬凑；延迟不劣化；50 条黄金集记录。
3. 记忆页：全部/最近/经历/已归档可打开；无隐藏；无旧标签/domain 噪声；保留记忆可见；搜索标题/正文/why 正常。
4. 事件页：故事/阶段/原话/来源/证据完整；事件与碎片不重复注入。
5. 删除：页面与 `/delete` 后正文/版本/向量/来源全无；重启不复活；旧墓碑无正文残留。
6. 后台经验整理：静置触发一次、页面可见、二次不重复；失败提示可恢复。
7. 备份恢复：停服备份 → 独立目录恢复 → 聊天/记忆/事件/召回正常；digest 一致。
8. 无 Ombre：无进程/无 18011/无 `.deps/ombre`/启动无 Ombre 错误；启动依赖只剩主依赖。
9. 费用：无 Ombre 子进程模型调用；embedding 预算照常；`/cost` 正常。
10. 多角色：虚构角色记忆隔离。

### 7.2 自动回归基线

- `test_brain_*` 78、`test_recall_*` 234、`test_memory_panel` 8、`test_web`+`test_veil_brain` 83；P2 黄金集命中/覆盖率/延迟；数据计数基线（记忆/版本/向量/事件/阶段/来源/sidecar）。
- 每单新增：P3-0 配置持久化；P3-1 pinned/importance/删除申请/判官/事件缓存/降级；P3-2 事件附账本/备份恢复导出；P3-3 hidden 取消/标签/domain 页面契约。

## 8. 风险与对策

| 风险 | 对策 |
|---|---|
| 配置持久化失效，更新后回影子模式 | P3-0 提交配置 + 回归测试锁定；P3-2 删过渡开关 |
| 取消隐藏导致旧内容突然进入页面/召回 | 先备份 + 逐条迁移 → 再删行为/字段；Lan 确认 |
| 事件附账本迁移丢证据/版本 | 幂等迁移 + dry-run + 计数/哈希比对 + 旧文件只读一个周期 |
| 清理墓碑/旧文件误伤 P6 数据 | P6 保留清单硬约束；dry-run 名单；只删清单内数据；清理后导出/恢复复验 |
| 误删记忆未恢复 | P3-0 已核实并完成定点恢复；后续测试只用专用测试记忆 |
| P2 体验未过就进 P3-1 | P3-1 显式 gate；STATUS 记录 P2 验收状态 |
| 页面去 domain 后分组空 | P3-3 同 PR 页面小改；P4 再做整体设计 |
| pinned/importance 修复不完整 | P3-1 补端到端测试（hold/trace/opening/page） |

## 9. 未关闭问题

1. P2 正式体验验收：STATUS/HANDOFF 待 Lan；通过后才启动 P3-1。
2. P3-2 事件附账本表结构：统一 `event_ledger` vs 分类型表，在 P3-2 工单定稿；P3-0 只做 inventory。
3. P3-3 隐藏列 DROP 方式与页面分组：P3-3 定稿；数据先备份。
4. P3-4 清理名单：Lan 在维护窗口前确认；墓碑/letters/plans/旧文件/安装包分开批准。
