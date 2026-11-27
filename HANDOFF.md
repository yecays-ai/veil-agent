# HANDOFF

> 更新：2026-11-27 +08:00 · 基线 `main = 504c0a7`（PR #460、PR #461 均已合并）· 当前分支 `ticket/461-p5-s6-gates`（已合流最新 `main` 并完成联合回归；待独立审核/CI/Lan 体验；未合并、未部署）

## 当前停点

- **工单 461 / P5 遗留 + S6 开工门禁**：开发、隔离验证与与 S5 的联合回归完成；未合并、未部署，未触碰正式 `data/peter`。
  - P5：`veil/brain/brain_health.py` 的 H-3（悬空引用 vs 无关联记忆/事件，带类别与判断依据）、H-7（删除 1024 维硬编码，按库内维度 + `meta.embedding_model` + 配置模型身份对账）、`period`（日期段增长/重复/碎片/断链/索引）、`historical_state`（只读快照，缺失即声明）；`veil/server/web_agent.py` 传入配置模型身份；`veil/brain/brain_p4_api.py` 成长报告期间摘要（与 S5 的同文件改动已自动合流）；`desktop/workspace-brain.js` 期间卡片、范围标签、证据依据（与 S5 的联想开关同文件改动已自动合流）。
  - S6：`veil/brain/learned_context.py` 聊天证据增量索引/400 条 LRU/旧证据按需查询/失效重建；`_EvidenceBudget` 统一墙钟预算；`event_ledger`/`quotes`/`sources` 分页与截断降级；反证去重 + 120 条上限 + `counterevidence_overflow`；持久化写入幂等回执 + 状态守卫；`veil/brain/relationship_semantic.py` 接受单层 code fence 的严格 JSON。
  - 文档与实验：`docs/tickets/461-p5-s6-gates.md`、`docs/reports/461-test-evidence-20261127.md`、`docs/reports/461-chat-index-benchmark-20261127.json`、`docs/reports/461-cross-language-live-20261127.md`、`veil-lab/runs/461-sem-synthetic-20261127T210905/reports/latest.md`。
  - 测试：新增 4 个 Python 测试文件（7+11+7+4 项）与 1 个桌面测试；合流 S5 后 126 项联合定向回归、58/58 桌面测试通过；完整套件 2469 项失败集合与 `origin/main` 基线一致（18 失败 + 3 错误：Maintenance/OpenCode/APPDATA desktop-data），无本单新增回归；详见 `docs/reports/461-test-evidence-20261127.md`。
  - 真实模型实验：DeepSeek `deepseek-flash` 2 次调用，本 run ¥0.00236848，累计本单语义探针 ¥0.0509（≤¥2）；无共同锚点翻译候选 `contradicted` 不注入，无关正例 `supported` 并注入。
  - 未测：主聊天模型自行提取候选的完整真实聊天链路；S6 功能本身（权重演化、衰减、巩固、自主联想）未开工。
- **工单 460 / PR #461（P6-S5）**：已合并为 `504c0a7`。保留 `stopwords_enabled=False`、`candidate_k=8`、情境判官、`association_enabled=True`、`association_contextual_enabled=False` 默认；记忆监控页双开关持久化；VEIL Lab `gemini-4-hydrogen` 三路盲评（3 组 9 回复，¥1.10）记录在 `docs/reports/p6-s5-retrieval-judge-association-20261127.md`。**未部署、无 Lan 验收记录。**
- **工单 459 / PR #460（P6-S3）**：已合并为 `f979ca2`；未部署、无 Lan 验收记录，按 `docs/ACCEPTANCE.md` B-7 体验。

## S6 门禁状态

