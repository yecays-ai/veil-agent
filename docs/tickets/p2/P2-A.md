# P2-A · Brain 独立写入与完整事件数据

## 1. 当前确认的目标

在隔离环境让 Brain 独立承担全部正式记忆写入与事件存储，成为唯一写入权威；正式读写切换留给 P2-B。
默认关闭，不改变正式 Peter 现有行为。

- 覆盖：新建、重写、合并/link、隐藏/恢复、锁定/解锁、归档/解档、删除申请/确认/软删/硬删、
  版本记录/恢复、来源/链接/sidecar、事件创建/续写/拆分/移动/故事锁定/来源变更。
- 完整事件数据入库：故事正文、阶段、来源、时间、状态、confidence、approved、原话/来源引用；
  不再只存 event_id/memory_id 映射。
- 写入反馈三态，禁止“部分保存却报告全部成功”：
  - `saved`：正文、版本、sidecar、关系/来源、事件关联、向量已在同一事务提交。
  - `saved_index_pending`：正文等已提交，但 embedding 失败；明确告知“已保存，检索索引待重试”，
    以 `vector_state='pending'` 入队重试，不谎报 saved。
  - `failed`：任一步失败且事务回滚，没有半条记录。
- 硬删除：清正文、原始 Markdown、版本、向量、关系、来源、引用；只保留最小 tombstone
  （id、deleted_at、原因摘要，不含正文/原话），用于阻止回退时复活。软删保留可恢复标记。
- 可见性保持现有已确认行为，不一律不可检索：
  - hidden/dont_surface：不自动召回，页面可见/可恢复；
  - locked：仍可召回，Peter 不可改，Lan 可改；
  - archived：不进入正常召回，归档页可见/可解档；
  - tombstone/deleted：不召回、不展示；hard delete 内容不可恢复。
- 事件数据与关联由本单验证；真实聊天中“事件与碎片不重复注入”的验收归 P2-B。
- 导出物完整性由本单验证；**实际启动 Ombre 验证回退归 P2-B**，不用“重新导入 Brain”替代。
- **互斥**：`write_authority='brain'` 时 P0-B 影子同步必须关闭；配置校验/运行期/测试三处拒绝
  同时开启，防止旧 Ombre 数据覆盖 Brain 新写入。默认配置保持现状（shadow=true，authority=off）。
- 不做：读取切换、页面/API 改造、标签/分类治理、145 条归组、正式环境切换、Ombre 下线。

## 2. 代码与数据现状

- main `ef446f2`；正式库 284 记忆 / 281 版本 / 245 向量 / 170 sidecar 事件（折叠 110 状态）/
  69 事件 / 157 来源；P1 已切自动召回（试用样本仅 2 轮）。
- `events` 表仅 `event_id,memory_id,source_count`；`event_sources` 仅映射；`event_stages`
  仅 `stage_id,event_id,at,state` 且 0 行。69 篇 story、69 条阶段、来源/审批/置信度等全在
  `data/peter/events.jsonl`、`event-stages.jsonl`——Brain 目前只有事件映射，没有完整事件正文。
- 写入口：`Memory.call('hold')`（memory_native._hold、event_memory、experience_memory）；
  `Memory.call('trace')`（_trace/_rewrite/_archive/panel_*、memory_agent.erase、event_memory、
  experience_memory）；删除队列/确认/erase；sidecar 与 memory-versions 直写；事件 split/move。
  `learned_context.py` 不是记忆写入口，不纳入。
- 读取点（P2-B 处理）：opening/_related 仍走 Ombre；判官/assisted 读 embeddings.db；记忆页读文件/
  sidecar/versions；事件页读 JSONL；recall_rotation 读 Markdown。
- Ombre hold 不接受调用方 ID：Brain 自生成 12 位十六进制 ID 并查重；回退导出复用 P0-A 快照格式
  （Markdown + embeddings.db + memory-versions.jsonl + memory-sidecar.jsonl + events.jsonl），
  不改记忆正文/来源/事件关系/版本/原始 ID。
- 写日志复用现有 write_reconciliation 语义，扩展为 Brain 事务日志；不另造两套账。
- 向量：优先复用未变化正文的旧向量；变化时生成一次；失败进入 pending 重试；遵守现有预算。
- 备份：brain.db + 写日志 + 事件数据 + schema 版本；恢复后 digest/counts 一致。
- 与 B/C 边界：B 负责读取切换、页面 API 兼容、旧机制收敛、黄金问题集、真实切换与 Ombre 启动回退；
  C 负责标签/domain 与 145 条核实归组（本单不做）。

## 3. 用户体验验收步骤（全部隔离）

1. **事件迁移对账**：隔离副本执行 schema v3；69 篇故事/69 条阶段逐字段核对（story/title/status/
   first_at/last_at/story_source/confidence/approved/related_events/previous_events/source_parts/
   sources/facts/text/state）；未知字段无损保存在 payload_json；重跑幂等；迁移前备份可恢复。
2. **写入矩阵**：新建、重写、合并、link_to、隐藏/恢复、锁定/解锁、归档/解档、删除申请/确认/
   软删/硬删、版本恢复、来源/链接、事件创建/续写/拆分/移动/故事锁定；逐项核对正文、版本、状态、
   sidecar、关系、事件来源、向量；无重复记录。
3. **三态反馈与失败**：正常保存返回 saved；embedding 失败返回 saved_index_pending 且可重试；
   DB 写入失败返回 failed 且无半条记录；进程中断后重启只完成一次；不出现虚假成功。
4. **可见性回归**：hidden 不自动召回但页面可见；locked 仍召回且 Peter 不可改；archived 不进入
   正常召回但可解档；tombstone 不召回不展示；hard delete 正文/版本/向量/关系全清，仅留最小 tombstone。
5. **事件与关联**：完整故事/阶段/来源可读；记忆与事件来源双向一致；同一事件不会产生两条记忆映射；
   事件操作后 sidecar/来源仍正确。
6. **互斥**：authority=brain + shadow=true 启动被拒绝并给出明确错误；单独 authority 可运行；
   shadow 单独运行与现在一致；默认配置不变。
7. **导出完整性**：切换期做新增/修改/删除，导出同 ID 的 Markdown、embeddings.db、versions、
   sidecar、events/stages；逐条核对新增/修改/删除结果、ID、版本、状态、事件关联；导出物可被
   Ombre 格式解析（P2-B 实际启动 Ombre）。
8. **备份恢复**：备份 brain.db + 写日志 + 事件数据，恢复到全新隔离目录，记忆/事件/版本/状态/检索可用。
9. **旁路审计**：静态检查无 hold/trace 写入绕过适配层；页面 API 返回结构未变；默认 flag 关闭时
   正式行为与现状一致。

通过标准：0 缺失、0 重复、0 半条记录；69/69 事件与阶段完整；所有写入口覆盖；导出含最小 tombstone；
隔离 Peter 能聊天/保存/修改/想起。任一不通过不得报告 P2-A 完成。

## 4. 交付前自审（本单强制）

- 推 PR 前逐条对照本工单第 1/2/3 节，特别是：三态反馈、tombstone、可见性、shadow 互斥、
  事件 69/69、导出完整性、备份恢复、无旁路写入。
- PR 说明和 STATUS/HANDOFF 写明每条“完成/未完成 + 证据”；未完成不得声称 P2-A 完成。
- 审查评论只改功能/数据/安全问题；一次性修改、一次性推送。
