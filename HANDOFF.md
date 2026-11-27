# HANDOFF

> 更新：2026-11-28 +08:00 · 基线 `main = 90846bd`（PR #461 / S5、PR #462 / 工单 461、PR #463 / S6-2、PR #464 / S6-1 均已合并）· 当前分支 `ticket/464-p6-s6-3-cognitive-consolidation` / worktree `worktrees/464-p6-s6-3`（工单 464 / P6-S6-3 后台认知巩固与经验提炼；默认关闭；待独立审核、CI 与 Lan 体验；未合并、未部署）

## 当前停点

- **工单 464 / P6-S6-3 后台认知巩固与经验提炼**：开发、自动化回归与 VEIL Lab 完整生产快照隔离验证完成；未合并、未部署，未触碰正式 `data/peter`。
  - 核心引擎：`veil/brain/cognitive_consolidation.py`（新）+ 系统提示词 `veil/prompts/defaults/memory.consolidation.prompt.md`（已注册 `memory.consolidation.prompt`）。
  - 增量与演进：近因+价值均衡选批、关联历史锚点、S6-2 `autonomy.jsonl` 洁净过滤（剔除 `output_audit.clean=False`）、UTF-8 自适应裁剪、预算感知输入上限（`16000` 字节 / `1400` 输出 tokens）、批次指纹去重与跨日状态持久化。
  - S3 与 S6-1 复用：全部认知新项/修订走 `LearnedContext.submit_candidate()` + `reverify_relationship_evidence()` + `RelationshipSemanticWorker`，不另建平行系统；锁定/用户确认/否决硬保护；纯自主无用户原话强制 `uncertain` 防自证；重要度重评统一经 `MemoryWeights.propose()`（默认不自动应用，含去重与拒绝尊重）。
  - P7 预留契约：`p7.experience.v1` 结构化情境经验候选（`uncertain / confirmed / outdated / vetoed`），支持演化链与人工确认/否决，当前不注入 `HEAD`。
  - 运行与费用：`Chat.__init__` 挂载 `self.consolidation` 并随后台调度启停；独立 `Budget` 类目 `cognitive_consolidation`（默认 `¥0.05/日`，精确结算并在 `/cost` 与 `/api/budget/summary` 单列）；`in_flight` 崩溃恢复 + 指数退避 + 脱敏日志 `cognitive-consolidation-runs.jsonl`。
  - 界面与路由：`brain_p4_api.py` / `web_agent.py` / `desktop/main.cjs` 白名单全接通；`workspace-brain.js`（Brain 首页控制面板 + 关系与成长页结果/证据/演进/P7 候选审核）与 `workspace-budget.js`（独立预算与频率卡片）已接通；`config_registry.py` 收录全量配置项。
  - 测试：`tests/test_p6_s6_3_cognitive_consolidation.py`（7 项端到端）+ 关联 15 模块共 **262/262** 通过；`node --test desktop/tests/*.test.cjs` **69/69** 通过（含 `cognitive-consolidation.test.cjs` 4 项）。
  - VEIL Lab：隔离 run `p6-s6-3-final-20261128T074609`（快照 `peter-20261127T170245/r3`，mock，¥0，零触碰正式数据），覆盖默认关闭、真实大库选批、不洁自主过滤、Day 1 沉淀+S6-1 重评+精确费用（¥0.00496）、预算超限拦截、崩溃恢复、Day 2 冲突修订与旧版归档、锁定拦截、P7 演进、频控跳过与 `veil-lab run verify` 通过；报告见 `docs/reports/p6-s6-3-cognitive-consolidation-20261128.md`。
  - 文档：`docs/tickets/464-p6-s6-3-cognitive-consolidation.md`、`docs/reports/p6-s6-3-cognitive-consolidation-20261128.md`、`STATUS.md`、本文件、`docs/ACCEPTANCE.md` B-13。
