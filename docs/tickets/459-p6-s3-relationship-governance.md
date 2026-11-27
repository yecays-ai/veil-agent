# P6-S3｜关系底座解耦、证据链晋升与认知治理

- **工单编号**：459（分支 `ticket/459-p6-s3-relationship-governance`，worktree `worktrees/459-p6-s3`）
- **基线提交**：`main = 2a264a7`（PR #458 已合并，含 P6-S0/S1/S2/S4、P4/P5 管理中心及仓库目录结构重整）
- **产品格**：A（陪伴核心：关系认知、长期记忆与行为一致性）
- **做完后 Lan 能多做的一件事**：`facts` 和 `growth` 接近满载时，Peter 仍能正常记录和修订关系共识与进行中事项；从真实聊天及事件中形成有可靠用户原话证据支持的 `confirmed` 认知，杜绝自猜自证；Lan 随时拥有锁定、纠正、删除、恢复历史版本的最高控制权，并在 Brain 管理中心与召回诊断里区分“已存储”和“实际注入”。
- **与 P6-S5 并行边界**：
  - S3 独占/主责：`veil/brain/learned_context.py`、`veil/brain/relationship_base.py`、`veil/tools/learned_tools.py`、`veil/prompts/defaults/memory.learned.*`、`veil/prompts/defaults/memory.relationship*`、`desktop/peter-page.js`。
  - S3 与 S5 共享只读/接口衔接：`veil/core/veil_agent.py`（仅改 `profile` 配置解析、`relationship_base` 注入、预算裁剪及 `debug['relationship_base']` / `request_snapshots` 字段，不改检索/判官/联想逻辑）、`veil/brain/brain_p4_api.py` / `veil/server/web_agent.py` / `desktop/workspace-brain.js` / `desktop/chat.js`（仅改 `cognition` / `peter-page` / `relationship_base` 展示）。S3 不修改 `brain_retrieval.py`、`recall_judge.py`、`natural_association.py` 或 `memory_agent.py` 检索与判官逻辑。

## 基线审计与真实缺口（基于 `2a264a7`）

### 1. 四区独立预算与存量迁移
- **现状**：`learned_context.py` 使用 `DEFAULT_MAX_BYTES = 4000` 共享上限；`veil_agent.py` 默认 `profile.max_bytes = 3000`；`_validate()` 在 `_load()` 时直接调用 `_check_budget()`，导致存量超限时整个 `LearnedContext` 读取失败。
- **缺口**：缺乏 `facts=1800B / growth=2000B / relationship=1500B / ongoing=900B`（合计 6200B）四区独立计算、独立写入校验与超限降级状态；缺少无损幂等迁移入口（含 dry-run 与回滚保护）；旧 `profile.max_bytes` 会隐式覆盖或误导 UI/工具提示。

### 2. 关系认知严格证据链晋升与失效降级
- **现状**：`_verified_sources()` 仅校验 `chat:<message_id>`（`chat.jsonl` 中 `role == 'user'`），`event:` / `memory:` 仅作为指针保留，无法追溯 `brain.db` 中的 `event_ledger(kind='stage-evidence', role='user')` 与 `quotes(speaker='user')`。
- **缺口**：缺少经 `event:` / `memory:` 追溯真实用户原话的统一核验入口、同源证据去重、用户后续反驳/纠正校验、`stale_ref` 失效检测与安全降级（不足证据时降级而非继续作为 `confirmed` 注入），且缺少带缓存/索引的有界查询。

### 3. Lan 最高控制权与写入一致性
- **现状**：`relationship` 条目仅有 `source='lan'|'evidence'|'peter'`，无 `locked` 字段；Peter 可通过 `profile_replace` 修改或淘汰任意条目；Lan 删除的认知无否决墓碑保护；`save_relationship` 仅保留 `sources`/`superseded_by` 会剥离新增治理元数据。
- **缺口**：需要 `locked`、删除/否决保护（`vetoed`/`rejected` 记录防旧证据或恢复复活）、历史版本恢复入口、统一权限/锁定复核、正文与版本记录一致性保证。

### 4. 每轮最小关系底座与诊断
- **现状**：`relationship_base.select()` 按 `(status, source, updated_at, id)` 排序，超长单条整条丢弃，未优先保护 `locked`，未排除证据完全失效的模型认知，未记录“已存储 / 当前有效 / 本轮选中 / 实际注入 / 未注入原因”的完整分层诊断。
- **缺口**：补齐 `locked` 最高优先、超长高优先级条目的保义压缩呈现（仅作用于呈现层）、失效认知隔离、跨区冲突提示及真实请求快照后的注入状态回填。

