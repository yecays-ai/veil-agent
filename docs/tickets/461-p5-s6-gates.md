# P5 遗留与 S6 开工门禁｜健康巡检补齐、聊天证据索引与写入幂等

- **工单编号**：461（分支 `ticket/461-p5-s6-gates`，worktree `worktrees/461-p5-s6-gates`）
- **基线提交**：`main = f979ca2`（PR #460 已合并；PR #461 / P6-S5 仍开放，本单与它按文件隔离并行）
- **工单类型**：功能开发 + 工程加固（沿用现有 Brain 健康巡检、关系认知治理、聊天证据链与桌面 Brain 页面）
- **做完后 Lan 能多做的一件事**：在 Brain 健康页按日期看"这段时间新增了什么、哪里重复/碎片/断链/索引不同步"，全库体检与期间变化分开显示；向量检查不再写死 1024 维，换 Embedding 模型后能看懂索引状态。同时长聊天历史下 Peter 的每轮记忆核验不再越用越慢，写认知/待办的重试不会重复落盘。

## 一、施工边界

- 本单只补 P5 健康巡检遗留与 S6 五项开工门禁，不实施 S6 的权重演化、衰减、认知巩固或自主联想功能。
- 复用 `veil/brain/brain_health.py`、`veil/brain/learned_context.py`、`veil/brain/relationship_semantic.py`、现有 Brain API / 桌面 Brain 页面 / 定期巡检调度，不建平行系统。
- 不触碰正式 `data/peter`、不启停正式 Peter、不部署；全部验证使用隔离临时目录与合成数据。
- 与 PR #461（P6-S5）共享的文件：`desktop/workspace-brain.js`、`veil/brain/brain_p4_api.py`、`veil/core/veil_agent.py`、`STATUS.md`、`HANDOFF.md`、`docs/ACCEPTANCE.md`。本单只改健康页区域、健康 API 与证据链，不改 S5 的检索、判官、联想逻辑；合流时保留双方改动并重跑联合回归。

## 二、P5 健康巡检补齐

1. **孤立记忆与孤立事件**：区分"悬空引用"（指向已删除/不存在对象）与"无关联记录"（没有任何事件、来源、引述或 sidecar 关联），逐条展示对象、判断依据与所属类别；只报告，不自动删除、合并或归档。
2. **向量维度适配**：移除健康巡检中写死的 1024 维；读取 `meta.embedding_model` 与配置的 Embedding 模型身份，按库内实际维度分布和 `vectors.dim` 做自洽校验，展示模型身份、维度分布与索引状态；不因换模型/不同维度误判。
3. **时间范围体检**：指定日期段内检查记忆/事件增长、重复、碎片、断链与索引异常；明确区分"全库当前状态"与"指定期间变化/异常"；无历史快照或缺少创建时间时如实标记，不虚构历史状态。健康结果接入现有 Brain 页面、定期巡检与一键复制报告。

## 三、S6 前置工程加固

1. **聊天历史索引**：`_chat_user_index` 改为增量读取（追加只解析新字节）；缓存有明确上限，旧证据按 ID 按需查询；文件被删除、替换、截断、同长度改写或召回后不得命中旧缓存。
2. **证据核验性能**：`event_ledger` / `quotes` / `sources` 的固定窗口改为有界分页/精准查询；一次核验（`build_prompt` / 语义队列 / 状态汇总）共用一个墙钟预算；SQLite 争用时超限保守降级为"不可用"，不误晋升、不阻断聊天。
3. **反证长期积累**：反证查询不再随聊天全量增长；载荷按文本去重、有界，超限时不静默截断，标记 `counterevidence_overflow` 并保持未确认；相关否决、锁定与纠正不被容量裁掉。
4. **跨语言同义否决**：保留原文锚点拦截；无共同文字锚点的译写由受预算约束的语义核验判断；增加无关正例晋升、翻译否决拒绝、用户显式恢复旧认知的验证。不要求任意改写 100% 识别，不因安全退回全量拒绝。
5. **写入幂等**：facts/growth 追加与修订、relationship 追加与修订、ongoing 新增使用持久化幂等键；丢响应、并发重试、进程重启后同一操作只落一条正文/条目/版本记录，返回原结果与原 ID。

