# P6-S5｜检索、判官与情境相关自然联想升级实验与交付报告（PR #461 独立复审修复版）

> **基线提交**：`main = 2a264a7`（已合并 PR #458 目录结构重整）  
> **施工分支 / 工单**：`ticket/460-p6-s5-retrieval-judge-association`（工单 460 · PR #461，与 S3 工单 459 并行）  
> **实验环境**：隔离只读副本（`.local-tmp/s4-prep/isolated/brain.db` + `learned-context.json` + `eval-v2.jsonl` 73 条样本，零正式数据写入、零正式 Peter 启停）  
> **模型与费用控制**：使用官方 DeepSeek API（`https://api.deepseek.com`，`deepseek-chat`），经 `BudgetLedger` 硬限 ¥10.00；累计总花费 **¥2.71 / ¥10.00（27.1%）**（Embedding 100% 复用 S4 本地缓存，¥0）。

---

## 零、验证范围严格分层（结论与证据一一对应）

| 层级 | 验证方式 | 覆盖内容 | 对应证据文件 |
|---|---|---|---|
| **A. 生产组件回放** | 真实 `BrainIndex` + `Memory.context/finish_recall/confirm_delivery` 在隔离 `brain.db` 上回放 | 全量 73 条样本三路自然联想覆盖、预算裁剪、工作 trap 干扰统计；36 样本四组检索×K 及最终候选联合判官对照 | `research/recall-lab/results/p6_s5_eval.json` |
| **B. 离线校准 / 模拟** | 复用 S4 缓存分数离线扫描 + 合成数据单测 | `Qwen3-Reranker-0.6B` 前置分数门控阈值扫描（¥0）；首次联想回滚、`request_id` 错配拦截、话题切换释放、权限隔离单测 | `p6_s5_eval.json#reranker_score_gate_calibration`、`tests/test_p6_s5_retrieval_judge_association.py` |
| **C. 真实模型回复** | 隔离调用官方 DeepSeek API 生成真实 `persona/peter.md` 回复 | 6 组三路自然联想匿名盲评回复对照（隐藏配置名与 AI 评价，随机排列 A/B/C） | 本机 `.local-tmp/s5/lan-blind-review-s5.md`（不入库） |
| **D. 完整生产 `Chat.send()` 请求验收** | 隔离工作区实例化真实 `veil_agent.Chat` + `BrainAuthority` + 事件账本/故事 + `LearnedContext` + `relationship_base` + 全局 `recall_budget` + 真实 `RecallJudge` + `request_snapshots` | 6 条已验证判官子集样本 × 3 路（18 轮完整 `Chat.send()`），验证 `stopwords=False, K=8` + S5 情境判官、关系底座、快照落盘、送达确认及三路联想真实差异 | `research/recall-lab/results/p6_s5_prod_verify.json` |

---

## 一、核心结论与最终配置建议（严格按门禁裁决）

