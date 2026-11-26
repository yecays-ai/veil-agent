# P6-S4 消融实验报告｜统一消融执行与候选机制决策（脱敏）

> 工单：P6-S4（docs/tickets/454-p6-s4-ablations.md）｜设计依据：P6-4 第五章实验矩阵、第六章备份契约
> 执行时间：2026-11-26 15:50–17:40（+08:00）｜分支：ticket/454-p6-s4-ablations
> 基线：P6-S1 合并提交 fd59f7a（PR #452 squash），S2 774b4bd、S0 56abe3f；S1 合并后加固 #453 合并 b3d14c2（换模型待索引恢复与诊断透出）。实验在 base main = fd59f7a 的独立 worktree 完成；提交前 rebase 到 origin/main = b3d14c2 并在 rebase 后复跑门禁/单测
> 实验设施：recall-lab/（隔离、只读、可复现）｜聚合结果：recall-lab/results/
> 隐私：本报告只含聚合指标、哈希、计数与匿名化描述；不含真实聊天原文、记忆正文、人设、密钥、本机路径

## 一、结论摘要（给决策者的 5 行）

1. **只有一个检索侧改动通过准入并建议实施**：AB-R1b 中文停用词过滤（K=8 下 Recall@8 +3.88pp、Precision@5 +3.55pp、误注入不变、零额外费用）。AB-COMB 复验该单一胜出项：Recall@8 +3.88pp，无负交互。
2. **判官链路维持现状**：保留 LLM 判官（去判官误报率 100%，阈值门控 81.8%，LLM 判官 45.5%）；超时保持 4s（4s/6s 均 0 次超时）；不放宽 prompt（+4.00pp 召回换来 +18.18pp 误报）；不动 300 字卡片（500 字 +2.00pp 召回但 +33,944 输入 token，且差异小于重跑噪声）。
3. **关系认知侧建议先补证据链、暂不扩权**：当前 64 条 stage-evidence 全是 tool 证据、76 个事件中 0 个能用 Lan 原话/chat 证据严格溯源、quotes 仅 6 条（3 条可逐字核验）、关系区历史 0 条 confirmed 晋升。AB-A2 的 event: 晋升扩展不产生有效晋升，维持 chat-only；循环自证率红线保持 0。
4. **四区独立存储预算建议实施**：learned-context 当前占用 3440/4000 B、余量仅 560 B；按 S3 目标负载前向模拟，共享上限下 2/4 次写入被拒（含 relationship 回填），四区独立（facts 1800 / growth 2000 / relationship 1500 / ongoing 900 B）0/4 被拒；每轮 HEAD 估算 +126 token，约 ¥0.02/日。
5. **Tier 2 只拿到费用/结构侧与微探针证据，未做真实生成盲评**：AB-C1 微探针证实「能力描述放在 HEAD 内变化时前缀缓存命中率 5.9%，移出后 88.4%（稳定时 89.5%）」，方向支持 S7，但需真机复测；C2a/C2b/C3/A1b 只有 token/候选侧测量；Reranker 与认知巩固（AB-E1）因缺 provider/价格/金标准判为阻断。**没有任何 Lan 人工体验验收**。

## 二、Baseline-1 来源与固定条件

| 项 | 值 |
|---|---|
| Baseline-1 来源 | P6-S1「检索、卡片与元数据确定性修复」交付，PR #452，合并/squash 提交 **fd59f7a** |
| 门禁复跑 | scripts/p6_s1_baseline.py，rebase 后 **20/20 检查通过**（S1 交付 18 项 + #453 加固新增 2 项），输出存档 recall-lab/results/baseline-1-gate.json（code_commit=仓库测试时 HEAD e5b3494，生产模块版本 b3d14c2） |
| 隔离快照 brain.db | sha256 2e6dd769a7c3…b3492；241 条记忆（自动召回可见 225：bucket dynamic/permanent 且 type 非隐藏）、76 事件、120 关系、64 条 stage-evidence、6 条 quotes；vectors 241 条 × 1024 维 |
| 隔离 chat.jsonl | sha256 a948c60acdec…bf76；9,529 行（user 3,152 / assistant 6,377），2026-10-19 → 2026-11-25 |
| eval-v2 | 73 条，sha256 c11cff7967e8…a25a0e（见第四节） |
| learned-context | sha256 a3825c0ead91…43f0；version 1，facts 1585 B / growth 1563 B / relationship 292 B / ongoing 0 B（合计 3440 B） |
| 检索固定条件 | 后端 brain；BM25(jieba) + 1024d 余弦；Min-Max 融合 0.65/0.35；importance 加分上限 0.05；K=8（生产默认） |
| 判官固定条件 | deepseek-flash，json_object，thinking disabled，max_tokens 200，timeout 4.0s；候选布局=fused top-8 + vector top-8（生产 recall_judge 布局）；card 300 保守 prompt |
| 价格上界 | 判官 deepseek 峰值 input 2 / cached 0.04 / output 8 CNY per 1M；Embedding BAAI/bge-m3 input 0 CNY per 1M（character.toml 配置价，2026-10-19 核对） |

