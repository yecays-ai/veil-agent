# P7-S1 生产经验 Provider 与协同恢复验收记录

> 工单 469 · PR #470 · 基线 `c7f23ed` · 2026-11-28 +08:00

## 结论

P7-S1 规划范围已在隔离假数据中完成开发侧验收：生产 Chat 已安装角色隔离 provider；P7 默认关闭时不查询候选、不产生 Token 或模型费用；开启后只经 `CognitiveConsolidator` 治理接口读取 `confirmed` 候选，并在返回前复核永久否决状态。`memory/event/chat/autonomy/tool/long_task` 六类证据共享 150ms 墙钟预算并 fail-closed；P7 配置、巩固状态和未来反馈账本进入同一备份 bundle generation，旧备份恢复单调保留后来否决并重新对账证据。

本记录只证明开发侧结果。代码未部署，Lan 未做体验验收，正式 Peter、正式记忆与正式运行配置均未改动；没有模型或 Embedding 调用，费用 ¥0。

## 交付范围

| 规划门槛 | 实际交付 | 证据 |
| --- | --- | --- |
| 生产角色隔离 Provider | `Chat.__init__` 为当前 `cfg.id` 安装 `ExperienceHintProvider`；对象层拒绝角色不匹配；只调用统一候选查询接口 | `tests.test_p7_s1_experience_provider.test_role_isolation_and_veto_recheck` |
| 统一 SituationEnvelope | Chat 首轮将文本、入口类型、同轮查询向量、S5 `context_signal` 与自主唤醒态势统一传入；仍兼容旧 `str` provider | `tests.test_chat.ChatTests.test_p7_slot_is_auxiliary_bounded_and_confirmed_only` |
| 全类型证据新鲜度 | 六类证据逐类验证；日志使用文件标识+尾部 SHA-256 checkpoint，DB 使用 canonical digest；候选共享 150ms；超时/锁定/解析失败不注入 | `test_all_evidence_types_fail_closed_after_delete_or_change` |
| 否决状态复核 | 候选评分后再次经统一查询边界读取状态和永久否决账本，已提交 veto 不进入渲染 | `test_role_isolation_and_veto_recheck` |
| 向后兼容契约 | 保持 `p7.experience.v1`，新增字段均为可选投影；旧记录保留原字段并标 `experience_type=unknown_legacy` | `test_legacy_contract_is_additive_and_conservative` + P6-S6-3 14 项回归 |
| P7 配置持久化 | `experience-injection.json` 角色隔离、默认 `enabled=False`、revision/409、硬上限 4 条/600 tokens；配置总览可读 | `test_default_off_persists_with_revision_and_zero_candidates` |
| 协同备份、恢复与对账 | 四个 P7/巩固文件进入 `COMPANION_FILES`；共享 Brain 写锁；manifest 带 generation；恢复时永久否决单调合并，P7 候选重新核验证据 | `test_backup_waits_for_atomic_p7_config_write`、`test_backup_restore_preserves_later_veto_and_generation`、既有 P6-S2 8 项恢复回归 |
| 实际调用链与异常降级 | 开启后经验块实际进入模型请求；默认关闭 0 Token；provider 抛错返回空槽位，不阻断聊天 | `test_production_p7_provider_reaches_the_real_request_chain_when_enabled`、`test_production_p7_provider_defaults_off_and_provider_failure_is_fail_closed` |

## 新鲜验证

- `python -m unittest tests.test_p7_s1_experience_provider -v`：6/6 通过。
- `python -m unittest tests.test_chat -q`：115/115 通过。
- P7-S1 + P6-S6-3 + 配置注册 + P6-S2 备份恢复定向组合：35/35 通过。
- 扩展备份/恢复兼容集：89/90 通过；唯一失败为主线既有冷备份测试在本机自动发现仓库外 `desktop-data` 后仍固定断言 13 个文件（实际额外打包 3 个桌面文件），本单未修改该系统备份路径或测试，P7 Brain bundle 相关用例全部通过。
- `python -m py_compile ...`：通过。
- `git -c core.whitespace=cr-at-eol diff --check`：通过。

