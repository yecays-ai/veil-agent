# HANDOFF

> 更新：2026-11-28 +08:00 · 基线 `main = c8bc8bb`（工单 464 / PR #465 · S6-3 已合并）· 当前分支 `ticket/465-p6-s7-context` / worktree `worktrees/p6-s7-context` / **PR #466**（工单 465 / P6-S7 上下文纯化、缓存优化与跨阶段接口收口；待 CI / 审核，未合并、未部署）

## 当前停点

- **工单 465 / PR #466 · P6-S7**：实现与隔离验证完成，待 CI / 审核；VEIL Lab 完整生产快照 `peter-20261127T170245/r4` run `p6-s7-r4-20261128-20261128T091129` 最终 verify 通过（mock，¥0，正式数据无变化）。
  - `system.facts` 已移除随工具状态变化的能力描述；`capability_state` 位于运行态尾部，每个工具步骤按实际 schema 刷新。多步展开回归确认稳定 facts 不变、动态能力同步变化。
  - 同轮后续工具请求只删除精确的空召回/空开场说明；第一步边界、真实记忆、完整事件、历史与关系底座保留。
  - `p7.experience_hints` 标准槽位位于 AUXILIARY 的短期便签后、撤回候选前，独立 600 tokens / 4 条，只接收 confirmed；默认没有 provider、0 Token，P7 行为尚未实现。
  - 请求快照、上下文诊断、费用/缓存证据与 P5 H-8 已接入；结构证据为稳定前缀 19,850 → 15,256 chars、受控消息+工具结构 85,426 → 81,789 chars。真实模型缓存命中、费用和延迟未付费验证。
  - 回归：关联路径通过；完整 Python 2,580 项中 2,548 通过、29 跳过，剩余 2 失败 + 1 错误均在未修改 `main c8bc8bb` 原样复现；桌面 71/71 通过。
  - 文档：`docs/tickets/465-p6-s7-context-purification.md`、`docs/reports/p6-s7-context-purification-20261128.md`、`STATUS.md`、本文件、`docs/ACCEPTANCE.md` B-14、P6 需求覆盖审计更新。

