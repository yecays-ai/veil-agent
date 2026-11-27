# P6-S5｜检索、判官与情境相关自然联想升级

- **工单编号**：460（分支 `ticket/460-p6-s5-retrieval-judge-association`，worktree `worktrees/460-p6-s5`）
- **基线提交**：`main = 2a264a7`（PR #458 已合并，含 P6-S0/S1/S2/S4、P4/P5 管理中心及仓库目录结构重整）
- **产品格**：A（陪伴核心：记忆检索、判官情境判断与自然联想）
- **与 P6-S3（工单 459）并行边界**：
  - S5 独占：`veil/brain/recall/*`、`veil/brain/memory_agent.py`（检索/判官/联想/跨轮与送达契约）、`veil/prompts/defaults/memory.judge.*`、`research/recall-lab/*`。
  - 共享衔接（最小化改动）：`veil/core/veil_agent.py`、`veil/core/config_registry.py`、`veil/brain/brain_p4_api.py`、`desktop/workspace-brain.js` 及状态文档。S5 不触碰 S3 的 `learned_context.py`、`relationship_base.py`、`learned_tools.py`、`desktop/peter-page.js`。

## 目标与实际改动

1. **检索与候选池优化（`veil/brain/recall/brain_retrieval.py`、`memory_agent.py`）**：
   - 保留 Min-Max 混合检索、`0.65/0.35` 融合权重、`importance` `0.05` 加分、向量兜底及同轮 Embedding 共享。
   - 新增可审计、默认关闭的中文停用词过滤 `stopwords_enabled`（仅作用于 BM25 分词，保护日期/人物/昵称/否定词/约定及中英文实义口语词，不改动原始 query 与 Embedding 输入；`get_index` 按开关隔离缓存）。
   - 显式化 `candidate_k`（默认 8），明确 K 控制混合检索返回数及送入判官的混合候选上限，不同步放大 `vector_candidates=8` 或 `rotation_candidates`。
2. **判官与情境相关性优化（`veil/brain/recall/context_signal.py`、`recall_judge.py`、`memory.judge.prompt.md`）**：
   - 维持 S4 确定的 LLM 判官底盘：4s 超时、300 字卡片、保守提示词与向量兜底。
   - 新增零付费本地情境分析器 `context_signal.py`，结合当前消息与近期对话区分纯技术排查/报错任务（`technical_task`）与工作引发的情绪/关系交流（`work_emotion`），不以硬编码工作词黑名单永久排除含技术内容的共同经历。
   - 判官调用携带受限长度的近期对话上下文与情境提示，并记录 `context_mode`、`outcome`（区分正常空召回与降级）及 Token 用量。
3. **带情境约束的自然联想（`veil/brain/recall/natural_association.py`、`recall_rotation.py`、`memory_agent.py`）**：
   - 保留总开关 `association_enabled`，新增独立候选开关 `association_contextual_enabled`（默认关闭，关闭时确定性恢复 Ticket 422 基线）。
   - 解除对 `opening`（`breath() LIMIT 8`）的单一依赖，构建有界多源候选池（`pinned`/`imp>=7` 长尾优先 + `imp>=4` 普通生活经历），按 `brain.db` 签名自动失效刷新。
   - 复用同轮共享 `query_vector` 计算余弦相似度，实施硬相似度门控（`association_min_similarity=0.52`），重要度与长期未出现仅作排序参考、绝不绕过相关性门控；纯技术/报错/短敷衍任务抑制（工作场景干扰 = 0），无合适联想自然返回空。
4. **Reranker 评估与接入契约（`veil/brain/recall/reranker_contract.py`）**：
   - 实测本机硬件与安装环境（RTX 5060 8GB / 31.8GB RAM / 20 核 CPU，但正式 `.venv` 无 torch/onnxruntime/llama-server），按“不擅自改变正式安装与启动流程”红线，保持默认关闭，不强行上线。
   - 基于 S4 缓存数据完成 `Qwen/Qwen3-Reranker-0.6B` 前置分数门控校准，并交付 `reranker_contract.py`（前置分数门控 + 向量兜底保留 + 异步 `request_id`/`topic_hash`/`db_signature`/版本/权限过期核对）。
5. **跨轮连续性与实际送达/回滚契约（`memory_agent.py`、`veil_agent.py`）**：
   - 明确 `retrieved -> judged -> injected -> delivered -> effective_used`（预留 S6）五层状态。
   - 修复预算裁剪淘汰自然联想仍污染冷却/连击的问题：`mark_association` 延迟至 `finish_recall` 确认实际注入后提交；`veil_agent.send()` 送达后调用 `confirm_delivery()` 触发 S6 监听钩子，失败/取消时完整回滚 `rollback_recall()`。
   - 明显切换到技术任务时及时释放旧 `followup` 承接，承接前重新校验 `context_visible()`。
6. **全局配置与 Brain/P4 诊断（`config_registry.py`、`brain_p4_api.py`、`desktop/workspace-brain.js`）**：
   - 在现有 `config_registry` 注册全部 S5 配置项并在 `brain_p4_api` 投影 `recall_config`，零平行配置系统；`brain-recall` 页展示情境模式、判官结论、联想模式与未触发原因、降级来源及送达/回滚状态。

## 验收步骤

1. 在隔离副本运行单元与集成测试（`test_p6_s5_retrieval_judge_association.py` 及全套 `test_*recall*`、`test_brain_retrieval*`、`test_p6_s1*`、`test_repo_layout.py`、桌面单测）全部通过。
2. 审阅 `docs/reports/p6-s5-retrieval-judge-association-20261127.md` 中的四组（停用词×K）对照、trap 判官优化、三路自然联想统计与 Reranker 门控校准结果。
3. 审阅本机 `.local-tmp/s5/lan-blind-review-s5.md` 三路匿名真实回复盲评材料（6 组，隐藏配置与 AI 评价），决定是否开启 `association_contextual_enabled` 及最终默认参数。
4. 在隔离预览中验证：换词回忆、连续追问与切题释放、工作报错不插旧事、工作情绪倾诉可共情、以及 Brain「召回诊断」页各项标签显示准确。