完整仓库 CI 以 PR #470 的 GitHub Actions 为准；本地未运行真实模型、正式数据或 Windows 打包桌面验收。

## 影响与接入核对

| 用户场景 | 改后行为 | 验证 |
| --- | --- | --- |
| 日常聊天，P7 未开启 | 与主线一致：P7 槽位为空、0 Token、0 费用 | 完整 Chat 115 项 |
| 开启后读取经验 | 仅当前角色、`confirmed`、证据仍有效且未否决的经验进入原 S7 槽位 | 生产链测试 + 隔离测试 |
| 删除/撤回证据或日志不可读 | 保守返回空，不误删永久状态，不阻断聊天 | 六类型删除/改写测试 |
| 切换 Peter/Ghost | 配置、候选、DB/日志路径与 provider 对象全部按角色隔离 | 双角色测试 |
| 备份与恢复 | 快照不会截取半写文件；旧备份不会复活后来否决；恢复后重新对账证据 | 并发锁 + 旧备份演练 |
| 权限与费用 | 经验仍是 AUXILIARY 不可信数据，不改变工具权限/审批；S1 没有新增模型调用 | 结构检查 + 默认关闭测试 |

八项接入结论：入口只接现有 Chat 首轮（工具步骤/自主入口细化留 S2）；停止与恢复无新增运行任务；权限不变；费用默认 ¥0；数据归角色目录并纳入备份；状态可从请求诊断与配置总览读取；旧文件零迁移；Windows 正式桌面未部署、待 PR 合并后再安排。

## 明确未施工

- 未施工 S2：向量混合匹配、隐含场景、短应和、冲突裁决、HEAD 去重、会话冷却、工具步骤补检和自主入口域策略。
- 未施工 S3–S5：四级自动晋升、反馈账本实际写入、自主判断工具、Brain UI/H-8 与 10 场景总验收。
- 未启停、更新或测试正式 Peter；未读取、复制或写入正式 `data/peter`；未产生付费调用。

## 审查修复记录（PR #470 · 18 条）

按 PR #470 的 18 条审查意见在 S1 范围内集中修复；语义匹配与候选池硬截断不提前施工。

