# 452 · P6-S1：检索、卡片与元数据确定性修复

- **属于哪一格**：A 陪伴核心（记忆注入与精修）。
- **做完 Lan 能多做的一件事**：问起 11 月初某件旧事的结尾细节时，Peter 能凭完整记忆卡片正确想起；用日期范围主动检索也能按时间找到；同样的对话每轮只付一次 Embedding 费用。
- **她怎么验收**：正常聊一段后问“你还记得 11 月初那次……后来怎么了吗”，看 Peter 是否接上结尾细节；让他用日期范围查一次旧事；观察回复变快、记忆不重复计费（P4 召回诊断里 Embedding 一次/轮）。

## 目标

在 P6-S2 已合并的单事务写入与单份版本契约之上，修掉检索链路里已经确认的确定性缺陷，形成 P6-S4 可直接使用的 Baseline-1：CRLF 卡片与元数据兼容、同轮单次 Embedding、主动检索日期与配置权重、动态向量维度与模型身份保护、判官独立超时与显式降级、移除 reuse_recall() 与随机 drift 死代码，并保持跨轮承接、冷却、轮内去重与记忆连续性不变。

## 当前状态

- 已完成代码修复与 17 项新增回归测试，全部历史相关套件与 main 基线失败集一致（无本单引入的回归）。
- Baseline-1 已用隔离假数据/假模型实测 18/18 项检查通过：CRLF 卡片正文可见 20/20（修复前仅 6/20 的 LF 样本可见）、每轮 Embedding 1 次（旧路径 2 次）、日期过滤与配置权重生效、维度/模型不兼容时降级并留诊断、判官超时按 embedding/model 分开记录、跟进承接与冷却保持。
- 未改表结构、未批量改写历史记忆、未调用正式数据或付费接口。

## 验收步骤

1. 正常聊几轮后，问一件 10 月底至 11 月中旧事的结尾或细节，确认 Peter 记得完整经过而不只记得标题。
2. 让 Peter 主动用日期范围检索（例如“帮我找 11 月 10 日到 11 月 20 日之间我们聊过的某件事”），确认只返回该时间段内的记忆。
3. 在 P4 召回诊断/健康页确认同一轮只有一次 Embedding 记录；判官失败时能看到 embedding / model 分开的原因与降级路径，而不是统一显示“超时”。

## 影响与验证

| 用户场景 | 代码证据 | 预期与验证 |
|---|---|---|
| 早期 CRLF 记忆被召回 | recall_judge.split_card()/card_text()、brain_writer._with_metadata() | 卡片含标题/关键词/开头/结尾；元数据更新不再丢 body，也不批量重写历史文件 |
| 普通聊天自动召回 | Memory.context() 共享 judge.prefetch() 的 query_vector 给 search_candidates() | 同轮只 Embedding 一次、只结算一次；判官未启用时检索自己 Embedding |
| 主动日期检索 | memory_native._search → BrainAuthority.breath_search → brain_retrieval.search_text | date_from/date_to 按创建日包含边界；非法日期拒绝；权重读 [memory.retrieval] |
| 换 Embedding 模型/维度 | brain_retrieval.search()、brain_writer._vector_compat()、meta.embedding_model | 维度不同跳过、模型不同降级关键词并留诊断；不混写向量、pending 恢复保留 |
| 判官失败/超时 | recall_judge.choose() 分离 embedding/model 计时 | 正常空召回、embedding 失败、判官超时、自主活动池回退可区分 |
| 跨轮承接/去重 | Memory.context() 保留 followup carry/cooldown/dedupe | 移除 reuse_recall() 与 drift 死分支后，承接、冷却与轮内去重不退化 |
