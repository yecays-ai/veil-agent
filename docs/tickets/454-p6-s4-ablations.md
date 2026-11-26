# 454 · P6-S4 统一消融实验执行与候选机制决策

> 状态：**已完成全部 24/24 实验与有效性核查，达到完整交付且可合并状态（PR #455 待审查/CI，不自行合并）**。
> 未启动 S3/S5/S6/S7 施工，未修改正式运行源码与真实 `data/peter`，未启停正式 Peter。

## 一、属于哪一格

- **P6 连续施工顺序**：P6-S0 → P6-S2 → P6-S1 → **P6-S4（本单）** → 依据实验结论进入 P6-S3 / P6-S5 → P6-S6 → P6-S7。
- **本单在全局架构里的位置**：只做**隔离消融实验与决策证据建设**，不部署候选功能到正式 Peter。用 `recall-lab` 在只读快照上完成 P6-4 第五章全部 24 个实验 ID 的单变量对照、S2 隔离写入验证、Reranker 实测及 Tier 2 真实模型回复盲评，产出可直接指导 S3/S5/S6/S7 施工方向的决策依据。
- **明确不做**：不改生产功能源码（`recall-lab` 之外零源码修改）；不写正式 `data/peter`、不启停正式进程、不向正式 Peter 发消息；不自行合并 PR；不提前启动后续工单。

## 二、Lan 能多做什么

1. **直接拍板的 24 项完整决策表**：`docs/reports/p6-s4-decision-table-20261126.md` 按 S3/S5/S6/S7 列出每项候选的日常变化、已证明/未证明范围、推荐参数、费用与风险。
2. **仅花 ¥2.21 / ¥20 跑完 24/24 全部实验**：含 SiliconFlow Reranker 实测（`AB-R2`）、S2 隔离写入验证（`AB-A2/A3`）、真实生产结构前缀缓存（`AB-C1`）、真实工具调用（`AB-C3`）及时间切片认知巩固前瞻回放（`AB-E1`）。
3. **本机可直接盲读的 46 组匿名 A/B 回复对照**：`.local-tmp/s4/lan-blind-review.md`（不进 git），无需看代码即可核验真实回复体验差异。

## 三、怎么验收

### 代码与数据层（可在本机复跑）

1. 合成单测：`python -m unittest discover -s recall-lab/tests -v` → **39/39 通过**（0 网络、0 真实数据）。
2. Baseline-1 门禁：`python scripts/p6_s1_baseline.py --out .local-tmp/s4/baseline-1-verify.json` → **20/20 通过**。
3. 覆盖与一致性：核对 `recall-lab/results/coverage.json`（**24/24 completed**）、`recall-lab/results/budget-summary.json`（**¥2.208254 / ¥20**）与报告/决策表逐项一致。

### 体验层（需 Lan 人工确认）

- 打开本机 `.local-tmp/s4/lan-blind-review.md` 抽读匿名 A/B 回复对照（46 组）；所有 Tier 2 评分均为 AI 辅助盲评（`human_confirmed=false`），不代替 Lan 人工验收。

## 四、实验矩阵与费用摘要

- **覆盖**：24/24 全部 `completed`（Tier 0 11/11、Tier 1 6/6、Tier 2 7/7；0 partial、0 blocked）。
- **费用**：Tier 0 `¥0.00`；Tier 1 `¥1.784595 / ≤¥6`；Tier 2 `¥0.423659 / ≤¥10`；**总计 ¥2.208254 / ≤¥20（11.0%）**，append-only 台账 699 条结算记录完整可查。

## 五、交付物

- `recall-lab/`：隔离 harness（含 `rerank.py`、`s2_verify.py`、`tier2_real.py`）、预算硬控台账、39 项合成单测、JSON Schema、README
- `recall-lab/results/`：24 项实验结果 JSON + `coverage.json` + `run-manifest.json` + `baseline-1-gate.json` + `budget-summary.json`
- `docs/reports/p6-s4-ablations-20261126.md`：完整脱敏实验报告（含阶段一有效性核查记录）
- `docs/reports/p6-s4-decision-table-20261126.md`：Lan 白话决策表
- 本地（gitignored）：`.local-tmp/s4/lan-blind-review.md`（46 组匿名 A/B 回复对照材料）
- 本工单 + `STATUS.md` / `HANDOFF.md` 同步更新

## 六、隐私与边界

- 未修改正式运行目录任何文件；未启停正式进程；未向正式 Peter 发消息；未写 `data/peter`。
- 未提交真实聊天/记忆/人设/API key/本机路径/原始回复；报告与结果 JSON 仅含聚合指标、哈希、计数与匿名化描述。
