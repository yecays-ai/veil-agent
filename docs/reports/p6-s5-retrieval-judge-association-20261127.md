# P6-S5｜检索、判官与情境相关自然联想升级实验与交付报告（PR #461 独立复审修复版）

> **基线提交**：`main = 2a264a7`（已合并 PR #458 目录结构重整）  
> **施工分支 / 工单**：`ticket/460-p6-s5-retrieval-judge-association`（工单 460 · PR #461，与 S3 工单 459 并行）  
> **实验环境**：隔离只读副本（`.local-tmp/s4-prep/isolated/brain.db` + `learned-context.json` + `eval-v2.jsonl` 73 条样本，零正式数据写入、零正式 Peter 启停）  
> **模型与费用控制**：使用官方 DeepSeek API（`https://api.deepseek.com`，`deepseek-chat`），经 `BudgetLedger` 硬限 ¥10.00；累计总花费 **¥2.54 / ¥10.00（25.4%）**（Embedding 100% 复用 S4 本地缓存，¥0）。

---

## 一、核心结论与最终配置建议（严格按门禁裁决）

| 候选机制 | 门禁结果 | 默认配置 | 核心实测依据（复审修复后） |
|---|---|---|---|
| **S5 情境感知判官（近期对话 + 工作/情感分流，`sw=off, K=8`）** | **通过门禁** | **主链路启用** | 维持 4s 超时/300 字卡片/保守底盘；空查询 FPR `0.5455→0.3636 (-18.2pp)`、工作 trap FPR `1.0000→0.6667 (-33.3pp)`，最终注入召回基本无损（`0.5933→0.5867`），0 超时/0 解析失败/0 降级 |
| **可审计中文停用词（`stopwords_enabled`）** | **单变量正收益，联合 S5 判官存在负交互 → 未通过联合门禁** | **保持默认 `False`** | 旧判官单变量下 K=8 指标略优，但与 S5 情境判官联合（`s5_final_candidate_sw_on_k8`）时，空查询 FPR 反弹回 `0.5455`、`trap_fpr` 反弹回 `1.0000`；按门禁保持默认关闭 |
| **混合候选池 `candidate_k = 12`** | **未通过门禁** | **维持默认 `8`** | 离线 Recall@K 虽升 `+2.0pp`，但最终判官精确率下跌 `-3.3pp~-12.3pp`、Token 增 `+21.6%` 且 `trap_fpr` 反弹至 `1.0000` |
| **带情境约束自然联想（`association_contextual_enabled`）** | **覆盖提升 5.5×，但工作干扰 `2 > 0` 未过 0 干扰门禁** | **保持默认 `False`** | 真实生产 `Memory` 链路回放：唯一送达覆盖 `2→11` 条（长尾 9 + 近期 5 + 普通生活 2，预算淘汰 3 条保护正常召回）；移除硬长度截断后，2 条隐式自然语言 `trap` 穿透门控（`work_trap_intrusions = 2`），不作弊加黑名单，保持默认关闭，已交付完整生产上下文三路匿名盲评供 Lan 体验 |
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

**完整生产上下文三路匿名盲评（F1 已修复）**：
- 本机路径：`.local-tmp/s5/lan-blind-review-s5.md`（不入库）及密钥 `.local-tmp/s5/lan-blind-map-s5.json`；
- 6 组代表性场景均使用**完整生产请求组装**（真实 `persona/peter.md` + `LearnedContext` `facts`/`growth` + `relationship_base.build()` + 近期对话 + 生产 `Memory.finish_recall()` 正常判官召回 + `ongoing` + 当前消息），三组之间**唯一变量仅为自然联想机制**，随机排列 A/B/C，隐藏 AI 评价与机制名称。

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
| **F8** | 以 S3 最新提交 `de2cc06` + S5 代码执行隔离 `git worktree` 真实合流演练：`veil_agent.py`、`brain_p4_api.py`、`workspace-brain.js` **0 冲突**自动合并 | S3+S5 联合 40 项测试 + 前端语法检查全部通过，演练目录已清理 |