门禁结论：rebase 到 b3d14c2 后复跑 Baseline-1 全部 20/20 通过（S1 交付 18 项 + #453 加固新增 2 项：换模型待索引恢复前置校验、诊断字段透出）；与 S1 报告一致（CRLF 卡片 20/20、每轮 1 次 Embedding、日期过滤/权重、维度与模型身份、判官诊断、连续性），未发现需先排查的差异。

## 三、隔离执行方式（recall-lab）

- **只读**：brain.db 以 SQLite `mode=ro` 打开；chat/learned-context/context-log 只读复制到 gitignored 的 .local-tmp/s4/。
- **零生产改动**：recall-lab 外未修改任何生产模块；变体（融合/停用词/importance/last_active/情感/related_to/别名/story 索引/判官卡片与 prompt/阈值门控）全部在 recall-lab 内实现或包装。
- **单变量**：每个实验只改一个变量，其他条件按「上一实验的胜出参数」固定；固定基线与变量写入每个结果 JSON 的 fixed_baseline / variable。
- **预算硬控**：append-only JSONL 台账，调用前 reserve（按 token 上界）、调用后按 provider 上报 usage settle；失败调用按预留上界保守结算；Tier 1 ≤ ¥6、Tier 2 ≤ ¥10、总 ≤ ¥20；无法确认价格上界不调用。
- **缓存**：query/story embedding 一次性计算并落本地缓存（22,946 tokens，配置价 ¥0，但调用量与 model 已记账）；判官按 (model,messages,timeout) 哈希缓存，重跑不重复付费。
- **判官样本**：分层抽 36 条（S1 8 / S2 6 / S3 5 / S4 5 / S5 6 / S6 6），固定且可复现；判官指标含候选上限（candidate ceiling recall）以区分「检索没给到」与「判官没选」。
- **延迟口径**：检索延迟为排序耗时（不含缓存 embedding 与 DB 加载）；判官为真实 provider 往返 p50/p95。

## 四、eval-v2 构成与独立标注方法

| 类别 | 样本 | allow_empty=true | 期望 id 数 | 置信度 high/medium/low |
|---|---|---|---|---|
| S1 显式回忆与具体细节追问 | 15 | 0 | 20 | 14/1/0 |
| S2 隐含共情与关系情境 | 12 | 0 | 24 | 6/5/1 |
| S3 跨事件串联与自然联想 | 10 | 0 | 35 | 4/5/1 |
| S4 应空召回噪声/表情包/短消息 | 12 | 12 | 0 | 12/0/0 |
| S5 同词陷阱/工作讨论/报错抑制 | 12 | 12 | 0 | 12/0/0 |
| S6 自主唤醒任务态与闲暇态 | 12 | 4 | 20 | 8/4/0 |
| 合计 | 73 | 28 | 99（去重 55） | 56/15/2 |

- **标注身份**：labeler=ai-assisted，human_confirmed=false。**AI 辅助标注，未经 Lan 人工确认，不是人工金标准。**
- **独立性**：expected id 来自记忆正文/事件 story/聊天上下文的文本阅读；不把 RecallJudge 结果当金标准，不把历史 memory_refs 直接当金标准（其中 12.5% 指向已 tombstone 记忆，只作候选指针）。所有期望 id 已校验存在且非 tombstone。
- **抽样**：分层目的抽样（非随机），六类覆盖 60–80 条目标取 73 条；方法、歧义与偏差来源见准备阶段 annotation-method.md（本地，不进 git）。
- **已知偏差**：测试型 query 占比偏高、工作/技术语料偏多、S3 长线主题不可能穷举、S6 任务态相关性多解、少数样本期望记忆创建于 query 之后（回放为最终快照）。
- **原始样本不进 git**：eval-v2.jsonl 只存在于 gitignored 的 .local-tmp/s4/；仓库仅保留 schema 与聚合结果。

## 五、P6-4 完整实验 ID 覆盖核对表（24/24 有状态）

> 计数口径说明：P6-4 正文写「共 21 项原子实验」、工单描述写「22 个实验 ID」，但第五章矩阵表实际列出 **24 个 ID**（23 个单变量原子实验 + AB-COMB 组合验证）。工单第九节要求「以完整矩阵条目为准，不因报告概述中的数量差异遗漏任何条目」，故本报告逐行覆盖 **24 个 ID**，其中 Tier 0 11 个、Tier 1 6 个、Tier 2 7 个。
> 状态定义：completed=按设计了执行并得到可用结果；partial=只覆盖了声明的一部分（结果里写明覆盖/缺口）；blocked=缺数据/模型/接口/价格上界，附证据；not_run=因预算或时间未执行。**partial/blocked 不计入「完成」**。
> 统计：completed 17、partial 5、blocked 2、not_run 0（24/24 均有状态与理由）。