| 门禁 | 状态 | 证据 |
| --- | --- | --- |
| 聊天历史增量索引/有界缓存/旧证据按需查询 | 已关闭 | `tests/test_p6_s6_chat_evidence_budget.py`；5 万条基准 0.256s 首建 / 0.435ms 重复 / 0.824ms 追加 |
| 实时证据统一墙钟预算、分页与安全降级 | 已关闭 | 同上（真实 SQLite 排他锁、旧 200 行窗口外证据、截断→unavailable） |
| 追加重试持久化幂等 | 已关闭 | `tests/test_p6_s6_idempotent_writes.py`（丢响应/重启/4 线程并发/状态守卫） |
| 反证长期积累有界、不丢相关否决 | 已关闭（安全降级） | 去重 + 120 条上限 + `counterevidence_overflow`；不截断 |
| 任意跨语言同义否决与无关正例 | 已关闭（能力边界内） | 单测 + DeepSeek 合成实验 `docs/reports/461-cross-language-live-20261127.md` |
| 情境自然联想隐式 `trap` 穿透 + 同轮判官联合门控 | **未关闭**（S5 延期，不在 461 范围） | `docs/tickets/460-p6-s5-retrieval-judge-association.md`；`association_contextual_enabled=False` 默认关闭 |

## 下一个窗口第一步

1. 核对本单 PR（待开）的 CI 与独立审核；重点看 `learned_context.py` 的证据预算/幂等回执、健康页期间卡片，以及合流 S5 后的健康页与记忆监控页是否互相覆盖。
2. 合流结果已在 `504c0a7` 上重跑定向与完整套件；若后续 `main` 再变，重跑：
   - `python -m unittest tests.test_p5_health_period_orphans tests.test_p6_s6_chat_evidence_budget tests.test_p6_s6_idempotent_writes tests.test_p6_s6_cross_language_veto`
   - `python -m unittest tests.test_relationship_422 tests.test_p6_s3_relationship_governance tests.test_pr460_sovereignty tests.test_relationship_semantic_worker tests.test_p6_s5_retrieval_judge_association`
   - `node --test desktop/tests/*.test.cjs`
3. 按 Lan 体验结果更新 `docs/ACCEPTANCE.md` B-8/B-9/B-10；不要自行合并或部署。
4. S6 功能另开单：先处理 S5 延期的隐式工作 `trap` 联合门控，再以工单 461 关闭的五项门禁为前置；不复用本单分支继续施工。

## 本单已知边界

- 聊天索引首次建立仍顺序读完现有 `chat.jsonl`（5 万条/6.68MB 约 0.256s，一次性）；不跨进程持久化。
- 同长度原地改写若只落在 5 个 64B 采样窗口之外且 mtime/size 都不变，理论上仍可能命中旧缓存；正常追加、删除、截断、替换与两端改写均失效。与现有 `Store.incremental_state` 的检测边界一致。
- 反证超过 120 条唯一文本安全降级为 `counterevidence_overflow`，需要 Lan 清理/合并旧否决；不静默截断。
- 健康期间状态只依据库内时间字段与既有每日快照；无快照不还原历史。
- 跨语言语义核验受模型行为影响，不承诺任意译写 100% 识别；失败保留 `uncertain`。
- `evidence_budget_ms` 目前是 `profile` 可选配置（100–10000，默认 1200），未加入桌面设置页。

## 环境与工具注意

- 正式目录 `.git` 为写保护，不在正式目录开分支、提交或改文件。
- 本机 `git` 走 HTTPS 拉取时可能因 schannel 凭据不可用而失败；可用 `gh api` 推送分支，不改全局 git 配置。
- 本机 Node 24 用于桌面测试；Python 使用 `veil-agent/.venv`（本 worktree 无独立 venv）。
- 既有失败（Maintenance CRLF、OpenCode 未安装、APPDATA desktop-data）在 `origin/main` 基线同样复现，不是本单引入。

## 红线（不变）

- 不启停正式 Peter、不写正式 `data/peter`、不向正式 Peter 发测试消息。
- 正式恢复必须先协同 bundle + dry-run，经 Lan 确认后才 `--apply`。
- 换 Embedding 模型必须用 `scripts/embedding_reindex.py`（先 dry-run 再 `--apply`），不依赖后台重试。
- 不提交人设正文、密钥、真实聊天/记忆、本机绝对路径、日志；VEIL Lab 快照含真实数据，只留本地。
