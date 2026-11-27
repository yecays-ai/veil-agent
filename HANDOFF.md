# HANDOFF

> 更新时间：2026-11-27T15:10:00+08:00 · 基线 `main` = `2a264a7`（PR #458 已合并）· 当前分支 `ticket/460-p6-s5-retrieval-judge-association`（工单 460 · P6-S5 实现、隔离实验与回归测试完成，待 PR 审核与 Lan 三路盲评验收；未合并、未部署正式目录）

## 当前停点

- **本单（工单 460 · P6-S5 检索、判官与情境相关自然联想升级）**：
  - **检索与候选池**：`veil/brain/recall/brain_retrieval.py` 新增默认关闭的可审计停用词表（`CHINESE_STOPWORDS`，保护日期/否定/人称/约定词，单测守卫零交集）及显式 `candidate_k`（默认 8，不放大 `vector_candidates=8`）。DeepSeek 36 样本四组对照证实 `sw_on_k8` 全指标净正收益，`K=12` 精确率下跌不默认开启。
  - **判官与情境相关性**：新增零付费 `veil/brain/recall/context_signal.py`，`recall_judge.py` 注入受限近期对话与工作/情感分流提示，`memory.judge.prompt.md` 维持保守底盘并区分纯技术排查与工作情绪倾诉（空查询 FPR `-18.2pp`，工作 trap FPR `-33.3pp`）。
  - **带情境约束自然联想**：`natural_association.py`、`recall_rotation.py`、`memory_agent.py` 新增独立默认关闭候选开关 `association_contextual_enabled`、按 `brain.db` 签名自动失效的有界多源候选池（长尾 `imp>=7` + 普通生活经历）、共享向量余弦相似度硬门控（`min_similarity=0.52`），唯一覆盖 `2→18` 条、工作干扰 `0`；6 组三路匿名盲评材料落盘于本机 `.local-tmp/s5/lan-blind-review-s5.md`（不入库）。
  - **Reranker 评估与契约**：本机 `.venv` 无本地重型推理依赖，按红线保持关闭；交付 `veil/brain/recall/reranker_contract.py`（`threshold=0.25` 拦截 39.3% 空查询 + 异步 `request_id`/`topic_hash`/`db_signature`/版本/权限过期核对）。
  - **跨轮连续性与送达/回滚契约**：修复预算淘汰联想污染冷却/连击问题；明确 `retrieved/judged/injected/delivered/effective_used` 五层生命周期，`veil_agent.send()` 送达后调 `confirm_delivery()` 通知 S6 监听钩子，失败/取消完整回滚。
  - **配置与诊断**：通过 `config_registry.py`、`brain_p4_api.py`、`desktop/workspace-brain.js` 统一投影与展示，零平行系统。
  - **测试与费用**：340+ 项单元/回归/布局/recall-lab 测试全绿；DeepSeek 实验累计花费 `¥2.44 / ¥10.00`；未触碰正式 Peter 与正式数据。
  - **与 S3 并行**：S5 未触碰 S3（工单 459）独占文件；合流后需与 S3 做一次联合回归。

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