| ID | Tier | 状态 | 机器判定 recommended | 结论摘要 |
|---|---|---|---|---|
| AB-R1a | 0 | completed | 否 | K 到 30 仍未收敛（Recall@K 单调上升，向量补位占比 22.2%->26.2%）；离线无法确认判官 token/延迟代价，不推荐仅凭检索侧扩大候选池，下游实验用 K=20 折中 |
| AB-R1b | 0 | completed | 是 | 停用词过滤在 K=8 提升 Recall +3.88pp / Precision@5 +3.55pp，门控误注入 +0.00pp；K=20 敏感性见 evidence |
| AB-R1c | 0 | completed | 否 | Min-Max 保持最优或差异不足 1pp |
| AB-R4b | 0 | completed | 否 | S3 跨事件覆盖 +1 条（需 ≥2 才达实收益门槛），平均候选池 +3.6 条 |
| AB-R4c | 0 | completed | 否 | S1/S2 别名补召回 1 条，噪声误触 16/28 |
| AB-R5a | 0 | completed | 否 | 四档中 0.05 综合最优 |
| AB-R5b | 0 | completed | 否 | H2：近 7 天续接增益 +1/53 条，冷门重要记忆保底 +0/0 条；证据不足（冷门重要记忆样本为 0 且增益过小），不足以支持上线 |
| AB-R5c | 0 | completed | 否 | 假设 H1：情感加分对 S2 提升 -2.78pp，S4/S5 误召回 +0.00pp |
| AB-A1a | 0 | completed | 否 | 长尾 imp=8 覆盖 +-10，噪声/工作违和 +0；对话自然度盲评留给 AB-A1b/ Tier 2 |
| AB-A2 | 0 | completed | 否 | 当前数据没有任何 event 能凭 Lan 原话/chat 证据晋升（5/76 事件只有 tool 级请求血缘）；扩展规则不产生有效晋升，建议维持 chat-only，并让 S3 先补 chat 证据链 |
| AB-A3 | 0 | completed | 是 | 共享上限下 2/4 次写入被拒（当前余量仅 560B），四区独立后 0/4 次被拒；HEAD 估算增量 +126.0 tokens |
| AB-R4a | 1 | completed | 否 | story 向量覆盖 76/76 事件；Recall@8 +0.00pp，S1 +0.00pp，跨事件 +0.00pp |
| AB-R2 | 1 | blocked | — | 没有可用的 rerank provider、模型或价格上界；按工单规则不在无法确认价格时发起付费调用 |
| AB-R3a | 1 | completed | 否 | 本样本 4s/6s 均 0 次超时；两臂同 prompt 同候选的判官选择重叠率仅 0.6683，6.3pp 召回差属于模型采样噪声，维持 4s 并记录该限制 |
| AB-R3b | 1 | completed | 是 | 按 (judge_recall - 误报率) 排序，llm_judge 最优；去判官会明显抬高应空误报 |
| AB-R3c | 1 | completed | 否 | 放宽 prompt：整体判官召回 +4.00pp，应空误报 +18.18pp |
| AB-R3d | 1 | completed | 否 | 500 字卡片召回 +2.00pp（同 prompt 重跑噪声 ~6pp 以内视为噪声），输入 token +33944 |
| AB-C1 | 2 | partial | 是 | 能力描述在 HEAD 内变化时缓存命中率 5.9%，移出 HEAD 后 88.4%（HEAD 稳定时 89.5%），方向与 AB-C1 假设一致 |
| AB-C2a | 2 | partial | 是 | 真实 555 次请求中非首轮 332 次；按空召回率 50.4% 估算每窗口节省 6563 tokens |
| AB-C2b | 2 | partial | 是 | 真实诊断里 10/220 次请求注入超过 3 条；按平均记忆长度估算每窗口节省 4606 tokens |
| AB-C3 | 2 | partial | 是 | 75/76 个事件 story 长于摘要+指针，平均每条注入可省 216 tokens；模型主动读全文频次与回复完整度未测 |
| AB-A1b | 2 | partial | 否 | 12 条自主唤醒切片里 0 条闲暇唤醒有可用联想候选；只加在闲暇态，任务态 8 条不受影响。自然度盲评未执行 |
| AB-E1 | 2 | blocked | — | 历史数据无法验证巩固收益：证据链/有效认知样本不足，且真实巩固需要多日前瞻生成与人工验收，超出本次预算与隔离条件 |
| AB-COMB | 2 | completed | 是 | 组合离线 Recall@K +3.88pp（单变量加总 +4.26pp），负交互=no；判官臂费用 ¥0.0000 |

### Tier 1 / Tier 2 的额外说明

- AB-R2：仓库内无 rerank 客户端/模型/价格上界（rep_rerank 实现 0 处、配置无 rerank provider），按工单「无法确认价格上界不调用」判 blocked，未发起任何探测付费请求。
- AB-E1：事件账本 87.3% 是 dream-report（605/693），quotes 6 条、关系区历史 0 条 confirmed 晋升，不存在「巩固前后回复质量」对照标签；真实巩固需多日前瞻生成与人工验收，判 blocked（证据不足），建议 S6 前补数据。
- AB-COMB：Tier 0/1 只有 AB-R1b 一个机制通过准入，组合=该单变量；离线 Recall@8 +3.88pp、负交互=no；判官臂与 AB-R3a 保守臂同配置，命中缓存零新增费用。**因此 AB-COMB 的证明力有限，不能当作「多机制组合无冲突」的证据。**