| 候选机制 | 门禁结果 | 默认配置 | 核心实测依据（复审修复后） |
|---|---|---|---|
| **S5 情境感知判官（近期对话 + 工作/情感分流，`sw=off, K=8`）** | **通过门禁** | **主链路启用** | 维持 4s 超时/300 字卡片/保守底盘；空查询 FPR `0.5455→0.3636 (-18.2pp)`、工作 trap FPR `1.0000→0.6667 (-33.3pp)`，最终注入召回基本无损（`0.5933→0.5867`），0 超时/0 解析失败/0 降级 |
| **可审计中文停用词（`stopwords_enabled`）** | **单变量正收益，联合 S5 判官存在负交互 → 未通过联合门禁** | **保持默认 `False`** | 旧判官单变量下 K=8 指标略优，但与 S5 情境判官联合（`s5_final_candidate_sw_on_k8`）时，空查询 FPR 反弹回 `0.5455`、`trap_fpr` 反弹回 `1.0000`；按门禁保持默认关闭 |
| **混合候选池 `candidate_k = 12`** | **未通过门禁** | **维持默认 `8`** | 离线 Recall@K 虽升 `+2.0pp`，但最终判官精确率下跌 `-3.3pp~-12.3pp`、Token 增 `+21.6%` 且 `trap_fpr` 反弹至 `1.0000` |
| **带情境约束自然联想（`association_contextual_enabled`）** | **待验证候选（未通过 0 干扰门禁，不标记为体验目标完成）** | **保持默认 `False`** | Tier A 生产组件回放唯一送达覆盖 `2→11` 条（长尾 9 + 近期 5 + 普通生活 2，预算淘汰 3 条），但移除长度截断后 2 条隐式自然语言 `trap` 穿透（`work_trap_intrusions = 2`）；Tier D 完整 `Chat.send()` 验证了 6 组三路真实差异并生成匿名盲评 |
| **Qwen3-Reranker-0.6B 本地/深度回忆** | **安装依赖阻断** | **保持默认 `False`** | 本机虽有 RTX 5060 (8GB) + 31.8GB RAM，但正式 `.venv` 无 `torch/onnxruntime/llama-server`，遵循不擅自改安装与启动流程原则；已交付前置门控校准（`threshold=0.25` 拦截 `39.3%` 空查询）及异步防过期契约 `reranker_contract.py` |

---

## 二、四组（停用词 × K）与最终候选联合对照（F4 + F5 严格口径）

固定参数：`weight_vector=0.65`, `weight_keyword=0.35`, `importance_bonus<=0.05`, `vector_candidates=8`（K 仅控制混合检索 `asked` 与判官混合池上限 `ombre_candidates`）。F5 严格区分正常拒绝（`normal_rejected`）、超时、API 错误、JSON 解析失败与降级后实际注入（绝不将异常/解析失败计为空召回成功）：

| 实验组 | 停用词 | K | 判官版本 | 离线 Recall@K | **最终注入召回** | **最终注入精确率** | **空查询 FPR** | **工作 trap FPR** | 正常拒绝 / 异常 / 降级 | 注入条数 |
|---|---|---:|---|---:|---:|---:|---:|---:|---|---:|
| `baseline_k8_sw_off` | 关 | 8 | 旧保守 | 0.6267 | 0.5933 | 0.4510 | 0.5455 (5/11) | 1.0000 | 7 / 0 / 0 | 93 |
| `sw_on_k8` | 开 | 8 | 旧保守 | 0.6267 | 0.6033 | 0.4658 | 0.4545 (6/11) | 0.8333 | 8 / 0 / 0 | 87 |
| `sw_off_k12` | 关 | 12 | 旧保守 | 0.6467 | 0.6000 | 0.4182 | 0.5455 (5/11) | 1.0000 | 7 / 0 / 0 | 101 |
| `sw_on_k12` | 开 | 12 | 旧保守 | 0.6467 | 0.5900 | 0.3280 | 0.5455 (5/11) | 1.0000 | 7 / 0 / 0 | 110 |
| **`s5_context_judge_k8`（推荐最终配置）** | **关** | **8** | **S5 情境** | **0.6267** | **0.5867** | **0.3880** | **0.3636 (-18.2pp)** | **0.6667 (-33.3pp)** | **10 / 0 / 0** | **82** |
| `s5_context_judge_k12` | 关 | 12 | S5 情境 | 0.6467 | 0.5767 | 0.3704 | 0.5455 | 1.0000 | 8 / 0 / 0 | 95 |
| `s5_final_candidate_sw_on_k8`（联合验证） | 开 | 8 | S5 情境 | 0.6267 | 0.5867 | 0.3953 | 0.5455 | 1.0000 | 8 / 0 / 0 | 88 |

**结论**：`s5_context_judge_k8`（停用词关 + K=8 + S5 情境判官）为唯一通过全部质量与误报门禁的最终配置；`stopwords_enabled` 与 `K=12` 均保留为可配置项但保持默认关闭/`K=8`。