| 审查意见 | 修复 | 缺陷复现测试 |
| --- | --- | --- |
| #1 旧快照进入请求 | 候选查询与 `confirm_experience_candidates()` 在 `mutex + brain-write.lock` 同步边界读取；Provider 返回前、Chat 渲染前各复核一次，已提交否决立即失效 | `test_veto_committed_after_query_is_rejected_before_render`、`test_p7_final_veto_recheck_drops_hints_before_send` |
| #2 存储提示劫持工具 | 经验正文清理控制字符与数据边界标记，放入独立 `<<<EXPERIENCE_DATA>>>` 区块；提示词声明不可信数据，权限/审批/预算/参数校验由程序独立执行 | `test_p7_slot_is_auxiliary_bounded_and_confirmed_only`（数据区块断言） |
| #3 并发保存丢失永久否决 | 状态读改写统一锁边界；`_save_state()` 在锁内与磁盘状态合并否决/恢复治理字段，旧快照不能复活否决 | `test_stale_state_save_cannot_resurrect_permanent_veto` |
| #4 旧备份撤销后续恢复 | 恢复合并按 `decided_at` 与 bundle `created_at` 排序：晚于备份的显式恢复优先于备份中的否决 | `test_old_backup_does_not_undo_later_explicit_restore` |
| #5 已删除事件证据仍有效 | 事件必须有仍有效的关联记忆或事件来源；全部失效即 `invalid` | `test_event_without_live_memory_or_sources_is_invalid` |
| #6 正文修改仍被采用 | 经验形成时记录逐引用内容指纹；原 ID 正文改变 → `invalid`，旧经验停止注入 | `test_in_place_evidence_edit_invalidates_recorded_basis` |
| #7 部分失效仍注入 | 按 P7-0：`partial_stale` 只有仍有有效核心证据（按 `origin_evidence_tier`/形成期 core refs）才放行；核心证据失效 → `invalid`，无法证明 → `unavailable` 暂停 | `test_partial_stale_requires_remaining_core_evidence` |
| #8 待删除请求未失效缓存 | DB 缓存指纹显式包含 `delete_requests(status='pending')`；新增待删除即重验 | `test_pending_delete_request_invalidates_cached_validation` |
| #9 已撤回自主活动仍有效 | 自主/工具/长任务日志统一检查 `deleted/withdrawn/retracted` | `test_withdrawn_autonomy_evidence_is_invalid` |
| #10 临时故障永久降级 | `unavailable` 只暂停注入；仅 `invalid` 在恢复对账时把非锁定 confirmed 降为 uncertain | `test_transient_unavailable_does_not_demote_confirmed` |
| #11 长任务读取超预算 | 单文件最多 512KB，读取/解析前后检查共享 deadline，超限返回 `unavailable` | `test_long_task_read_is_bounded_by_budget` |
| #12 Profile 否决读取失败 | Profile 存在但读取/否决检查失败时生产查询 fail-closed 返回空 | `test_unreadable_profile_veto_ledger_fails_closed` |
| #13 恢复回滚配置 revision | 恢复 `experience-injection.json` 时 `_revision = max(实时, 备份) + 1`；旧客户端 CAS 得到 409 | `test_restore_keeps_config_revision_monotonic` |
| #14 证据缓存无限增长 | 缓存按 DB 指纹淘汰并封顶 512 条；来源缓存只保留最新文件指纹 | `test_validation_cache_stays_bounded` |
| #15 HANDOFF 重复部署状态 | HANDOFF 删除部署细节，只链接 `STATUS.md` 权威记录 | 文档核对 |
| #16 200 条候选池硬截断 | 登记为 P7-S2 `P7-MATCH-02`，本单不提前施工语义匹配 | 文档登记（STATUS/HANDOFF/工单/P7-0 规划） |
| #17/#18 SQL 扫描告警 | 核实为固定 SQL 片段 + `?` 参数绑定误报；本次改为纯字面量参数化查询（无字符串拼接），不引入 ORM、不重构数据库层 | `py_compile` + 代码核对 |

### 审查修复后新鲜验证

- `python -m unittest tests.test_p7_s1_experience_provider -q`：20/20 通过。
- `python -m unittest tests.test_chat -q`：117/117 通过。
- 认知巩固、配置注册/总览、Brain 协同备份、P6-S2 恢复定向集：118 项，117 通过；唯一失败为主线既有冷备份测试在本机发现 `%APPDATA%/VEIL Desktop` 的 3 个桌面文件后仍断言 13 个文件（实际 16）。本单未修改 `veil/system/backup_restore.py` 或该测试，属既知环境相关失败，与本次修改无关。
- `python -m py_compile`（全部修改文件）与 `git diff --check` 通过。
- 全程隔离假数据、无模型/Embedding 调用、正式数据和正式配置 0 改动。

### 审查意见复核要点（SQL 两条）

- 原告警位置对应的 SQL 为固定常量片段：`SELECT bucket,type FROM memories WHERE id=? AND is_tombstone=0 AND is_deleted=0` 与 `SELECT bucket,type FROM memories WHERE id=? ...`；`AND NOT EXISTS (...)` 是代码内固定片段，不含外部输入，所有变量值为 `?` 参数绑定。
- 修复后改为纯字面量查询 `SELECT body,title,type,bucket,is_tombstone,is_deleted FROM memories WHERE id=?`、`SELECT * FROM events WHERE event_id=?`、`SELECT memory_id FROM event_sources WHERE event_id=? ORDER BY ord`，待删除请求单独参数化查询；仍不引入 ORM、不改变数据库层结构。