## 六、Tier 0：零付费离线实验（11/11 completed，¥0）

### 6.1 检索类（R1/R4/R5）

| ID | 臂 | Recall@K | P@5 | NDCG@8 | 门控FPR | 跨事件覆盖 | p50/p95 ms |
|---|---|---|---|---|---|---|---|
| AB-R1a | K=8 | 0.5056 | 0.1378 | 0.4229 | 0.8571 | 0.1 | 1.22/4.03 |
| AB-R1a | K=12 | 0.5444 | 0.1378 | 0.4229 | 0.8571 | 0.3 | 1.23/3.91 |
| AB-R1a | K=20 | 0.6019 | 0.1378 | 0.4229 | 0.8571 | 0.4 | 1.19/3.9 |
| AB-R1a | K=30 | 0.6778 | 0.1378 | 0.4229 | 0.8571 | 0.7 | 1.2/3.95 |
| AB-R1b | K=8 stopwords=off | 0.5056 | 0.1378 | 0.4229 | 0.8571 | 0.1 | 1.26/4.13 |
| AB-R1b | K=8 stopwords=on | 0.5444 | 0.1733 | 0.4809 | 0.8571 | 0.1 | 1.07/3.88 |
| AB-R1c | minmax | 0.5444 | 0.1733 | 0.4809 | 0.8571 | 0.1 | 1.14/3.36 |
| AB-R1c | rrf | 0.5426 | 0.16 | 0.4799 | 0.8571 | 0.2 | 1.11/3.96 |
| AB-R1c | vector | 0.5241 | 0.1689 | 0.4395 | 0.8571 | 0.3 | 1.72/5.24 |
| AB-R4b | related_expand=off | 0.5444 | 0.1733 | 0.4809 | 0.8571 | 0.1 | 1.58/4.45 |
| AB-R4b | related_expand=on | 0.5444 | 0.1733 | 0.4809 | 0.8571 | 0.1 | 1.57/4.45 |
| AB-R4c | alias_expand=off | 0.5444 | 0.1733 | 0.4809 | 0.8571 | 0.1 | 1.71/5.92 |
| AB-R4c | alias_expand=on | 0.5333 | 0.1556 | 0.4796 | 0.8571 | 0.2 | 1.6/5.49 |
| AB-R5a | importance=0.0 | 0.5333 | 0.1511 | 0.4852 | 0.8571 | 0.2 | 1.71/6.26 |
| AB-R5a | importance=0.05 | 0.5444 | 0.1733 | 0.4809 | 0.8571 | 0.1 | 1.02/4.57 |
| AB-R5a | importance=0.1 | 0.5444 | 0.1733 | 0.4809 | 0.8571 | 0.1 | 1.19/3.23 |
| AB-R5a | importance=0.15 | 0.5444 | 0.1733 | 0.4809 | 0.8571 | 0.1 | 1.13/5.04 |
| AB-R5b | last_active_boost=off | 0.5444 | 0.1733 | 0.4809 | 0.8571 | 0.1 | 0.96/4.68 |
| AB-R5b | last_active_boost=on | 0.5389 | 0.1689 | 0.4855 | 0.8571 | 0.1 | 5.96/14.41 |
| AB-R5c | affect_boost=off | 0.5444 | 0.1733 | 0.4809 | 0.8571 | 0.1 | 1.46/4.96 |
| AB-R5c | affect_boost=on | 0.537 | 0.1733 | 0.4771 | 0.8571 | 0.1 | 1.31/6.9 |