- **工单 462 / PR #464（P6-S6-1 记忆动态评价、强化与保护性衰减）**：已合并 `90846bd`（2026-11-27 23:15 UTC）；**未部署、无 Lan 验收记录**（B-12）。
- **工单 463 / PR #463（P6-S6-2 自主闲暇自然联想）**：已合并 `81dba35`（2026-11-27 17:05 UTC）；默认关闭、**未部署、无 Lan 验收记录**（B-11）。
- **工单 461 / PR #462（P5 健康巡检 + S6 开工门禁）**：已合并 `ead71b6`（2026-11-27 15:38 UTC）；**未部署、无 Lan 验收记录**（B-9/B-10）。
- **工单 460 / PR #461（P6-S5）**：已合并 `504c0a7`；未部署、无 Lan 验收记录（B-8）。
- **工单 459 / PR #460（P6-S3）**：已合并 `f979ca2`；未部署、无 Lan 验收记录（B-7）。

## S6 门禁与 S6 子阶段状态

| 事项 | 状态 | 证据 |
| --- | --- | --- |
| S6 五项工程门禁（工单 461） | 已关闭 | `docs/tickets/461-p5-s6-gates.md`；对应测试与实验记录 |
| S6-1 送达激活、保护性衰减与版本化重评（工单 462 / PR #464） | 已合并 `90846bd`，未部署、待验收 | `docs/ACCEPTANCE.md` B-12；`docs/reports/p6-s6-1-memory-reinforcement-20261128.md` |
| S6-2 自主闲暇联想（工单 463 / PR #463） | 已合并 `81dba35`，默认关闭；未部署、待验收 | `docs/ACCEPTANCE.md` B-11 |
| S6-3 后台认知巩固与经验提炼（工单 464） | **开发侧完成，默认关闭，待 PR/审核/CI/体验** | 262/262 Python；69/69 桌面；Lab run `p6-s6-3-final-20261128T074609` |
| S5 延期：隐式工作 `trap` 穿透 + 同轮判官联合门控 | **未关闭** | `docs/tickets/460-p6-s5-retrieval-judge-association.md`；仍待专项处理 |

## 下一个窗口第一步

1. 跟踪本单 PR 的 CI 与独立审查；如有实质审查意见集中修复后一次推送。
2. 合并并部署后，按 `docs/ACCEPTANCE.md` B-13 让 Lan 在灰度开关开启下体验真实多日巩固效果；**不要自行合并或部署**。
3. 后续两个独立方向等 Lan 拍板优先级：
   - 专项关闭 S5 延期的隐式工作 `trap` 穿透 + 同轮判官联合门控；
   - 基于本单 `p7.experience.v1` 契约进入 P7 经验影响行为层施工（或进入 S7 上下文瘦身）。

## 本单已知边界

- 默认关闭（`enabled=False`），自动应用 S6-1 重评也默认关闭（`auto_apply_importance=False`）。
- 未做付费 `--live` 多日实盘实验：真实日用模型在多日自然对话中的归纳深度、噪音比与主观成长体感需灰度开启后由 Lan 实测。
- P7 情境经验仅完成结构化沉淀、演进与人工确认/否决，尚未注入对话或路由行为（留待 P7）。
- S5 延期的隐式工作 `trap` 穿透 + 同轮判官联合门控仍未关闭。
- Windows 真机 Electron 页面视觉与手工点击待 Lan 按 B-13 体验。

## 红线（不变）

- 不启停正式 Peter、不写正式 `data/peter`、不向正式 Peter 发测试消息。
- 未经授权不追加付费 API 实验。
- 正式恢复必须先协同 bundle + dry-run，经 Lan 确认后才 `--apply`。
- 换 Embedding 模型必须用 `scripts/embedding_reindex.py`（先 dry-run 再 `--apply`），不依赖后台重试。
- 不提交人设正文、密钥、真实聊天/记忆、本机绝对路径、日志；VEIL Lab 快照含真实数据，只留本地。