---

## 三、生产链路三路自然联想回放与完整生产上下文盲评（F1 + F6）

通过真实生产 `Memory.context(..., defer=True)` + `Memory.finish_recall(space=4000)` + `Memory.confirm_delivery()` 对全量 73 条样本回放（包含三层轻量元数据候选池、正常判官召回去重、`followup` 跳过、冷却/同事件去重与预算裁剪）：

| 模式 | 最终送达联想 | 预算淘汰 | 唯一送达覆盖 | `imp>=7` 长尾 | 近期记忆 | 普通生活 | **工作 trap 干扰** | 主要抑制原因 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `off` | 0 / 73 | 0 | 0 | 0 | 0 | 0 | **0** | `disabled: 46`, `not_applicable: 27` |
| `legacy_breath8` | 9 / 73 | 0 | 2 | 2 | 1 | 0 | **0** | `not_applicable: 27`, `nothing_suitable: 21`, `mood: 16` |
| `contextual_s5`（候选，默认关） | **11 / 73** | **3** | **11 (5.5×)** | **9** | **5** | **2** | **2（未过 0 干扰门禁）** | `not_applicable: 27`, `low_relevance: 13`, `low_context_signal: 11`, `work_task_suppressed: 8` |

**完整生产 `Chat.send()` 请求验收（Tier D，6 组 × 3 路 = 18 轮，`p6_s5_prod_verify.json`）**：
- 全部 6 条代表性样本均选自 36 条已验证判官子集，直接执行真实 `veil_agent.Chat.send()`（含真实 `BrainAuthority`、事件账本/故事、`LearnedContext`、`relationship_base`、全局 `recall_budget`、真实 `RecallJudge` 及 `request_snapshots`）：
  - **18/18 轮** `has_relationship_base = True`、`request_snapshots_written = 2`、`lifecycle_delivered = True`、`retrieval_stopwords = False`、`retrieval_k = 8`、`config_entries_count = 8`；
  - 判官表现覆盖 `chosen`（`S2-001/S2-002/S1-001/S5-001`）、`normal_empty`（`S4-001`）与超时优雅降级（`S3-001`）；
  - 三路自然联想真实差异：`S2-001`/`S2-002` 共情场景三路分别呈现不同背景（`off=[]` / `legacy=['aaf587207e3f']` / `contextual_s5=['76aa0b1db68b']`）；`S4-001` 短噪声场景 `legacy` 仍硬插旧事而 `contextual_s5` 正确抑制；`S5-001` 工作 trap 场景 `contextual_s5` 正确抑制；
  - 新版完整 `Chat.send()` 三路匿名盲评已落盘本机 `.local-tmp/s5/lan-blind-review-s5.md`（不入库）。

**`contextual_s5` 两条工作 `trap` 误触根因分析与后续修复可行性评估**：
- **实际原因**：12 条 `trap` 样本中，8 条被 `context_signal` 准确识别为 `technical_task`/`explicit_past` 拦截；剩余 4 条（`S5-002`/`S5-009`/`S5-011`/`S5-012`）为不含典型技术命令词的自然语言工作/系统陷阱，其中 2 条因与库中记忆共享表层话题词，在 `bge-m3` 双塔余弦相似度上达到 `0.547–0.632`（超过 `0.52` 门控），穿透了纯规则+双塔向量门控。
- **修复可行性评估**：
  1. 调高纯余弦阈值至 `>=0.65`：**不可行**，会同步误杀大部分合法情感/生活长尾联想（多数合法相似度集中在 `0.52–0.62`）；
  2. **复用现有 `RecallJudge` 二阶段审核（可行，推荐后续迭代验证）**：将通过余弦门控的最多 1 条联想候选并入同轮已有 `RecallJudge`（仅增 1 张 300 字卡片，约 +100 input tokens、~¥0.0002/轮、0 新增常驻模型/0 额外往返），由判官结合近期对话语义过滤表层同词工作陷阱。