要点：
- **AB-R1a**：K 从 8 增到 30，Recall@K 单调上升（50.56% → 67.78%），没有出现设计想要的「收敛拐点」；向量补位占比同时从 20.21% 升到 26.16%。离线无法确认判官 token/延迟代价，机器判定 recommended=false；后续实验固定 K=8（生产基线），不把 K=20/30 当作胜出参数。
- **AB-R1b**：K=8 下停用词开启：Recall@8 50.56% → 54.44%（+3.88pp），Precision@5 13.78% → 17.33%（+3.55pp），NDCG@8 42.29% → 48.09%，门控误注入不变（85.71% → 85.71%），trap 查询平均 token 45.67 → 34.25。K=20 敏感性：Recall@20 60.19% → 65.00%（+4.81pp），方向一致（gated FPR 均 85.71%）。机器判定 recommended=true。**注：FPR 的 85.71% 是「离线统一门控」下的值，不代表生产判官后的误报；它跨所有 Tier 0 臂相同，只用来看相对变化。**
- **AB-R1c**：Min-Max 54.44% / RRF 54.26% / 纯向量 52.41%，差异 <1pp，机器判定维持 Min-Max。
- **AB-R4b**：related_to 一跳把候选池平均从 20 条扩到 23.6 条（+3.6），S3 跨事件覆盖 +1 条、候选池期望命中 +1，未达「≥2 条覆盖」实收益门槛，不推荐。
- **AB-R4c**：别名补召回 1 条，但工作/自主查询误触 16/28（自动抽取的别名字典含大量通用中文/英文词）；原「豆辞典」数据源在仓库不存在，本次用标题/标签/关键词自动抽取替代，证据缺口已记录。不推荐。
- **AB-R5a**：importance 加分 0 / 0.05 / 0.10 / 0.15 四档，重要老记忆命中完全一致（7/9），综合指标 0.05 最优，维持现网。
- **AB-R5b**：last_active 微调在 53 条近因期望上 +1 条命中，冷门重要记忆样本 0 条、无法验证保底；证据不足，不推荐上线。
- **AB-R5c**：valence/arousal 同向加分对 S2 共情召回 -2.78pp（噪声范围内），误注入不变；假设 H1 未获支持，按设计在 Tier 0 否决、不进 Tier 2。

### 6.2 联想与认知治理（A1a/A2/A3）

| ID | 臂 | 关键指标 |
|---|---|---|
| AB-A1a | breath8 | 联想生成 37 条 / 唯一记忆 31 / imp=8 覆盖 0.2111 / 噪声违和 12 |
| AB-A1a | stratified24 | 联想生成 39 条 / 唯一记忆 11 / imp=8 覆盖 0.1 / 噪声违和 12 |
| AB-A2 | chat_only (current) | confirmed 晋升 0 / 循环自证率 0 / 可核验 quote - |
| AB-A2 | event_pointer_naive | confirmed 晋升 76 / 循环自证率 1 / 可核验 quote - |
| AB-A2 | event_stage_evidence_chat (candidate, strict) | confirmed 晋升 0 / 循环自证率 0 / 可核验 quote 3 |
| AB-A2 | event_request_lineage (weaker proxy) | confirmed 晋升 5 / 循环自证率 1 / 可核验 quote - |
| AB-A3 | shared_4000B | 写入被拒 2/4 / 拒绝率 0.5 |
| AB-A3 | four_zone_independent | 写入被拒 0/4 / 拒绝率 0 / HEAD +126.0 tok |

- **AB-A1a**：全库分层轮换池（24 条，pinned+imp>=7）相对 breath(8)：联想生成 38→39 条、唯一记忆 32→11 条、imp=8 长尾覆盖 16.67%→10.0%、噪声违和 11→12。池变大反而让高重要度记忆反复胜出，长尾覆盖下降；不推荐。
- **AB-A2**：严格规则下 0/76 事件可凭 chat/stage-evidence(chat)/quotes 溯源晋升；若采用「任何 event:/memory: 指针即晋升」的朴素规则，循环自证率=100%；采用「event 需可追溯到 Lan 原话」的候选规则，可晋升 0 条、自证率 0。当前数据不支持扩权；建议 S3 先补 chat 证据链（stage-evidence 目前全部是 tool 类型，quotes 6 条中 3 条文本能在真实用户消息中逐字命中）。
- **AB-A3**：共享 4000B 上限下前向模拟 4 次写入被拒 2 次（relationship 回填 4768B、ongoing 4160B 超限）；四区独立（facts 1800 / growth 2000 / relationship 1500 / ongoing 900 B）0/4 被拒；HEAD 渲染增量 facts+growth +126 token 估算。机器判定 recommended=true。历史最大占用 facts 3300B 说明 facts 区曾逼近共享上限，是共享挤压的直接证据。

## 七、Tier 1：检索与判官实测（5 completed + 1 blocked，结算 ¥1.539940）

### 7.1 AB-R4a events.story 纳入向量索引（completed）

- 76/76 事件 story 一次性嵌入（22,946 token 中约 19.5k 为此项，配置价 ¥0），story 平均 961 字（197–2,788），69/76 来自 final-import。
- story 索引关/开：整体 Recall@8 54.44% → 54.44%（+0.00pp），S1 +0.00pp，S3 跨事件覆盖不变，p50/p95 1.1/3.4ms → 1.1/3.9ms，费用 ¥0。
- **结论：无收益，不推荐**（与「story 是长文本、向量检索已由 memory body 覆盖」一致；分来源看 final-import 占 69 条，story 语义与 memory 高度重叠）。

### 7.2 AB-R2 Reranker（blocked）

- 仓库内没有任何 rerank 客户端实现（检索 0 处）、没有配置 rerank provider/model、没有可确认的价格上界；OpenAI 兼容 chat 端点不提供 rerank API。
- 按工单「无法确认价格或费用上界时，不执行该付费调用」，未发起探测请求。**Blocked，证据见 recall-lab/results/ab_r2.json。**

### 7.3 AB-R3a 判官超时 4.0s vs 6.0s（completed）