## 四、验收要点

- 保留 S3 的四区预算、语义晋升、用户锁定/否决/恢复与实际注入，不退化。
- 隔离数据覆盖：长历史连续追加、数据库争用、证据失效、跨语言否决、并发重试、768/1024/1536 维向量、日期筛选。
- 正常认知仍能自主提取、自动晋升并进入新会话；不以全部拒绝换安全。
- 确定性问题用本地测试；语义判断用受预算约束的真实模型实验（隔离数据，费用上限 ¥2）。
- 交付源码位置、测试证据、实际运行结果、剩余能力边界；独立审核与 CI 通过后停在可合并状态。

## 当前状态

- 开发与隔离验证完成（PR #462），待独立审核 / CI / Lan 体验；未合并、未部署，未改正式 `data/peter`。
- 基线 `main = f979ca2`（PR #460 已合并）；PR #461（P6-S5）随后合并为 `504c0a7`，本单已合流并完成联合回归（126 项定向 + 58 项桌面 + 2469 项完整套件，失败集合与基线一致）。
- 分支 `ticket/461-p5-s6-gates`，worktree `worktrees/461-p5-s6-gates`。
- 与 PR #461（P6-S5）共享 `desktop/workspace-brain.js`、`veil/brain/brain_p4_api.py`、`veil/core/veil_agent.py`、三份状态文档；本单只改健康页/健康 API/证据链，未改 S5 的检索、判官、联想逻辑。

## 交付位置

### P5 健康巡检

- `veil/brain/brain_health.py`
  - H-3：悬空引用（sidecar/relations/合并指针）与无关联记忆/事件的精确反连接核验，逐条带 `category` / `basis`，只报告不删除、不合并。
  - H-7：删除 1024 维硬编码；按 `meta.embedding_model`、角色配置的 Embedding 模型身份、库内 `vectors.dim` 分布与 `length(vector)` 自洽校验；采样余弦也使用每行记录的维度。
  - `period`：指定日期段的记忆/事件/阶段/版本/认知版本增长、重复、碎片、悬空引用、索引异常；每个检查带 `scope`（`current` / `current+range` / `range`）。
  - `historical_state`：只使用 `health-reports/health-snapshots.jsonl`；无快照明确写 `no_snapshot_in_range`，不把今天的数字回填成历史状态。
  - `export_report`：新增“指定期间变化与异常（与全库当前分开）”与“数据范围”。
- `veil/server/web_agent.py`：健康扫描把角色配置的 Embedding 模型身份传入；定期巡检同样传入。
- `veil/brain/brain_p4_api.py`：成长报告的关联健康摘要增加期间结果与历史证据边界。
- `desktop/workspace-brain.js`：健康页新增期间卡片、每项“数据范围”、证据判断依据；一键复制/导出沿用现有出口。
- 测试：`tests/test_p5_health_period_orphans.py`（7 项），`desktop/tests/health-period.test.cjs`。

### S6 开工门禁

- `veil/brain/learned_context.py`
  - 聊天证据索引：`_chat_user_index` / `_ensure_chat_state` / `_parse_chat_append` 增量读取；400 条文本 LRU；`_chat_lookup` 旧证据按 ID 按需查询；文件删除、截断、替换、同长度改写（5 个 64B 指纹 + mtime）失效重建；重复/召回消息不作为证据。
  - 证据预算：`_EvidenceBudget` 一次核验共享墙钟预算（默认 1200ms，可由 `profile.evidence_budget_ms` 100–10000 覆盖）；`event_ledger` / `quotes` / `sources` 分页（200/50 行、最多 5/4/4 页），超时或窗口截断标 `unavailable`，不判“不存在”。
  - 反证：按规范化文本去重；`COUNTEREVIDENCE_LIMIT=120` 超限返回 `counterevidence_overflow`，不截断、不晋升；更正索引随聊天增量维护。
  - 幂等：facts/growth 追加与修订、relationship 追加与修订、ongoing 新增/更新/完成/重开均带持久化回执；回执随正文同一事务提交，带状态守卫（删除/完成/编辑后不返回旧成功）；重试/并发/重启返回原结果与原 ID，不重复写正文、条目或版本。
