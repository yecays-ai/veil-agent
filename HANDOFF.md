# HANDOFF

> 更新：2026-11-29 00:16 +08:00 · 基线 `main = 64fa112`（工单 471 · PR #472 已合并）· 分支 `ticket/473-p7-s3-evidence-feedback-loop` · worktree `worktrees/473-p7-s3` · Issue #473 · 工单 473 / P7-S3 已完成隔离实现与集中回归，待 PR 复审
>
> 部署状态、阶段结论与验收入口只以 [STATUS.md](STATUS.md) 为权威来源，本文件不重复记录。

## 当前停点

- **L1–L4 四级证据核验与分域晋升**：在 `veil/brain/experience_provider.py` 扩展 `ongoing:` 证据新鲜度校验与程序化 `inspect_ref_tier()`，在 `veil/brain/cognitive_consolidation.py` 落地 `_classify_experience_evidence()` / `_preverify_experience_candidates()` / `_evaluate_experience_promotion()`；忽略模型自报等级，L3/L4 循环自证率严格为 0%；三类分域授权（关系仅认 L1+S3、承诺独立受 `auto_promote_commitment` 授权、自主/工具需 ≥2 次独立 L2 运行且 0 失败反例并独立受 `auto_promote_objective` 授权）互不越权；P7 默认关闭时不产生额外 S3 核验调用。
- **待验证硬拦截与低风险单次实践队列**：`unverified_improvement` 在 `decide_experience_candidate`（返回 409）、`query_experience_candidates`、`confirm_experience_candidates`、`ExperienceHintProvider` 与 `Chat.p7_experience_hints_context` 多层硬拦截；仅低风险且无额外权限/付费/外部副作用的方案可经 `select_trial()` 获得单次带 `trial_id` 的独立实践机会，获得 1 次 L1 正向或 2 次独立 L2 正向后毕业生成新的 `success` 候选并淘汰旧项。
- **自主判断工具 `experience_reflect`**：新增于 `veil/brain/experience_feedback.py` 并注册进 `tool_groups.GROUP_TOOL_NAMES['memory_admin']`（不在 `ALWAYS_ON`，定义严格 ≤1500B）；P7 开启时经 `memory.experience.hints.md` 与 `expand_capability` 按需扩组发现；只写反馈记录、悬置冷却（`suspension_until`）或 `uncertain` 修订草案，不篡改已确认正文、不绕过 Lan 否决、不放宽工具权限、不虚构 `adopted`/`adjusted` 指标。
- **原子幂等反馈账本与完整闭环**：`ExperienceFeedbackLedger` 写入 `data/<role>/experience-feedback.jsonl`（每角色互斥锁 + `brain-write.lock` + `(request_id, exp_id, event_type, result_id)` 幂等键 + 密钥脱敏 + `brain_backup.py` `idem_key` 去重合并）；发送前原子落盘 `pending_delivery`，`_finalize_recall_delivery()` 按真实送达落盘 `delivery_sent`/`delivery_cancelled` 与多条客观工具结果，`ongoing_update` 与 `AutonomyController` 自动关联承诺与自主结果；崩溃重启按 `chat.jsonl`/`autonomy.jsonl`/`tools.jsonl` 事实自动协调；连续 2 次客观负结果自动降级 `objective_corroboration` 项，巩固器消费未结算反馈并生成可追溯 `supersedes` 修订版且在下轮生效。
- **验证结果**：12 个定向测试模块合计 **294/294** 通过（`test_p7_s3_evidence_feedback_loop` 8 项、`test_p7_s1_experience_provider`、`test_p7_s2_experience_matching`/`head_semantic`/`chat_review`/`head_backup`、`test_p6_s6_3_cognitive_consolidation`、`test_tool_budget`、`test_prompt_registry`、`test_chat`、`test_autonomy` 53 项、`test_brain_backup`）；`py_compile` 通过；正式 Peter、正式数据与正式配置 0 改动，费用 ¥0。

## 独占文件

- `veil/brain/experience_feedback.py`（新增）
- `veil/brain/experience_provider.py`
- `veil/brain/cognitive_consolidation.py`
- `veil/core/veil_agent.py`
- `veil/autonomy/autonomy.py`
- `veil/brain/storage/brain_backup.py`
- `veil/tools/tool_groups.py`
- `veil/prompts/inline.py`
- `veil/prompts/defaults/memory.experience.hints.md`
- `veil/prompts/defaults/memory.consolidation.prompt.md`
- `tests/test_p7_s3_evidence_feedback_loop.py`（新增）
- `tests/test_tool_budget.py`
- `docs/tickets/473-p7-s3-evidence-feedback-loop.md`、`STATUS.md`、`HANDOFF.md`、`docs/ACCEPTANCE.md`

## 下一步

1. 等待 P7-S3 PR 的 GitHub CI 与复审结果；本停点不合并、不部署、不提前施工 S4/S5。
2. PR 合并后再从最新 `main` 按顺序启动 P7-S4（Brain 监控/成长/召回诊断 UI 与 H-8 主动体检）与 P7-S5（VEIL Lab 10 场景自动验收）。

## 尚未施工

- P7-S4：Brain UI 控制卡、经验卡片溯源与修订 diff、召回与注入诊断、H-8 主动健康检查与路由白名单。
- P7-S5：VEIL Lab 10 场景自动造数与 ON/OFF 回复对照门禁。
- 正式 Windows 部署、正式灰度开启与 Lan 即时自然度确认。

## 红线

- 不启停正式 Peter，不向正式 Peter 发测试消息，不读写正式 `data/peter`，不修改正式配置。
- 不追加付费实验，不提交密钥、人设正文、真实聊天/记忆、本机绝对路径或运行日志。
- 不自动合并 PR；创建 PR 后立即停止，等待复审与合并指令。
