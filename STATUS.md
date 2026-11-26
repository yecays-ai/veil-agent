# 当前状态

> 更新时间：2026-11-26T10:25:00Z（P6-S2、P6-S1 及其加固全部合并；P6-S4 达到可合并状态，PR 待审查/CI，不自行合并）

## P6-S2 · 统一记忆写入事务、防丢稿、证据链与协同恢复（已合并 + 两次加固）

- **合并**：PR #450 squash 合并为 `774b4bd`；合并后加固 PR #451 合并为 `beb5fa3`（锁状态事务内复核、恢复时间戳严格化、retry_dirty 锁定路径）；补充加固 PR #454 合并为 `588701c`（retry_pending 锁定阶段不占批次名额，避免批次饿死）。三者 CI 全绿。
- **核验**：独立只读核验确认 #450 的 43 项审查意见全部能在对应代码/回归中定位；#451/#454 的 CodeRabbit 实质意见均已修复并重新通过 CI；P6-S2 定向套件 363/363 通过。
- **正式数据红线**：未对正式 `data/peter` 执行恢复；维护窗口仍需先 bundle + dry-run，经 Lan 确认后再 apply。

## P6-S1 · 检索、卡片与元数据确定性修复（#452 已合并 + #453 合并后加固）

- **合并**：PR #452 squash 合并为 `fd59f7a`（6 项修复、17 项新增回归、Baseline-1）；合并后加固 PR #453 合并为 `b3d14c2`（换模型待索引 0 付费/0 写入 + 显式 reindex、诊断 API 透出、日期反转校验），CI 全绿。
- **合并后独立审查**：2 个阻断项（换模型 pending 重复付费/不可恢复、诊断未透出 API）已在 #453 修复；桌面 UI 展示按 P6-4 职责边界归 P6-S7。
- **验收步骤**：见 `docs/tickets/452-p6-s1-retrieval-determinism.md` 与 `docs/tickets/455-p6-s1-postmerge-hardening.md`。

## P6-S4 · 统一消融实验执行与候选机制决策（达到可合并状态，PR 待审查）

- **分支/设施**：`ticket/454-p6-s4-ablations`，隔离 `recall-lab` harness（只读快照回放、检索/判官单变量变体、预算硬控台账、合成单测、JSON schema）+ eval-v2（73 条，labeler=ai-assisted、human_confirmed=false，未经 Lan 人工确认）。
- **覆盖与状态**：P6-4 第五章实际 24 个实验 ID 逐行有状态——**17 completed / 5 partial / 2 blocked**（R2 无 rerank 服务与价格上界；E1 缺巩固收益金标准）。partial/blocked 均写明覆盖与证据缺口，不虚报完成。
- **预算**：Tier 0 ¥0；Tier 1 ≈¥1.54 / ≤¥6；Tier 2 ≈¥0.08 / ≤¥10；**总计 ≈¥1.62 / ≤¥20**；append-only 台账如实计入失败与重试，无超限调用。
- **结论（详见决策表）**：建议实施 AB-R1b 中文停用词过滤与 AB-A3 learned-context 四区独立预算；判官链维持 LLM 判官 + 4s + 300 字卡片 + 保守 prompt；关系认知晋升扩权维持 chat-only（先补证据链）；S6 暂缓；S7 的 C1/C2/C3 只有结构侧证据，需真机复测。
- **报告**：`docs/reports/p6-s4-ablations-20261126.md`、`docs/reports/p6-s4-decision-table-20261126.md`、`docs/tickets/454-p6-s4-ablations.md`。**S4 达到可合并状态后停止，不自行合并、不启动 S3/S5/S6/S7。**

## P6 路线

`P6-S0`（#449）→ `P6-S2`（#450 + #451 + #454）→ `P6-S1`（#452 + #453）→ **`P6-S4` 达到可合并状态**；之后按 S4 决策表实施 S3/S5/S6/S7（S4 PR 由审查/CI 通过后交 Lan 决定合并）。P6 不改 M2 的 `input_token_ratio/tool_groups`、不改 M3 的缓存协议，也不削减瞄一眼/弹幕连续性。

## 正式底座

- P3-0 至 P3-4 已合并并完成正式维护部署；#446/#448 已合并。
- 正式运行目录固定跟随 `main`。P6 各单未向正式 Peter 发消息、未启停正式进程、未修改正式源码或真实记忆；所有写入与恢复演练均在独立 worktree 隔离副本完成。