| ID | 臂 | 判官召回 | 判官精确率 | 误报率 | 超时 | 付费调用 | 输入 token | 费用 ¥ | p50/p95 ms |
|---|---|---|---|---|---|---|---|---|---|
| AB-R3a | timeout=4.0s | 0.5533 | 0.3808 | 0.5455 | 0 | 0 | 66892 | 0 | 748.83/1855.01 |
| AB-R3a | timeout=6.0s | 0.5967 | 0.4109 | 0.5455 | 0 | 0 | 66892 | 0 | 805.77/1179 |
| AB-R3b | llm_judge | 0.5967 | 0.4109 | 0.5455 | 0 | 0 | 66892 | 0 | 805.77/1179 |
| AB-R3b | no_judge_top3 | 0.5433 | 0.32 | 1 | 0 | None | 0 | 0 | 0/0 |
| AB-R3b | threshold_gate_0.45 | 0.4367 | 0.2667 | 0.8182 | 0 | None | 0 | 0 | 0/0 |
| AB-R3c | conservative | 0.5533 | 0.3808 | 0.5455 | 0 | 0 | 66892 | 0 | 748.83/1855.01 |
| AB-R3c | relaxed | 0.5933 | 0.3071 | 0.7273 | 0 | 0 | 67216 | 0 | 680.14/1149.57 |
| AB-R3d | card=300 | 0.5533 | 0.3808 | 0.5455 | 0 | 0 | 66892 | 0 | 748.83/1855.01 |
| AB-R3d | card=500 | 0.5733 | 0.3707 | 0.5455 | 0 | 0 | 100836 | 0 | 774.01/1284.01 |

- 两臂均 0 次超时；p95 1,308.59ms（4s）vs 1,008.10ms（6s）。**但「同 prompt 同候选」的两臂判官选择重叠率只有 0.6683，6.3pp 的召回差属于 deepseek-flash 采样噪声**，不能归因于超时。
- **结论：维持 4.0s**；若真实线上出现 timeout:model 压力样本，需先用真实诊断回放确认，不凭离线猜测放宽。

### 7.4 AB-R3b 判官角色：LLM vs 无判官 vs 阈值门控（completed）

- LLM 判官：judge_recall 57.00%，误报率 45.45%；无判官取 fused top3：50.67%，误报率 100%；阈值门控（余弦≥0.45，最多 3 条）：43.67%，误报率 81.82%。
- **结论：保留 LLM 判官**（去掉判官会让应空噪声 100% 被注入；阈值门控替代不了判官）。LLM 判官实付约 ¥4.33/千轮（按本次实际调用结算换算，见第十节）。

### 7.5 AB-R3c 判官 prompt：保守 vs 放宽（completed）

- 保守：召回 60.33%（4s 臂）/ 57.00%（6s 臂，缓存复用）；放宽：召回 57.67%，误报率 54.55%→72.73%（+18.18pp）。
- **结论：不放宽**，放宽 prompt 用更高的误报换不来稳定召回提升。

### 7.6 AB-R3d 判官卡片：300 vs 500 字（completed）

- 300 字：召回 60.33%；500 字（含 why_remembered/importance/story 摘要）：召回 62.33%（+2.00pp），输入 token 65,176 → 99,120（+33,944，约 +45%）。
- **结论：不动 300 字卡片**。+2pp 小于同 prompt 重跑噪声（AB-R3a 实测重复臂差异可达 6pp），却固定多付约 45% 判官输入 token。

## 八、Tier 2：回复体验与上下文成本（1 completed + 5 partial + 1 blocked，结算 ¥0.084864）

| ID | 臂 | 关键指标 |
|---|---|---|
| AB-C1 | stable_head_1 | {"cached_ratio": 0.0, "cached_tokens": 0, "error": "", "prompt_tokens": 2146} |
| AB-C1 | stable_head_2 | {"cached_ratio": 0.8947, "cached_tokens": 1920, "error": "", "prompt_tokens": 2146} |
| AB-C1 | changed_in_head | {"cached_ratio": 0.0591, "cached_tokens": 128, "error": "", "prompt_tokens": 2167} |
| AB-C1 | moved_head_1 | {"cached_ratio": 0.0, "cached_tokens": 0, "error": "", "prompt_tokens": 2172} |
| AB-C1 | moved_head_2 | {"cached_ratio": 0.884, "cached_tokens": 1920, "error": "", "prompt_tokens": 2172} |
| AB-C2a | omit_empty_hint | {"estimated_empty_occurrences": 167, "non_first_steps": 332, "saved_bytes": 21877, "saved_tokens_est": 6563.1} |
| AB-C2b | cap_top2+1 | {"excess_memories": 18, "requests_over_cap": 10, "saved_cny_per_1k_turns_est": 9.2124, "saved_tokens_est": 4606.2} |
| AB-C3 | full_story | {} |
| AB-C3 | summary_pointer | {"avg_saved_tokens_per_long_event": 216.4} |
| AB-A1b | association_off | {"associations_proposed": 0, "idle_wakes": 4} |
| AB-A1b | association_on | {"associations_proposed": 0, "idle_wakes": 4, "task_wakes_untouched": 8} |

