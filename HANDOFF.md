# HANDOFF

> 更新时间：2026-11-27T15:56:00+08:00 · 基线 `main` = `2a264a7`（PR #458 已合并）· 当前分支 `ticket/460-p6-s5-retrieval-judge-association`（工单 460 · PR #461 二次复审收口完成：补齐 18 轮真实 `Chat.send()` 完整生产链路验证、验证范围四层区分及 `contextual_s5` 两条 `trap` 误触根因评估，待独立复审；未合并、未部署正式目录）

## 当前停点

- **本单（工单 460 · PR #461 · P6-S5 二次复审收口完成）**：
  - **Tier D 真实 `Chat.send()` 完整生产链路验证**：新增 `research/recall-lab/recall_lab/s5_prod_verify.py` 与结果 `p6_s5_prod_verify.json`，在隔离副本实例化真实 `Chat`、`BrainAuthority`、事件账本/故事、`LearnedContext`、`relationship_base`、全局 `recall_budget`、真实 `RecallJudge` 与 `request_snapshots`，对 6 条已验证判官子集样本 × 3 路跑完 18 轮完整 `Chat.send()`（18/18 关系底座/快照/送达确认通过，`stopwords=False, K=8`）；重生成本机 `.local-tmp/s5/lan-blind-review-s5.md`（不入库）。
  - **自然联想交付定性**：`association_contextual_enabled` 因生产组件回放中 2 条自然语言隐式 `trap` 穿透双塔余弦门控（`work_trap_intrusions=2 > 0`），**未通过 0 干扰门禁，严格保持默认关闭，记录为「待验证候选」，不标记为体验目标完成**；后续可行方向为复用同轮 `RecallJudge` 做二阶段审核。
  - **最终默认配置**：`stopwords_enabled=False`、`candidate_k=8` + S5 情境感知判官（空查询 FPR `-18.2pp`，工作 trap FPR `-33.3pp`）；DeepSeek 实验累计花费 `¥2.71 / ¥10.00`。
  - **S3/S5 合流状态**：基于 S3 已提交 HEAD `de2cc06` 的隔离合流 0 代码冲突、40 项联合回归通过；S3 工作区当前正处于自身合并/修复中，待 S3 最终 HEAD 确定且合并后，再做一次合流后最终联合回归。

## 下一个窗口第一步

1. 待本单 PR 合并后，若继续 `feat/p4-p5-brain-rework`，先在该分支的隔离副本运行 `.venv\Scripts\python.exe scripts/dev/migrate_layout.py --all --check`，确认无目标冲突后再运行 `--all --move --rewrite`；工具会先改写、再移动、生成兼容入口，并在失败时恢复原文件。
2. 读 `docs/ACCEPTANCE.md`，按 Lan 体验结果更新验收勾选并推进 P6-S3/S5/S6/S7。
3. 三项待授权操作（B-3 正式历史恢复、B-4 换 Embedding 重建、H-1 QQ 邮箱）只在 Lan 明确同意后做，且先 dry-run。

## 环境与工具注意

- 正式目录 `.git` 为写保护，**不要在正式目录开分支、提交或改文件**；开发一律在独立 worktree / 副本里做。
- 本机 `git` 走 HTTPS 拉取时可能因 schannel 凭据不可用而失败；本单改用 `gh api` 推送分支，未改任何全局 git 配置。
- 本机 Node 24 用于桌面构建；Python 3.11/3.12 跑测试。

## 红线（不变）

- 不启停正式 Peter、不写正式 `data/peter`、不向正式 Peter 发消息。
- 正式恢复必须先做协同 bundle + dry-run，经 Lan 确认后才 `--apply`。
- 换 Embedding 模型必须用 `scripts/embedding_reindex.py`（先 dry-run 再 `--apply`），不依赖后台重试。
- 不提交人设正文、密钥、真实聊天/记忆、本机绝对路径、日志。