- **当前交付定性**：`association_contextual_enabled` **未达到 0 干扰启用门禁，严格保持默认关闭，记录为「待验证候选」，不标记为体验目标完成**。

---

## 四、Qwen3-Reranker-0.6B 本地部署阻断与前置门控校准

- **本机硬件与安装阻断**：RTX 5060 (8GB VRAM) + 31.8GB RAM + 20 核 CPU，但正式 `.venv` 无 `torch/onnxruntime/transformers/llama_cpp`、系统无 `ollama/llama-server`。不擅自改变正式安装与启动流程，保持默认关闭。
- **前置分数门控校准（S4 缓存 73 样本，¥0）**：`threshold=0.25` 拦截 `39.3% (11/28)` 空查询误报（`recall_at_5_gated=0.5111`），契约已落盘于 `veil/brain/recall/reranker_contract.py`（前置分数门控 + 向量兜底 + 异步 `request_id`/`topic_hash`/`db_signature`/版本/权限核对）。

---

## 五、PR #461 独立复审 F1–F8 修复与 S3/S5 合流验证

| 复审项 | 修复内容 | 验证结果 |
|---|---|---|
| **F1** | `s5_eval.py` 改用完整生产请求组装（人设 + Learned Context + 关系底座 + 近期历史 + 正常判官召回 + Ongoing + 当前消息），三路仅变自然联想 | 重新生成 6 组匿名盲评材料（`production_assembled=true`） |
| **F2** | `context_signal.py` 移除 `len > 70` 硬截断；明确技术命令/报错与情绪并存时仍判 `technical_task`（抑制无关联想、保留 `has_emotion_signal`），长篇日常交流与纯工作情绪倾诉正常放行 | 新增单测覆盖长篇生活、带情绪明确技术任务、工作情绪倾诉 |
| **F3** | `memory_agent.py` 先以 `followup=False` 分析任务意图再判定 `followup`，修复 `is_work_task` 被 `followup` 掩盖的问题，话题切换至明确技术任务可可靠释放承接并记录 `followup_released_reason` | 回归测试通过 |
| **F4** | 补跑 `s5_final_candidate_sw_on_k8`（停用词开 + K=8 + S5 情境判官）联合对照，发现与 S5 判官存在负交互（FPR 反弹至 0.5455），据此将 `stopwords_enabled` 保持默认关闭 | 数据已入库 `p6_s5_eval.json` |
| **F5** | `_eval_retrieval_and_judge_arm()` 分别统计 `normal_rejected`、`judge_timeouts`、`judge_errors`、`parse_failures`、`degraded_fallbacks` 及降级后最终注入结果，异常绝不计为空召回成功 | 全部臂次 0 超时/0 错误/0 解析失败 |
| **F6** | `memory_agent._association_pool()` 改为轻量元数据缓存（不缓存全库正文），按选定 ID 批量取正文，均衡覆盖长尾 `imp>=7` + 近期 + 普通生活；`s5_eval.py` 改为真实生产 `Memory` 回放 | 单测验证缓存不含 `body`；回放覆盖长尾/近期/普通三层 |
| **F7** | `finish_recall`/`bind_recall_request`/`confirm_delivery`/`rollback_recall` 补齐 `request_id` 归属核验与 `_has_association_rollback` 显式标记，修复首次联想旧状态为 `None` 时的回滚遗漏 | 单测验证首次联想回滚及 `req-A`/`req-B` 错配拦截 |
| **F8** | 以 S3 最新已提交 HEAD `de2cc06` + S5 代码执行隔离 `git worktree` 真实合流演练：`veil_agent.py`、`brain_p4_api.py`、`workspace-brain.js` **0 冲突**自动合并（注：检查时 S3 工作区正处于自身合并/修复中，待 S3 最终 HEAD 确定并合流后须再执行一次最终联合回归） | S3(`de2cc06`)+S5 联合 40 项测试 + 前端语法检查全部通过，演练目录已清理 |