## S6 统一认知治理接口契约

`LearnedContext` 对 P6-S6（夜间/空闲认知巩固）提供以下统一入口（零旁路，强制经过四区独立预算、严格证据核验、Lan 锁定/否决主权与原子版本提交）：

- `submit_candidate(section, text, *, status='uncertain', sources=(), replace_old_text=None, origin='consolidation') -> (message, state, tool_name)`：提交新增或修订候选，禁止无用户证据直接写入 `confirmed`。
- `reverify_relationship_evidence() -> {'changed': bool, 'items': list}`：复核在库关系认知证据链并安全降级失效模型条目（不自动改写 `source='lan'` 或 `locked=True`）。
- `set_relationship_lock(item_id, locked, expected_updated_at=None)`：Lan 锁定/解锁单条关系认知。
- `restore_relationship_item(item_id, expected_updated_at=None)`：从 `outdated` 历史条目恢复为 Lan 确认的新当前版本（保留 `restored_from` 与完整历史链）。
- `migrate_storage(dry_run=True)` / `rollback_migration(backup_path)`：幂等无损迁移与保新回滚（回滚只撤销迁移结构变更，保留迁移后的新增、编辑、删除、锁定、状态、否决与版本历史）。

### 供 S6 使用的能力边界

- **证据核验不是翻译或语义推断。** 自动确认只接受可追溯到原始用户消息、与候选整句一致（仅做轻量文本规范化）的直接断言，或与相邻、单一、明确的是非问句绑定的肯定答复。译文、概述、复杂改写、无原始消息的副本均不能独立晋升；不能核验时保留 `uncertain`，需确认时由 Lan 明确确认。
- **否决匹配不是语义等价判断。** 自动入口识别相同文本和部分高词面重合、包含关系的改写，可能漏掉低重合的同义改写、翻译，也可能保守拦截相近但有不同条件的认知。“未匹配”不等于 Lan 同意。S6 应复用统一入口、保留已有历史与否决，不以翻译、改写或换分区绕过；疑似冲突而无法判断时不自动写入，交由人工确认。

## 补充：独立语义核验

- 关系候选先按角色追溯真实用户原话及去重，再独立判断翻译、概括或多段经历是否支持整条认知；生成器自评、assistant/tool 文字、不能核实的副本都不构成支持。可靠确定性路径不增加调用；无法判定时可进入后台隔离核验。判官不拥有写入权限，晋升仍须重验候选版本、证据及用户主权。
- 日额度默认 ¥0.06、超时默认 6 秒，使用现有全局配置与费用管理界面热更新；无固定日调用次数。额度不足保留待核验，提额或跨日恢复时后台继续；同一候选版本及证据去重。每轮关系底座只核实已持久化结论的有效性，不发模型请求。
- 隔离验收总额 ¥2。以虚构原话验证准确英译、概括、多段、条件/例外的正向晋升及新会话注入；反向验证自证、无关、一次性情境、否定/反讽/转述、后续纠正、同义改写否决及失效来源。费用管理应能看到已用、剩余、待核验、已晋升与失败原因。

## 当前状态

- 原八项核心修复已通过独立复审。收口仅处理历史恢复、GET Peter 档案鉴权及验证记录；待收口提交复核与 Lan 体验验收，未合并、未部署。

## 验收步骤

1. 在隔离副本打开 Brain「关系与成长」与「Peter 档案」页，确认四区各自显示独立字节预算（`facts 1800 / growth 2000 / relationship 1500 / ongoing 900 B`）；当 `facts` 和 `growth` 接近满载时，仍可新增 `relationship` 与 `ongoing` 条目。
2. 通过真实工具链路提交带有效 `chat:` 或 `event:`/`memory:`（含 `stage-evidence(chat, user)` / `quotes`）的认知，确认晋升为 `confirmed`；提交无证据或仅引用 assistant/tool/自猜的认知，确认保持 `uncertain`。
3. 在管理界面锁定或纠正一条关系认知，确认 Peter 无法覆盖，且下一次对话请求立即使用最新状态；删除一条认知后，旧证据不会将其自动复活。
4. 在「召回诊断」与「关系与成长」中核对：已存储认知、当前有效认知、本轮选中认知、实际注入认知与未注入原因（去重/预算/失效）区分清晰。