- `veil/brain/relationship_semantic.py`：接受单层 Markdown code fence 包裹的严格 JSON，其他包装仍判无效。
- 测试：`tests/test_p6_s6_chat_evidence_budget.py`（11 项，含真实 SQLite 排他锁、旧窗口外证据、截断降级）、`tests/test_p6_s6_idempotent_writes.py`（7 项）、`tests/test_p6_s6_cross_language_veto.py`（4 项）。

### 实验与实测

- `docs/reports/461-cross-language-live-20261127.md`：VEIL Lab 合成 run `461-sem-synthetic-20261127T210905`，DeepSeek 真实调用 2 次，本 run ¥0.00236848，累计本单语义探针 ¥0.0509（≤¥2）。翻译候选 contradicted 不注入；无关正例 supported 并注入。
- `docs/reports/461-chat-index-benchmark-20261127.json`：5 万条 / 6.68MB 合成聊天，首建 0.256s、重复调用 0.435ms、追加一条 0.824ms；旧证据按需查询 1–11ms；LRU 上限 400。
- 合流后：126 项联合定向回归通过，桌面 58/58 通过，完整 Python 套件 2469 项中 18 失败 + 3 错误，失败集合与 `origin/main` 基线完全相同；详见 `docs/reports/461-test-evidence-20261127.md`。

## 剩余能力边界

- 聊天证据索引首次建立仍需顺序读完现有 `chat.jsonl`（一次性 0.256s/6.68MB）；索引不跨进程持久化，重启后首次核验重建。
- 同长度原地改写若恰好只落在 5 个采样窗口之外、且 mtime/size 都不变，理论上仍可能命中旧缓存；正常文件写入、删除、截断、替换和两端改写均会失效。该边界与现有 `Store.incremental_state` 一致。
- 反证超过 120 条唯一文本时安全降级为 `counterevidence_overflow`，需要 Lan 清理/合并旧否决后才能继续自动核验；不会静默丢弃。
- 健康巡检只能依据库内时间字段与既有每日快照判断“期间变化”；没有快照时不能还原当时的整体健康状态。
- 跨语言识别由受预算约束的语义核验判断，不承诺任意译写 100% 识别；模型失败时保留 `uncertain`。
- 本单未实施 S6 的权重演化、衰减、夜间巩固、自主闲暇联想；未验证主聊天模型自行提取候选的完整链路。

## 验证命令与结果

```
python -m unittest tests.test_p5_health_period_orphans tests.test_p6_s6_chat_evidence_budget \
  tests.test_p6_s6_idempotent_writes tests.test_p6_s6_cross_language_veto -v   # 29 项通过
python -m unittest tests.test_relationship_422 tests.test_p6_s3_relationship_governance \
  tests.test_pr460_sovereignty tests.test_relationship_semantic_worker \
  tests.test_p6_s5_retrieval_judge_association                                 # 合流后联合 126 项通过
node --test desktop/tests/*.test.cjs                                          # 58/58 通过
```

## 验收步骤

1. Brain → 健康：不填日期运行一次，确认 H-1~H-9 显示为"全库当前"；填一个历史日期段再运行，确认出现"指定期间"增长/重复/碎片/断链/索引结果，且没有历史快照时明确写"缺少历史证据"。
2. 健康页核对向量卡片：显示当前 Embedding 模型身份、维度分布与索引异常；用 768/1024/1536 维的隔离库各运行一次，不出现"维度 ≠ 1024"误报。
3. 点"复制巡检报告"，确认期间结果与全库结果都在文本里，报告写明只读、耗时与数据范围。
4. 隔离副本中长历史连续追加聊天后连续触发记忆核验，确认不随历史长度线性变慢；锁库时整次核验在预算内结束并降级，不阻塞聊天。
5. 同一写入操作重复提交（模拟丢响应/并发/重启），确认只有一条正文/条目/版本，返回同一个 ID。
6. 跨语言否决与无关正例按 `docs/reports/461-*` 的实验记录验收；用户显式恢复旧认知后能重新注入。