---

## 十、VEIL Lab 正式日用模型三路盲评（`gemini-4-hydrogen`）

- **实验环境**：VEIL Lab 统一隔离环境（`veil-lab.cmd`），正式只读快照 `peter-20261127T170245/r2` + S5 分支代码。
- **配置一致性**：三路（`off` / `legacy_breath8` / `contextual_s5`）仅改变自然联想开关，主回复模型统一为正式日用 `gemini-4-hydrogen`，判官与 Embedding 沿用正式配置并复用缓存；每轮请求均经校验包含完整人设、说话样例、背景、关系底座（`relationship_base`）、Learned Context 与正常召回记忆，三路正常召回完全一致（0 判官异常），3 组 × 3 路 = 9 条有效回复，总成本 ¥1.10，`veil-lab run verify` 全项 `all_checks_ok=true`。
- **Lan 匿名盲评结果（揭盲映射）**：

| 组别 | 场景类型 | Lan 盲评选择 | 揭盲对应 | 备注 |
| --- | --- | --- | --- | --- |
| 第 1 组（`S2-001`） | 情绪倾诉 | **A、C 平局**（A 有安抚感，C 更有生活连续性） | A=`contextual_s5`、C=`legacy_breath8`、B=`off` | 两者各有优势，均优于关闭 |
| 第 2 组（`S2-002`） | 情绪倾诉 | **B** | B=`contextual_s5`、A=`legacy_breath8`、C=`off` | 情境相关联想获选 |
| 第 3 组（`S4-001`） | 极短噪声/表情 | **A（弱偏好）** | A=`contextual_s5`、B=`off`、C=`legacy_breath8` | Lan 明确说明三条回复均不错，属硬挑出的弱偏好 |

- **统计口径与边界**：3 组小样本下 `contextual_s5` 2 胜（含 1 组弱偏好）1 平 0 负，`legacy_breath8` 0 胜 1 平 2 负，`off` 0 胜 0 平 3 负。**明确不将 3 组小样本及第 3 组弱偏好表述为显著优势**。

---

## 十一、自然联想界面双开关、默认关闭决策与 S6 前置待办

1. **默认配置与界面可操作开关**：
   - 保持 `association_contextual_enabled = False`（默认关闭）、`association_enabled = True`（默认启用现行机制）、`stopwords_enabled = False`、`candidate_k = 8`。
   - 在现有「VEIL Brain → 记忆监控 → 六层记忆系统」卡片内新增「自然联想控制」区块（不另建页面，保持六层网格结构不变），复用 `/api/brain/layer-toggle` 与 `recall-association.json` 持久化：
     - **自然联想总开关**（`association`）
     - **情境相关自然联想开关**（`association_contextual`）
     - 实时展示当前实际生效状态（`已关闭` / `已启用 · 现行联想机制` / `已启用 · 情境相关机制`），修改后持久化保存、下一轮聊天生效、重启保留；关闭情境相关联想后确定性恢复现行联想机制，全程无需手动编辑配置文件。
2. **S6 前置待办（不纳入本单默认放行）**：
   - `contextual_s5` 离线回放中仍存在 **2 条隐式 `trap` 穿透**（工作场景下高相似度旧事在无显式技术关键词时被联想放行），待 **S6 前置阶段**结合「同轮判官联合判断」方案（利用同轮判官语义结论门控自然联想，避免纯向量相似度穿透）修复并重新通过门禁后，再评估是否默认启用。
3. **与最新 `main`（`f979ca2`，含 PR #459 与 PR #460 S3）合流验证**：
   - 已将最新 `origin/main`（`f979ca2`）合入本分支，解决 `veil/core/veil_agent.py`、`desktop/workspace-brain.js` 与状态文档冲突，完成 S3 + S5 + P4/P5 路由 + 配置注册 + 检索底座 + 桌面端联合回归验证。