- **AB-C1（partial）**：真实 deepseek-flash 前缀缓存微探针（5 次真实调用、按 system.facts 的「静态 head + 可变 capability + 长静态后缀」布局）：HEAD 稳定时缓存命中率 **89.47%**；能力描述在 HEAD 内变化时骤降到 **5.91%**；把能力描述移到 runtime 消息、HEAD 保持稳定时回到 **88.40%**。生产 context-log 555 次请求实测缓存命中（tool_step_context）70.36%，tool_schema 平均 38,108 字节。**方向与 AB-C1 假设一致，但这是微探针不是完整生产请求组装，status=partial，需 S7 真机复测。**
- **AB-C2a（partial）**：真实 555 次请求中非首轮 332 次；按空召回率 50.45% 估算，省略空提示每观测窗口（约 4 天）省约 6,563 token（约 ¥0.013/req 换算），日均约 1.5k token。**幻觉回忆率必须=0 的红线未验证（未做真实生成），不能据此直接实施。**
- **AB-C2b（partial）**：真实 recall-diagnostics 220 次请求里 10 次注入 >3 条（超出 18 条记忆）；按平均记忆 255.9 token 估算每窗口省约 4,606 token，日均约 1.0k token。**短闲聊陪伴自然度盲评未执行。**
- **AB-C3（partial）**：75/76 事件 story 长于「标题+摘要+timeline 指针」，平均每条注入可省 216 token；15 条 eval 样本涉及长事件。**模型主动读全文频次与回复完整度未测。**
- **AB-A1b（partial）**：4 条闲暇唤醒、8 条任务唤醒；用 A1a 池离线模拟，0 条闲暇唤醒产生联想候选——当前 natural_association 的 light_chat 门控（≤40 字且非工作话题）会直接挡掉自主唤醒的长上下文，说明该分支需要独立的 wake 侧门控设计。**自然度盲评未执行。**
- **AB-E1（blocked）**：见第五节说明。
- **AB-COMB（completed，证明力有限）**：唯一胜出项为停用词；组合离线 Recall@8 50.56%→54.44%（+3.88pp），单变量加总 +4.26pp，负交互=no；判官臂命中缓存零新增费用。**该结果只验证「停用词单独加入不产生负交互」，不能证明多机制组合。**

## 九、预算明细与日均费用估算

### 9.1 实际结算（append-only 台账，全部调用含开发期修复重跑）

| Tier | 实验/类别 | 结算调用 | 费用 ¥ |
|---|---|---|---|
| 1 | AB-R3a | 178 | 0.684200 |
| 1 | AB-R3c | 89 | 0.345742 |
| 1 | AB-R3d | 89 | 0.509998 |
| 1 | PREP-embedding | 14 | 0.000000 |
| 2 | AB-C1 | 9 | 0.084864 |
| — | **合计** | **379** | **1.624804** |

- **总支出 ¥1.624804 / 硬顶 ¥20（8.1%）**；Tier 1 ¥1.539940 / 上限 ¥6（25.7%）；Tier 2 ¥0.084864 / 上限 ¥10（0.85%）；Tier 0 ¥0 / ¥0。
- 台账 379 条结算：判官付费 356 次、C1 微探针 9 次（其中 4 次因 prompt 未包含 json 关键字被 provider 拒绝，按工单「失败请求计入预算」按预留上界保守结算 ¥0.0628，根因修复后 5 次成功 ¥0.0221）、embedding 14 条（配置价 ¥0，22,946 token）。
- **说明**：判官 356 次中有约 212 次来自 harness 修复期间的重复重跑（每次重跑均因单变量基线变化导致缓存未命中），最终配置对应的有效判官调用约 144 次（¥0.62）。工单要求失败与重试均计入预算，故台账如实保留全部支出；**没有为省预算而跳过必要对照臂**。
- 预算守卫测试：Tier/总上限在 reserve 阶段硬拦截（合成单测覆盖），本轮无触发超限调用。

### 9.2 每千轮与日均费用估算（按生产 82 轮/日、deepseek 峰值价 2/0.04/8 CNY per 1M）

