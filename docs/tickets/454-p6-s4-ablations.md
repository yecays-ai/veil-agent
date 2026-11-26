# 454 · P6-S4 统一消融实验执行与候选机制决策

> 状态：**已达到可合并状态，独立 PR 待审查（不自行合并）**。实验执行、报告、决策表、覆盖核对已完成；
> 未启动 S3/S5/S6/S7 施工，未修改正式运行环境与真实记忆。

## 一、属于哪一格

- **P6 连续施工顺序**：P6-S0 → P6-S2 → P6-S1 → **P6-S4（本单）** → 依据实验结论进入 P6-S3 / P6-S5 → P6-S6 → P6-S7。
- **本单在全局架构里的位置**：只做**隔离消融实验与决策证据建设**，不部署候选功能到正式 Peter。
  用 recall-lab 在只读快照上回放 P6-4 第五章的 24 个实验 ID，产出「哪些候选机制值得进 S3/S5/S6/S7、
  用什么参数、多少延迟与费用」的可复现证据。
- **前置**：P6-S2 统一写入/恢复契约（774b4bd）与 P6-S1 Baseline-1（fd59f7a，PR #452）均已合并；
  本单以 fd59f7a 为实验基线，提交前 rebase 到最新 origin/main。
- **明确不做**：不改生产功能源码（recall-lab 之外零源码修改）；不写正式 data/peter、不启停正式进程、
  不向正式 Peter 发消息；不提前实施 S4 未验证的判官/检索/巩固参数；不合并本 PR。

## 二、Lan 能多做什么

1. **拿到一份能直接拍板的清单**：docs/reports/p6-s4-decision-table-20261126.md 把候选机制按 S3/S5/S6/S7
   分类，逐条写「推荐实施 / 不推荐实施 / 证据不足」+ 你日常会有什么变化 + 风险/延迟/费用 + 推荐参数。
2. **只花 ¥1.62 就知道哪些候选该放弃**：24 个 ID 全部有状态（17 completed / 5 partial / 2 blocked），
   预算台账可查；没有为了凑完成而虚报，也没有为省钱跳过对照臂。
3. **可复现的实验设施**：recall-lab 可在本机隔离重跑；73 条 eval-v2（AI 辅助标注、未经你人工确认）
   与全部聚合指标、哈希进仓库（原始样本不进 git），以后 S3/S5/S6/S7 施工前可重新对照。
4. **明确知道下一步做什么**：S5 建议只做「中文停用词过滤」并维持判官现状；S3 建议做「四区独立存储预算」，
   晋升扩权先补 chat 证据链；S6 暂缓；S7 先真机复测 C1/C2/C3。

## 三、怎么验收

### 代码与数据层（可在本机复跑）

1. 跑合成单测：python -m unittest discover -s recall-lab/tests -v → 23 项全过（无网络、无真实数据）。
2. 复跑 Baseline-1 门禁：python scripts/p6_s1_baseline.py --out .local-tmp/s4/baseline-1-gate.json → 18/18 通过。
3. 复跑实验（命令见 recall-lab/README.md）→ recall-lab/results/coverage.json 里 24 个 ID 状态与参数可核对；
   各实验 JSON 含 fixed_baseline / variable / arms / verdict / budget。
4. 预算核对：recall-lab 的 append-only 台账 + 本单报告第九节；Tier 1 ≤¥6、Tier 2 ≤¥10、总 ≤¥20 均未超。

### 体验层（需要 Lan 自己做，本次未做）

- 本单**没有**真实回复体验盲评，也没有你的体验验收；任何「更好聊/更自然」的说法都不成立。
- S5/S3 按决策表实施后，需要你验收的体验点：
  - 问一件旧事（尤其结尾细节）是否更容易接上完整经过；
  - 工作/报错/表情包语境下不再乱插无关旧记忆；
  - 关系认知/约定能否稳定记住，且在 P4 记忆管理里可查、可改、可删；
  - 展开工具后首轮回复是否更快（S7 复测项）。

## 四、实验矩阵与状态（摘要）

- 计数口径：P6-4 正文写 21 项原子实验、工单描述写 22 个 ID，矩阵表实际 24 个 ID（23 原子 + AB-COMB）；
  按工单「以完整矩阵条目为准」逐行覆盖 24/24。
- Tier 0（¥0，11 个）：全部 completed。
- Tier 1（≤¥6，6 个）：completed 5（R4a/R3a/R3b/R3c/R3d）、blocked 1（R2 无 rerank 服务与价格上界）；
  实际结算 ¥1.539940。
- Tier 2（≤¥10，7 个）：completed 1（COMB，证明力有限）、partial 5（C1/C2a/C2b/C3/A1b：只有结构侧/微探针证据）、
  blocked 1（E1 缺巩固收益金标准）；实际结算 ¥0.084864。
- 总支出 **¥1.624804 / ¥20**。

## 五、交付物

- recall-lab/：隔离 harness、检索/判官变体、预算台账、指标、23 项合成单测、JSON Schema、README
- recall-lab/results/：24 个实验聚合结果 + coverage.json + run-manifest.json + baseline-1-gate.json
- docs/reports/p6-s4-ablations-20261126.md：完整脱敏实验报告
- docs/reports/p6-s4-decision-table-20261126.md：Lan 白话决策表
- 本工单 + STATUS.md / HANDOFF.md 状态更新

## 六、隐私与边界

- 未修改正式运行目录任何文件；未启停正式进程；未向正式 Peter 发消息；未写 data/peter。
- 未提交真实聊天/记忆/人设/API key/本机路径/日志；报告只含聚合结果、哈希、计数与匿名化描述。
- eval-v2 为 AI 辅助标注（labeler=ai-assisted、human_confirmed=false），不冒充人工金标准；不用判官结果当标注。