- **工单 464 / PR #465 · P6-S6-3 后台认知巩固与经验提炼**：已合并 `c8bc8bb`；默认关闭、未部署、待 Lan 验收，未触碰正式 `data/peter`。
  - 核心引擎：`veil/brain/cognitive_consolidation.py`（新）+ 系统提示词 `veil/prompts/defaults/memory.consolidation.prompt.md`（已注册 `memory.consolidation.prompt`）。
  - 增量与演进：近因+价值均衡选批、关联历史锚点、S6-2 `autonomy.jsonl` 洁净过滤（剔除 `output_audit.clean=False`）、UTF-8 自适应裁剪、预算感知输入上限（`16000` 字节 / `1400` 输出 tokens）、批次指纹去重与跨日状态持久化。
  - S3 与 S6-1 复用：全部认知新项/修订走 `LearnedContext.submit_candidate()` + `reverify_relationship_evidence()` + `RelationshipSemanticWorker`，不另建平行系统；锁定/用户确认/否决硬保护；纯自主无用户原话强制 `uncertain` 防自证；重要度重评统一经 `MemoryWeights.propose()`（默认不自动应用，含去重与拒绝尊重）。
  - P7 预留契约：`p7.experience.v1` 结构化情境经验候选（`uncertain / confirmed / outdated / vetoed`），支持演化链与人工确认/否决，当前不注入 `HEAD`。
  - 运行与费用：`Chat.__init__` 挂载 `self.consolidation` 并随后台调度启停；独立 `Budget` 类目 `cognitive_consolidation`（默认 `¥0.05/日`，精确结算并在 `/cost` 与 `/api/budget/summary` 单列）；`in_flight` 崩溃恢复 + 指数退避 + 脱敏日志 `cognitive-consolidation-runs.jsonl`。
  - 界面与路由：`brain_p4_api.py` / `web_agent.py` / `desktop/main.cjs` 白名单全接通；`workspace-brain.js`（Brain 首页控制面板 + 关系与成长页结果/证据/演进/P7 候选审核）与 `workspace-budget.js`（独立预算与频率卡片）已接通；`config_registry.py` 收录全量配置项。
  - 测试：`tests/test_p6_s6_3_cognitive_consolidation.py`（14 项端到端与复查回归）+ 关联 15 模块共 **269/269** 通过；`node --test desktop/tests/*.test.cjs` **71/71** 通过（含 `cognitive-consolidation.test.cjs` 6 项）。
  - VEIL Lab：隔离 run `p6-s6-3-r3-20261128T084025`（快照 `peter-20261127T170245/r3`，mock，¥0，零触碰正式数据），覆盖默认关闭（定时+手动）、真实大库选批与 335 条未审计旧自主记录排除、自主三态审计、Day 1 沉淀+S3 语义核验（含未通过留待审不入有效上下文）+S6-1 重评+`CacheUsage` 序列化与精确费用+脱敏日志、预算超限拦截、持久化 `pending_commit` 零重付费恢复、Day 2 冲突修订与旧版归档、锁定拦截、P7 已确认保护直到显式批准、频控跳过与 `veil-lab run verify` 通过；报告见 `docs/reports/p6-s6-3-cognitive-consolidation-20261128.md`。
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
| S6-3 后台认知巩固与经验提炼（工单 464 / PR #465） | 已合并 `c8bc8bb`，默认关闭；未部署、待验收 | 269/269 Python；71/71 桌面；Lab run `p6-s6-3-r3-20261128T084025` |
| S7 上下文纯化与接口收口（工单 465 / PR #466） | **实现与隔离验证完成，待 CI / 审核** | Python 2,548 pass / 29 skip / 3 个主线同现非回归；桌面 71/71；Lab run `p6-s7-r4-20261128-20261128T091129` |
| S5 延期：隐式工作 `trap` 穿透 + 同轮判官联合门控 | **未关闭** | `docs/tickets/460-p6-s5-retrieval-judge-association.md`；仍待专项处理 |

## 下一个窗口第一步

1. 提交、推送并创建工单 465 PR；等待 CI 与独立审查，有实质问题时集中修复一次；**不要自行合并或部署**。
2. Lan 按 `docs/ACCEPTANCE.md` B-14 检查连续追问、能力展开与诊断可见性。
3. 后续两个独立方向等 Lan 拍板优先级：
   - 专项关闭 S5 延期的隐式工作 `trap` 穿透 + 同轮判官联合门控；
   - 基于本单 `p7.experience.v1` 契约进入 P7 经验影响行为层施工（或进入 S7 上下文瘦身）。

## 本单已知边界

- 默认关闭（`enabled=False`），自动应用 S6-1 重评也默认关闭（`auto_apply_importance=False`）。
- 未做付费 `--live` 多日实盘实验：真实日用模型在多日自然对话中的归纳深度、噪音比与主观成长体感需灰度开启后由 Lan 实测。
- P7 情境经验已具备结构化沉淀、演进、确认/否决与 S7 标准槽位；默认 provider 未安装，尚未触发或影响对话/工具行为（留待 P7）。
- S7 的真实缓存命中率、费用和延迟没有追加付费实验；当前仅确认稳定前缀和请求结构优化，供应商实际收益待部署后看 usage。
- S5 延期的隐式工作 `trap` 穿透 + 同轮判官联合门控仍未关闭。
- Windows 真机 Electron 页面视觉与手工点击待 Lan 按 B-13/B-14 体验。

## 红线（不变）

- 不启停正式 Peter、不写正式 `data/peter`、不向正式 Peter 发测试消息。
- 未经授权不追加付费 API 实验。
- 正式恢复必须先协同 bundle + dry-run，经 Lan 确认后才 `--apply`。
- 换 Embedding 模型必须用 `scripts/embedding_reindex.py`（先 dry-run 再 `--apply`），不依赖后台重试。
- 不提交人设正文、密钥、真实聊天/记忆、本机绝对路径、日志；VEIL Lab 快照含真实数据，只留本地。