| 项目 | 单轮增量 | 82 轮/日 | 月（30 日） | 说明 |
|---|---|---|---|---|
| 判官（现状，参考） | ¥0.00433 | ¥0.355 | ¥10.7 | 现网已开启；本次实测 356 次付费调用均值，输入约 2,110 token/次 |
| AB-R1b 停用词 | ¥0 | ¥0 | ¥0 | 纯本地 BM25 过滤，无模型调用 |
| AB-A3 四区独立预算 | +126 token HEAD | ¥0.0207 | ¥0.62 | facts+growth 渲染增量估算 |
| AB-C1 HEAD 纯化（方向） | 能力变更时恢复缓存 | ≤¥0.07 | ≤¥2.0 | 21 次能力变更/10 天；每次变更若打断一次约 2 万 token 的缓存前缀 |
| AB-C2a 空提示收敛 | 约 1.5k token | ¥0.003 | ¥0.09 | 仅结构侧估算 |
| AB-C2b 短闲聊上限 | 约 1.0k token | ¥0.002 | ¥0.06 | 仅结构侧估算 |
| AB-C3 story 摘要+指针 | 约 1.0k token（按 5 条/日） | ¥0.002 | ¥0.06 | 仅结构侧估算 |
| AB-E1 巩固（若未来实施） | 1 次/夜约 1 万 token | ≈¥0.03 | ≈¥0.9 | 需先补数据与开关/日预算，本次不实施 |
| **本次建议实施合计** | — | **≈¥0.02/日** | **≈¥0.6/月** | 只计 AB-R1b + AB-A3；C 系列属 S7 待复测 |

## 十、未执行、失败与证据不足清单（对决策的影响）

| 项 | 状态 | 缺什么/为什么 | 对决策的影响 |
|---|---|---|---|
| AB-R2 Reranker | blocked | 无 rerank provider/模型/价格上界；不探测付费未知价 | S5 不引入 Reranker；若未来接服务需先补价格与延迟对照 |
| AB-E1 认知巩固收益 | blocked | 无「巩固前后回复质量」金标准；账本 87.3% 是恢复报告；真实巩固需多日生成+人工验收 | S6 暂不启动巩固；先补数据与开关/日预算设计 |
| AB-C1 | partial | 微探针非完整生产请求（未加载人设/全量工具 schema/真实 history） | S7 方向可信但需真机复测后再实施 |
| AB-C2a / C2b / C3 | partial | 只有 token/结构侧证据，无真实生成；幻觉率/自然度/回复完整度未验证 | S7 若要实施，必须先做小样本真实生成盲评（准入红线：幻觉=0） |
| AB-A1b | partial | 0 条闲暇唤醒候选（light 门控挡住长上下文），无盲评 | 自然联想/自主唤醒分支需重新设计门控后再测；暂不实施 |
| AB-A2 | completed 但证据不足 | 关系区历史 0 条 confirmed 晋升；stage-evidence 全 tool；quotes 仅 3/6 可核验 | 维持 chat-only；S3 先补证据链，循环自证红线仍为 0 |
| AB-A1a / R4b / R4c / R5b | completed 但不推荐/证据不足 | 效应量小或误触高；A1a 长尾覆盖反降 | 不进入 S5/S6 施工清单 |
| 真实回复体验盲评（Tier 2 全量） | not_run | 预算/时间只够做 1 个微探针 + 结构测量；无 Lan 人工验收 | 任何「体验变好」的说法都**不成立**；Tier 2 结论一律标 AI 辅助、未确认 |

## 十一、隐私与数据安全声明

- 未修改正式运行目录 E:/Peter-Agent/VEIL/veil-agent 下任何文件；未启动/停止正式 Peter 进程；未向正式 Peter 发消息；未写入 data/peter。
- 真实数据只在本地 gitignored 的 .local-tmp/s4/ 与只读源 .local-tmp/s4-prep/ 使用；eval-v2 原始样本、chat、memory 正文、learned-context 内容均未提交 git。
- 提交内容只有：工具代码、schema、聚合指标、哈希、计数、匿名化描述；报告与结果 JSON 均搜过本机路径/密钥/真实记忆关键词（0 命中）。
- 判官/embedding 调用使用只读 .env 中的 key，key 不落盘、不进日志/结果；付费调用全部经过预算台账。

## 十二、复现步骤

1. 从只读准备产物复制 eval-v2 与隔离快照到 worktree 的 .local-tmp/s4/（该目录 gitignored）。数据库 sha256、chat sha256、eval sha256 见 run-manifest.json。
2. 复跑 Baseline-1 门禁：python scripts/p6_s1_baseline.py --out .local-tmp/s4/baseline-1-gate.json，确认 18/18 通过。
3. 运行实验（详见 recall-lab/README.md 完整命令）：tier 0 → tier 1 → tier 2；结果写 recall-lab/results/，每样本明细写 gitignored 的 --detail-dir。
4. 合成单测：python -m unittest discover -s recall-lab/tests -v（23 项，无网络/无真实数据）。
5. 对照本报告覆盖表逐一核对 recall-lab/results/coverage.json（24 个 ID 的 status/recommended/reason）与各实验 JSON。

## 十三、交付物

- recall-lab/：harness、变体、预算台账、指标、23 项合成单测、schema、README
- recall-lab/results/：24 个实验聚合结果 + coverage.json + run-manifest.json + baseline-1-gate.json + budget-summary.json（预算聚合；原始 append-only JSONL 留在本地 .local-tmp，不进 git）
- docs/reports/p6-s4-ablations-20261126.md（本文件）
- docs/reports/p6-s4-decision-table-20261126.md（Lan 白话决策表）
- docs/tickets/454-p6-s4-ablations.md、STATUS.md、HANDOFF.md 更新
