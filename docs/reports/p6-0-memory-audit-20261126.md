# P6-0｜开源记忆认知机制深度调研与差距审计报告（第一轮补充修订版）

> 审计日期：2026-11-26  
> 审计范围：只读审计 `VEIL/veil-agent` 及 9 个开源记忆/检索项目；不修改正式功能代码、记忆数据及运行配置，不启动消融实验或正式施工。  
> 核心原则：**先通过单变量对照与消融实验确认实际收益，再决定是否增加系统复杂度；调研服务于“让 Peter 像人一样自然记忆并畅游电脑”，帮助 VEIL 做减法与精准升级。**

---

## 一、审计基线、本地未提交边界与三层证据标准

### 1.1 `main` 基线与 P3-4 本地未提交状态边界
- **Git `main` 基线**：`160b0ec`（*P3-4：同步维护窗口清理兼容修复 (#447)*）。
- **P3-4 本地未提交状态（属于 P3-4 维护窗口收尾产物，不属本单修改）**：
  - `STATUS.md`、`HANDOFF.md` 本地已有 P3-4 完成态更新（待随下一次开发 PR 同步）；
  - `docs/reports/p3-4-final-acceptance-20261125.md`（P3-4 最终验收报告）；
  - `P3-4-maintenance/p3_4_cleanup-deterministic.patch`（正式执行使用的确定性排序补丁，尚未进 `main`）；
  - 正式运行库（只读观测，不动）：240 memories / 234 versions / 240 vectors / 75 events / 76 stages / 593 event_ledger / 50 tombstones。
- **本单审计边界**：仅新增/更新本报告及在 `STATUS.md`、`HANDOFF.md` 补充 P6-0 记录；不触碰任何 `.py` 源码、正式 `data/` 与 `.env` 配置。

### 1.2 三层证据标签约定（全报告统一）
- **`[代码已实现]`**：在 `VEIL/veil-agent` 源码中直接观察到的类、函数、Schema 或约束。
- **`[实际运行已验证]`**：有单元测试、维护窗口对账或 `recall-lab` 离线标定数据支撑。
- **`[真实体验已验证]`**：`STATUS.md` / `HANDOFF.md` 明确记录 Lan 在真机/日常对话中确认通过。
- **凡缺少上述证据的机制收益或“够用/冗余”判断，一律降级为 `[待验证假设]`**，交由后续 P6-1 ~ P6-3 与消融实验验证。

---

## 二、开源项目深度调研（源码链接、版本依据、实际机制与社区反馈）

| 项目 | 源码链接与版本/分支依据 | 实际已实现机制 | 实验性 / 理论 / 社区反馈来源 | 成熟度与许可证 |
|---|---|---|---|---|
| **Ombre 优秀分支及衍生（Ombre-Brain）** | [P0luz/Ombre-Brain](https://github.com/P0luz/Ombre-Brain)（`main` 分支） | Obsidian 兼容 Markdown + YAML frontmatter；MCP 工具 `breath`（开场唤醒）、`hold`（写入+自动标签+合并）、`trace`、`pulse`；Russell 效价/唤醒度（`valence`/`arousal`）与改进 Ebbinghaus 遗忘曲线。 | 情感坐标与遗忘曲线在长期伴侣对话中的真实体验增益缺少公开对照基准；VEIL 已在 `ticket 426/427` 移除 `feel` 情感通道与自由 `tags` 依赖。 | 中；**MIT**（含 `NOTICE.md` 非强制署名建议与 DCO `CONTRIBUTING.md`） |
| **Mem0** | [mem0ai/mem0](https://github.com/mem0ai/mem0)（`main` 分支 / v3 架构，[Releases](https://github.com/mem0ai/mem0/releases)）；核心实现 [`mem0/memory/main.py`](https://github.com/mem0ai/mem0/blob/main/mem0/memory/main.py) | `.add()` 用 LLM 抽取原子事实；v3 从两阶段 `UPDATE/DELETE` 转向 **ADD-only + 多信号混合检索**（Vector + `BM25` + 实体匹配，`mem0ai[nlp]`）；支持可选 `rerank=True`。 | 早期 OSS 外置图库（Neo4j/Kuzu `graph_store`）在 v3 重构后主要迁往闭源 Mem0 Platform；**社区反馈**：Issue #4573 指出默认提示词易产生大量冗余“垃圾记忆”；Issue #3707 反映纯中文对话提取结果偶发被翻译成英文；重推理模型提取延迟可达 14–15s+。 | 高；**Apache-2.0** |
| **Zep / Graphiti** | [getzep/graphiti](https://github.com/getzep/graphiti)（`main` / v0.27.0 等，[Releases](https://github.com/getzep/graphiti/releases)）；PyPI `graphiti-core`；核心配置 `graphiti_core/search/search_config_recipes.py` | 双时态边模型（`valid_at`/`invalid_at` 现实时间窗 vs `created_at`/`expired_at` 入库时间），自动使旧边失效；混合检索配方 `EDGE_HYBRID_SEARCH_RRF`、`EDGE_HYBRID_SEARCH_NODE_DISTANCE`、`COMBINED_HYBRID_SEARCH_CROSS_ENCODER`；Cross-Encoder 客户端（OpenAI/Gemini/Bedrock）。 | **社区反馈**（GitHub Issues & Reddit）：`add_episode` 需多次 LLM 抽取/去重/失效判定，单条常达数秒至 10s+（受 `SEMAPHORE_LIMIT` 约束）；搜索期若开启 LLM Rerank 延迟可达数十秒、Token 费用攀升；Neo4j 运维重，社区正推动 Kuzu/LadybugDB 等嵌入式图库替代。 | 高；**Apache-2.0** |
| **Letta (MemGPT)** | [letta-ai/letta](https://github.com/letta-ai/letta)（`main` / `0.16.7` 等，[Releases](https://github.com/letta-ai/letta/releases)）、[`letta/memory.py`](https://github.com/letta-ai/letta/blob/main/letta/memory.py)、[letta-ai/sleep-time-compute](https://github.com/letta-ai/sleep-time-compute) | OS 虚拟上下文：常驻可自编辑 `Core Memory` 块（`core_memory_replace`）+ `Archival/Recall Memory` 工具按需检索；`letta-code` 支持 `/sleeptime`、`/dream` 后台整理与 MemFS（Git 版本化）。 | `sleep-time-compute` 属研究/实验范式（独立论文与基准库），在交互间隙持续跑后台推理会显著放大个人 API 预算消耗。 | 高；**Apache-2.0** |
| **MemOS** | [MemTensor/MemOS](https://github.com/MemTensor/MemOS)（[Releases](https://github.com/MemTensor/MemOS/releases)）；`src/memos/mem_scheduler/`、`src/memos/memories/`、`apps/memos-local-plugin` | 三层记忆（明文 `TreeTextMemory`、激活态 KV Cache、参数化权重）；核心单元 `MemCube`；`MemScheduler` 异步低延迟调度与 Next-Scene Prediction 预加载；Qdrant + Neo4j 后端。 | Activation/Parametric 记忆依赖自托管推理引擎（如 vLLM），无法直接用于闭源对话 API；[Issues](https://github.com/MemTensor/MemOS/issues) 中存在策略重排与检索器兼容性讨论。 | 中高；**Apache-2.0** |
| **EverMemOS (EverOS)** | [EverMind-AI/EverOS](https://github.com/EverMind-AI/EverOS)（原 `EverMemOS`，[Releases](https://github.com/EverMind-AI/EverOS/releases)、[`src/`](https://github.com/EverMind-AI/EverOS/tree/main/src)） | 三段式生命周期：`MemCell`（情景痕迹 + 时间边界 `Foresight`）→ `MemScene`（主题聚类 + 动态画像 + 冲突消解）→ `MemScene` 引导的 Agentic 重构召回；MongoDB + ES/Milvus 后端。 | `Foresight` 与多场景聚类偏重服务端批处理与高并发基础设施，LoCoMo/LongMemEval 论文指标优异，但单机轻量部署成本高。 | 中；**Apache-2.0** |
| **Nocturne** | [Dataojitori/nocturne_memory](https://github.com/Dataojitori/nocturne_memory)（`main` 至 `v2.5.6`，`backend/`）；演进项目 [Dataojitori/horon](https://github.com/Dataojitori/horon) | 详见**第三节专项核实**。 | 仓库已进入维护模式，转向 `Horon` / `AISoulKeeper`。 | 中；**MIT** |
| **Tidal_Echo & anchor-memory** | [anhe2021212-spec/Tidal_Echo](https://github.com/anhe2021212-spec/Tidal_Echo)；配套 [anhe2021212-spec/anchor-memory](https://github.com/anhe2021212-spec/anchor-memory) | 详见**第四节专项核实**。 | `Tidal_Echo` 的 `Memory` UI 标签页为占位壳。 | 中低；Tidal_Echo 为 **AGPL-3.0** |
| **Qwen3-Embedding / Reranker** | [QwenLM/Qwen3-Embedding](https://github.com/QwenLM/Qwen3-Embedding)（2025-06 发布，[arXiv:2506.05176](https://arxiv.org/abs/2506.05176)）；[`Qwen/Qwen3-Embedding-0.6B`](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)、[`Qwen/Qwen3-Reranker-0.6B`](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B) | 0.6B/4B/8B 三档，32K 上下文，100+ 语言；Embedding 支持 MRL 维度裁剪（0.6B 默认 1024 维）与指令感知；Reranker 为 Decoder-only 生成式打分（L4 GPU 0.6B p50 ~65ms）；支持 SiliconFlow/DashScope API 及 Ollama/GGUF/vLLM。 | `Qwen3-Reranker` 非传统 BERT Cross-Encoder，需按 `"yes"/"no"` token 概率模板调用；Windows 无独显纯 CPU 本地跑 0.6B 延迟偏高，消融阶段建议优先走兼容 API。 | 高；**Apache-2.0** |

---

## 三、专项核实一：Nocturne 认知机制核实与 VEIL 对照

### 3.1 仓库与实际源码实现核实
对 `Dataojitori/nocturne_memory`（`main` 分支至 `v2.5.6`）及其演进仓库 `Dataojitori/horon` 进行核实：
1. **已实际落地的核心机制**：
   - **URI 图谱路由**：以 `core://agent/identity`、`project://architecture` 等层级路径组织记忆，保留语义层级，不依赖纯向量切碎。
   - **豆辞典（Glossary Auto-Hyperlinking）**：将人名/实体/核心概念绑定到目标记忆 URI，正文写入时通过 **Aho-Corasick 多模式匹配算法**自动检测并生成跨节点超链接，消除记忆孤岛。
   - **条件触发路由（Conditional Disclosure Routing）**：每条记忆可绑定人类可读触发条件（`disclosure`），满足条件时精准注入上下文。
   - **System Boot 身份协议（`system://boot`）**：启动时加载身份、关系与使命锚点，防止会话失忆。
   - **自主 CRUD 与快照回滚**：每次写入自动生成快照，支持人类 Owner 在可视化 Dashboard 审计、合并与回滚。
2. **关于 `Trail`、`潜流`、`Mirror`、`Door` 的核实结论**：
   - 在 `Dataojitori/nocturne_memory` 与 `Dataojitori/horon` 的公开仓库及 `site:github.com/Dataojitori` 检索中，**未发现名为 `Trail`、`潜流`（Undercurrent）、`Mirror` 或 `Door` 的独立源码模块、API 或数据库表实现**。
   - 部分社区衍生讨论与 Agent Playbook 中出现过“semantic trails / memory doors / mirrored states”等概念隐喻（描述沿着节点超链接遍历、按条件打开记忆之门、或镜像状态），但属于**设计隐喻或非公开分支/衍生构想**，并非 `nocturne_memory` 主线可直接复用的成熟代码。

### 3.2 与 VEIL 现有能力对照
| Nocturne 机制 | VEIL 现状与证据层级 | 差距与可借鉴点 |
|---|---|---|
| **豆辞典（Aho-Corasick 实体超链接）** | `[代码已实现]` `brain_retrieval.py` 仅用 `jieba` 分词 BM25 + 向量；`recall_rotation` 有人工 `link_to` 与 `event` 归组，但无自动实体词典超链接。 | **值得在 P6-1 实验**：轻量人名/昵称/高频约定词字典可补足口语短句与专有名词漏召回，计算开销 <1ms。 |
| **条件触发披露（Disclosure）** | `[代码已实现]` `learned_context.py` 常驻注入；`recall_judge.py` 用 LLM 判断相关性；无轻量规则级触发器。 | 可作为“特定约定/高频口头禅”的零成本前置召回补充（P6-1 对比）。 |
| **写入快照与回滚** | `[代码已实现]` `brain_writer.py` 的 `versions` 表保留重写前正文；`brain_backup.py` 支持整库备份恢复；`[实际运行已验证]` P3-4 恢复演练通过。 | VEIL 已有且更完整（含事务 journal 与 digest 校验）。 |
| **Trail / 潜流 / Mirror / Door（隐喻层）** | `[代码已实现]` VEIL 已有 `natural_association.py`（弱联想潜流雏形）、`event_stages` + `directory_line()`（事件轨迹）、`recall_budget`（注入门控）。 | 核心缺口不在新名词，而在 `natural_association` 候选池目前被锁死在 `opening` Top-8，无法在长尾记忆中自然浮现。 |

---

## 四、专项核实二：`Tidal_Echo` 配套 `anchor-memory` 与 VEIL 事件/Dream 对照

### 4.1 `anchor-memory` 实际机制核实
- `Tidal_Echo`（`anhe2021212-spec/Tidal_Echo`，**AGPL-3.0**）本身是 1:1 隐私聊天通道（PWA + FastAPI SSE 中继），其界面中的 `Memory` 标签页为**占位壳**。
- 同作者的 `anhe2021212-spec/anchor-memory` 才是记忆核心，定位为**面向编程 Agent 的本地单 SQLite 账本**：
  1. **账本文件与防火墙**：`FACTS.md`、`decisions.md`、`status.md` 记录当前事实、决策与“已试错防火墙（tried-and-failed firewall）”，防止 Agent 重复踩坑。
  2. **记忆锚点（`@meta` anchors）**：在事实/决策上绑定机器可核验的锚点（`file_exists`、`file_grep`、`json_field`、`golden_metric`），通过 linter/hooks 对照实时仓库检查是否发生“认知漂移”。
  3. **会话连续性与夜间巩固（Overnight Consolidation）**：通过 `.anchor/session-state.md` 与 `HANDOFF.md` 保存会话断点，并在空闲/夜间周期整理、压缩和重组记忆状态。

### 4.2 与 VEIL 事件记忆及 Dream 机制逐项对照
| 维度 | `anchor-memory` | VEIL `event_memory.py` + `brain_events.py`（实代码） | 对照结论 |
|---|---|---|---|
| **存储与账本** | 单 SQLite + Markdown 账本 | `[代码已实现]` `brain.db` 统一承载 `events`、`event_stages`、`event_ledger`（7 类虚拟账本）、`event_state`；`[实际运行已验证]` P3-2/P3-4 迁移对账 mismatch 0。 | VEIL 账本结构更完整，且已与陪伴记忆同库。 |
| **锚点校验（防漂移）** | `@meta` 锚点直接校验代码/文件真实状态 | `[代码已实现]` `stage-evidence` 记录聊天/工具/自主活动时间戳证据；`relationship_base.py` 用 `SOURCE_REF` 绑定 `chat/memory/event/long_task` ID；`[部分缺失]` 尚无针对电脑文件/工具状态的自动失效重检。 | **“记忆锚点校验电脑现状”对 Peter 未来畅游电脑很有价值**：可避免电脑文件/配置变更后记忆仍按旧状态行动（留待 P6-2/电脑线评估）。 |
| **Dream / 巩固机制** | 夜间巩固：跨会话整理、压缩、去重与状态重构 | `[代码已实现]` `event_memory.py:1424` `dream_pass('startup'/'tidy')` **仅执行 `retry_pending() + retry_dirty() + auto_correct()`**（租约互斥 + 脏事件重绘 + 同一 `memory_id` 事件合并），**不做语义巩固、关系演化或记忆权重调整**。 | **关键发现**：VEIL 的 `dream_pass` 名为 Dream，实为“崩溃恢复与同 ID 合并”；真正的睡眠巩固/记忆演化目前是空白，需在 P6-2 专项审计。 |

---

## 五、三层证据盘点与“待验证假设”重分类

### 5.1 VEIL 现有能力三层证据清单
| 能力项 | `[代码已实现]` | `[实际运行已验证]` | `[真实体验已验证]` | 审计结论 |
|---|---|---|---|---|
| **程序级权限与可见性隔离** | `brain_retrieval.py`、`RecallJudge._eligible`、`BrainRead` | P3-1/P3-3/P3-4 隔离测试与正式库验收通过 | Lan P2/P3 确认通过 | **已验证够用** |
| **全回合动态预算与边界截断** | `recall_budget.py` + `memory_read`/`memory_timeline` | `test_recall_budget` 等测试通过 | Lan P2 确认通过 | **已验证够用** |
| **事件-阶段-证据账本与版本** | `brain_events.py`、`brain_writer.py`、`versions`/`tombstones` | P3-2/P3-4 冷备逐项比对 mismatch 0 | `STATUS.md` 记录待 1–2 天持续观察 | **底层已验证，体验持续观察** |
| **混合检索（Vec+BM25）与判官** | `brain_retrieval.py`、`recall_judge.py`（存在重复 Embed 与全库扫描） | `recall-lab/calibration-187` 标定显示纯向量有重叠区；判官无系统消融数据 | 日常可用，但延迟/漏召回/纯应和误召回未量化 | **需整合冗余 + P6-1 消融验证** |
| **自然联想（`natural_association`）** | `light_chat` 门控 + 确定性打分，但候选池仅 `opening` Top-8 + 极少触发 `drift` | 单元测试通过 | **无真实体验盲测对比** | **候选池受限，[待验证假设]** |
| **关系认知（`relationship_base`）** | `confirmed/uncertain/outdated` + `lan > evidence > peter` | 单元测试通过 | 未做长期演化专项验收 | **基础已实现，演化机制待 P6-2 审计** |

### 5.2 长期重要度 vs 当前活跃度 & 待验证假设
为了避免“未测先否定”，将情感数值、记忆强化、活跃度衰减从“无需实施”调整为 **`[待验证假设]`**（交由 P6-2 与消融实验验证）：

1. **概念区分**：
   - **长期重要度（Long-term Importance）**：记忆对 Peter 身份、价值观与核心关系的持久分量（对应现有 `memories.importance` 1–10 与 `pinned`）。重要度高的旧记忆（如重要约定、深刻经历）**不应因时间流逝被机械遗忘**。
   - **当前活跃度（Current Activation / Recency & Resonance）**：记忆在当下对话周期内有多容易“浮到嘴边”（对应 `last_active`、近期被提及/唤醒频次、未解决悬念、以及情感余温）。
2. **待验证假设清单（不在本单下结论，待实验测量）**：
   - **H1（情感数值假设）**：在第一人称故事正文之外，额外引入 `valence` / `arousal` 数值参与排序或联想门控，是否能提升陪伴共情与“不合时宜记忆抑制”？还是仅增加标注噪音？
   - **H2（记忆强化假设）**：每次有效召回/用户主动提及后更新 `last_active` 与唤醒计数，是否能让“最近常聊的事”更自然延续，同时不挤占冷门高重要度记忆？
   - **H3（活跃度衰减假设）**：对低重要度、长期未激活的日常琐事引入温和活跃度衰减（只降检索优先级，不删原文），是否能降低长周期后的噪音误召回率？

---

## 六、修正后的 P6 消融实验设计（单变量先行，再测组合）

### 6.1 基准集与 7 维统一评估指标
沿用并扩展 `recall-lab/calibration-187` 离线只读副本，构建 5 类样本（直问、隐晦追问、日常闲聊、工作/报错抑制、纯应和/表情，建议 60–80 条），统一测量：
1. **召回准确率 (Precision@K)** ｜ 2. **漏召回率 (Miss Rate)** ｜ 3. **误召回率 (False Positive Rate)** ｜ 4. **自然联想质量 (1–5 盲评 + 工作场景误触率)** ｜ 5. **聊天体验盲评** ｜ 6. **端到端延迟 (p50/p95 ms)** ｜ 7. **单轮检索费用 (CNY / 1k turns)**。

### 6.2 第一阶段：单变量对照实验（严格一次只改一个变量）
固定基线 `Baseline-0`（现网逻辑：Vec 0.65 + BM25 0.35，Top-7 + 判官 Vec-8，重复 Embed，重要度 `+0.05`，现网 300 字卡片），每次仅改变 **1 个变量**：

| 单变量实验 | 固定项 | 唯一变化项（对照组 vs 实验臂） | 核心验证目标 |
|---|---|---|---|
| **SV-1 Embedding 复用** | 现网检索、判官、候选池 | A: 同回合 Embed 2 次（现网）<br>B: 复用 `prefetch` 向量（仅 Embed 1 次） | 确认零精度损失下的延迟与费用降幅（预期立即可做减法）。 |
| **SV-2 候选池大小** | 复用 Embedding、线性融合、无 Rerank、现网判官 | A: 7+8（现网） ｜ B: 单池 Top-12 ｜ C: 单池 Top-20 ｜ D: 单池 Top-30 | 找到“漏召回率”趋于收敛的最小候选池拐点。 |
| **SV-3 融合算法** | 候选池 Top-20、无 Rerank、现网判官 | A: 现网 Min-Max 线性加权（0.65/0.35）<br>B: 倒数排名融合 RRF（`k=60`） | 解决向量与 BM25 分数尺度不一致问题，对比漏召回与准确率。 |
| **SV-4 Rerank 模型** | 候选池 Top-20、最佳融合、固定判官输入 Top-8 | A: 无 Rerank<br>B: `Qwen3-Reranker-0.6B`<br>C: `BGE-Reranker-v2-m3` | 验证 Reranker 能否把真正相关的记忆稳定推入 Top-5/Top-8。 |
| **SV-5 判官职责边界** | Top-20 + 最佳 Rerank | A: 现网 LLM 判官选 ID<br>B: 无判官（仅 Rerank 分数阈值截断）<br>C: 轻量判官仅做“0/非0 门控” | 测量去掉/简化判官后，纯应和（`ok`/`💀`）误召回率是否反弹，以及延迟/费用收益。 |
| **SV-6 记忆权重（长期 vs 活跃）** | Top-20 + 最佳融合/Rerank | A: 重要度 `+0.05`<br>B: 纯相关性（0 加权）<br>C: 相关性 + 重要度 + `last_active` 活跃度微调 | 验证长期重要度与当前活跃度在检索排序中的真实收益。 |
| **SV-7 关联与词典机制** | 固定主召回链路 | A: 现网 `opening` Top-8 + `drift`<br>B: 全库长尾未冷却高重要度池<br>C: B + 豆辞典实体别名触发 | 分别测量长尾联想自然度与豆辞典对专有名词/口语指代的补召回效果。 |
| **SV-8 索引表达形式** | 固定检索与判官 | A: 现网正文中段+首尾 300 字卡片<br>B: `title + why_remembered + 关键原话`<br>C: 事件级 `story` 索引 vs 单阶段索引 | 对比不同文本表达对 Reranker/判官判断准确率的直接影响。 |

### 6.3 第二阶段：组合收益验证
从 SV-1 ~ SV-8 中筛选出**单变量净收益为正**的改动，组装 2–3 套候选组合（例如：`最小减法版`、`Rerank 提质版`、`全量认知增强版`），在完整 7 维指标上做端到端对比，确认组合不存在负交互后，再作为 **P6-4 全局整合与施工设计** 的输入。

---

## 七、可复用组件、协议与 P6 后续工单对齐

### 7.1 可复用实现与协议边界
- **`Qwen3-Embedding-0.6B` / `Qwen3-Reranker-0.6B`**：**Apache-2.0**；接入需先解除 `brain_retrieval.py:103` 与 `brain_writer.py:116` 的 `dim == 1024` 硬编码，消融阶段优先走 SiliconFlow / DashScope 兼容 API。
- **RRF 融合算法（Graphiti 思路）**：**Apache-2.0**；15 行纯 Python 实现，零外部依赖。
- **豆辞典 Aho-Corasick / 别名触发（Nocturne 思路）**：**MIT**；纯本地轻量实现，零模型调用。
- **AGPL-3.0 避坑**：`anhe2021212-spec/Tidal_Echo` 为 **AGPL-3.0**，严禁直接复用源码。

### 7.2 后续工单既定顺序（本单不启动）
1. **P6-1**：检索、判官与自然联想审计（含单变量消融实验落地）
2. **P6-2**：关系认知、记忆权重与动态演化审计（含长期重要度/当前活跃度/情感与巩固假设验证）
3. **P6-3**：上下文组装、注入与预算审计
4. **P6-4**：全局整合与施工设计
