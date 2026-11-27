# P6-S6-2｜自主闲暇自然联想

- **工单编号**：463（分支 `ticket/463-p6-s6-2-leisure-association`，worktree `worktrees/463-p6-s6-2-leisure-association`）
- **基线提交**：`main = ead71b6`（PR #462 已合并，含 P6-S0~S5 与 S6 五项工程门禁）
- **施工依据**：PR #455 实验 `AB-A1b`、决策表（`docs/reports/p6-s4-decision-table-20261126.md`）及原始需求覆盖审计（`docs/reports/p6-s4-requirements-coverage-audit-20261126.md`）
- **产品格**：A（陪伴核心：自主生活与自然联想连续性）
- **与 S6-1 并行边界**：
  - S6-2 独占：`veil/brain/recall/leisure_association.py`、`veil/prompts/defaults/memory.association.leisure.label.md`、`veil/prompts/defaults/autonomy.wake.rules.md`、`veil/prompts/defaults/autonomy.wake.protocol.md`、`veil/autonomy/autonomy.py`、`tests/test_p6_s6_2_leisure_association.py`。
  - 共享最小化衔接：`veil/brain/recall/recall_rotation.py`、`veil/brain/memory_agent.py`、`veil/core/veil_agent.py`、`veil/core/config_registry.py`、`veil/brain/brain_p4_api.py`、`veil/prompts/registry.py`、`desktop/workspace-brain.js` 及状态/验收文档。不触碰 `brain.db` schema、权重演化表或认知巩固模块；通过只读元数据（兼容 `effective_importance`/`dynamic_importance`）和 `confirm_delivery(wake_mode='idle_leisure')` 监听钩子与 S6-1 松耦合协作。

## 三问（动手前确认）

1. **做完后 Lan 每天能做到什么现在做不到的事？**
   - 在 Brain「记忆监控」页打开「自主闲暇自然联想」开关（默认关闭）后，Peter 在没有工作/长任务的自主闲暇唤醒时，能够结合当前情境自然想起过往有意义的经历，并让这段经历真实影响他的思考、兴趣或工具行动，而正在执行的任务零干扰，且自主输出不再出现第三人称动作旁白或无工具文字扮演。
2. **比最简单的替代办法多了什么？**
   - 相比把老记忆随机塞进唤醒提示词，本方案提供程序级闲暇 vs 任务态门控、复用 S5 同轮共享向量相似度（硬门控不被重要度绕过）、个人意义（`why_remembered`）与长期重要度加权、冷却与连击指数退避、自主输出旁白/扮演审计、送达确认闭环及可持久化 UI 开关与诊断。
3. **不看代码怎么验收？**
   - 在 Brain「记忆监控」页切换「自主闲暇自然联想」开关并重启确认持久化；在 VEIL Lab 隔离副本对比工作任务唤醒（0 干扰）与闲暇唤醒（情境契合、无旁白扮演、行动与工具结果一致）；在 Brain「召回诊断」与自主活动记录查看门控原因、相似度与审计状态。

## 实际改动

1. **自主态行为约束与审计（`autonomy.wake.rules.md`、`autonomy.wake.protocol.md`、`leisure_association.py`、`autonomy.py`）**：
   - 在自主唤醒规则与四行笔记协议中明确禁止第三人称动作旁白/舞台指示与无工具文字扮演，要求仅陈述真实工具确认过的行动。
   - 新增程序级 `audit_autonomy_output()`，对每次自主唤醒输出检测第三人称旁白与无成功工具时的行动扮演，将审计结果写入 `autonomy.jsonl` 的 `output_audit` 字段。
2. **严格闲暇门控（`veil/brain/recall/leisure_association.py`、`veil/autonomy/autonomy.py`）**：
   - `AutonomyController._wake_situation()` 汇总结构化程序事实（活跃 `long_task`、`pending_action`、`work_checkpoint`、`investigation_checkpoint`、未确认工具恢复、定时闹钟原因、上次焦点/下一步、想法与生活状态）。
   - `classify_wake_mode()` 硬拦截所有工作/任务/恢复/非自主唤醒（`active_long_task`、`pending_action`、`checkpoint_active`、`unconfirmed_tool_recovery`、`scheduled_task_wake`、`technical_focus_task`、`non_autonomy_wake`），仅对纯闲暇态（`idle_leisure`）放行。
3. **有情境依据的长尾自然联想（`leisure_association.py`、`memory_agent.py`、`recall_rotation.py`）**：
   - 复用现有多源长尾候选池（`_association_pool`）、`BrainIndex` 向量与 S5 同轮共享 `query_vector` 余弦相似度（默认阈值 `0.38`，无向量或低于阈值宁缺毋滥，绝不盲目轮换）。
   - 综合个人意义（`why_remembered` 与当前情境共振）、长期重要度（`importance`/`pinned`，平滑只读兼容 S6-1 `effective_importance`/`dynamic_importance`）、沉默时长与冷却/连击指数退避，过滤纯技术报错日志，使用专用标签 `memory.association.leisure.label` 注入。
4. **模块协作与送达闭环（`veil_agent.py`、`memory_agent.py`、`autonomy.py`）**：
   - 修复 `Chat.send(autonomy=True)` 在不调用 `send_message` 时 `delivered=False` 误触发 `rollback_recall()` 的缺陷：自主唤醒拿到有效回复时置 `autonomy_reply_delivered=True`，确保 `confirm_delivery()` 持久化冷却并通知 S6-1 `delivery_listeners`（携带 `wake_mode='idle_leisure'`）。
   - 在 `autonomy.jsonl` 中记录脱敏的 `leisure_association` 摘要（仅模式/门控/ID/原因/相似度）。
5. **界面管理与配置持久化（`config_registry.py`、`brain_p4_api.py`、`desktop/workspace-brain.js`）**：
   - 在 `config_registry` 注册全部 `leisure_association_*` 配置项；在 Brain「记忆监控 → 自然联想控制」新增独立「自主闲暇自然联想」开关（默认关闭）、实际生效状态（含总开关关闭时的抑制提示）与最近自主唤醒运行诊断摘要；经 `/api/brain/layer-toggle`（`layer_id='association_leisure'`）持久化至 `recall-association.json`，重启保留；在「召回诊断」单轮详情展示自主门控模式与原因。

## 验证证据

- **单元测试**：`tests/test_p6_s6_2_leisure_association.py` + `tests/test_p6_s5_retrieval_judge_association.py` + `tests/test_autonomy.py` + `tests/test_repo_layout.py` + `tests/test_p6_s6_chat_evidence_budget.py` + `tests/test_p6_s6_idempotent_writes.py` 共 **119/119 通过**。
- **桌面测试**：`desktop/tests/workspace-brain.test.cjs` **3/3 通过**（全量 `desktop/tests/*.test.cjs` 59/60 通过，仅 `backend.test.cjs:74` 因沙箱端口隔离失败，与本单无关）。
- **VEIL Lab 完整生产上下文 + 隔离真实工具验证（零费用、零触碰正式目录）**：
  - 快照：`peter-20261127T170245 / r2`；Run：`veil-lab/runs/p6-s6-2-verify-20261127T235657/reports/latest.md`（`run verify` **通过**）。
  - 隔离副本实测（242 条真实向量、226 条可见记忆、36 条多源长尾池）：8/8 任务态唤醒 **0 干扰**；默认关闭不触发；开启持久化生效；闲暇唤醒命中情境契合长尾记忆（`similarity=1.0`, `meaning_bonus=2.0`）；隔离真实 `ThoughtIndex` 工具执行成功、`output_audit.clean=True`、`confirm_delivery` 触发 S6-1 监听器；连续二次唤醒冷却拦截重复；低相关性返回 `low_relevance`；正式 `data/peter` 目录零变化；付费调用 `0` 次。

## 尚未验证的边界（如实说明）

- 遵守「不擅自追加付费实验、不触碰正式 Peter 数据」红线，本单未执行付费 `--live` 真实大模型自主回复盲评；真实大模型在多日自主生活中的主观自然度与极低概率旁白倾向需由 Lan 授权后通过灰度开关或 VEIL Lab `--live` 体验确认。
- S6-1 与本单并行施工，双模块动态评价权重与自主闲暇联想的联合表现待 S6-1 合流后复验。

## 验收步骤

见 [`docs/ACCEPTANCE.md`](../ACCEPTANCE.md#b-11-自主闲暇自然联想p6-s6-2--工单-463)。
