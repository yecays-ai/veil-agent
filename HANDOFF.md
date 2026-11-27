# HANDOFF

> 更新：2026-11-28 +08:00 · 基线 `main = 81dba35`（PR #461 / S5、PR #462 / 工单 461、PR #463 / S6-2 均已合并）· 当前分支 `ticket/462-p6-s6-1-memory-reinforcement` / worktree `worktrees/462-p6-s6-1` / PR #464（已合流含 S6-2 的 `main`；待独立审核、CI 与 Lan 体验；未合并、未部署）

## 当前停点

- **工单 462 / P6-S6-1 记忆动态评价、强化与保护性衰减**：开发、单元/回归与 VEIL Lab 真实链路验证完成；已合流含 S6-2 的 `main = 81dba35`；未合并、未部署，未触碰正式 `data/peter`。
  - 送达记账：`veil/brain/memory_weights.py`（新）+ `veil/brain/storage/brain_writer.py` 三张派生表；`Memory.confirm_delivery(request_id, session_key, regenerated)` 只在 S5 确认送达后调用；`veil_agent._finalize_recall_delivery()` 只多传会话键/重生成标记（保留 S6-2 的锁内 finalize 与自主快照）。
  - 保护性衰减：`effective_score()` 仅对 `importance<=6` 且未置顶的活跃度投影按半衰期衰减；`importance>=7`/`pinned` 永不衰减；不改重要度、不删除、不隐藏。
  - 长期重要度：`memory_importance_proposals` 带原因、证据、`policy_version`；自动只提升 +1 且上限 7；降权/8+/锁定/置顶进 `pending`；应用写 `versions`、可撤回；S6-3 统一入口 `MemoryWeights.propose()`。
  - 检索：`brain_retrieval.py` 增加可选的活跃度排序项，默认关闭；无送达记录时严格无操作；S6-2 的 leisure 向量捕获与召回路径已合流。判官/联想/召回预算选择逻辑未改。
  - 界面：`desktop/workspace-brain.js` 记忆监控权重面板（5 开关 + 4 参数，持久化到 `data/<role>/memory-weights.json`）、记忆详情重要度/活跃度/待确认重评/重评历史/撤回、库过滤与排序；已与 S6-2 的「自主闲暇自然联想」开关同文件合流；`brain_p4_api.py` / `web_agent.py` 新增对应 API。
  - 测试：`tests/test_p6_s6_memory_weights.py`（26 项）通过；合流 S6-2 后桌面 63/63；合流后完整套件与 `81dba35` 基线对比结果见报告与 PR #464。
  - VEIL Lab：run `s6-1-activation-20261128T004338`（快照 `peter-20261127T170245/r3`，mock，¥0，代码 sha `44ddcdff...`），`run verify` 通过；送达/幂等/回滚/重启/衰减/5→6 自动重评均已观察；报告 `docs/reports/p6-s6-1-memory-reinforcement-20261128.md`。
  - 文档：`docs/tickets/462-p6-s6-1-memory-reinforcement.md`、本文件、`STATUS.md`、`docs/ACCEPTANCE.md` B-12。
- **工单 463 / P6-S6-2 自主闲暇自然联想**：已合并 `81dba35`（PR #463，2026-11-27 17:05 UTC）；默认关闭、**未部署、无 Lan 验收记录**（B-11）。实现自主态禁旁白/无工具扮演约束与审计、严格闲暇 vs 任务态门控、复用 S5 相关性/多源池/个人意义/冷却去重的长尾联想、自主送达确认与 S6-1 监听联动、Brain 独立开关与运行诊断；119 项 Python 与桌面 3/3 定向回归、VEIL Lab `run verify` 通过。与本单在 `memory_agent.py` / `veil_agent.py` / `workspace-brain.js` / `brain_p4_api.py` 相交处已合流，保留双方功能。
- **工单 461 / PR #462（P5 健康巡检 + S6 开工门禁）**：已合并 `ead71b6`（2026-11-27 15:38 UTC）；**未部署、无 Lan 验收记录**，验收见 ACCEPTANCE B-9/B-10。
- **工单 460 / PR #461（P6-S5）**：已合并 `504c0a7`；未部署、无 Lan 验收记录（B-8）。
- **工单 459 / PR #460（P6-S3）**：已合并 `f979ca2`；未部署、无 Lan 验收记录（B-7）。

## S6 门禁与 S6-1 状态

| 事项 | 状态 | 证据 |
| --- | --- | --- |
| S6 五项工程门禁（工单 461） | 已关闭 | `docs/tickets/461-p5-s6-gates.md`；对应测试与实验记录 |
| S6-1 送达激活与保护性衰减（工单 462） | **开发侧完成，待审核/CI/体验** | 26 项单测；桌面 63/63；Lab run `s6-1-activation-20261128T004338` |
| S6-1 版本化重要度重评（工单 462） | **开发侧完成，待审核/CI/体验** | 同上；`tests/test_p6_s6_memory_weights.py` 提案/锁定/撤回用例 |
| S6-2 自主闲暇联想（工单 463） | 已合并 `81dba35`，默认关闭；未部署、待验收 | `docs/ACCEPTANCE.md` B-11；只改联想/自主路径，未改本单送达记账 |
| S6-3 认知巩固 | 未开工 | 本单已交付 `MemoryWeights.propose()` 统一接口 |
| S5 延期：隐式工作 `trap` 穿透 + 同轮判官联合门控 | **未关闭** | `docs/tickets/460-p6-s5-retrieval-judge-association.md`；S6-1 不依赖它，S6-2 已合并，S6-3 开工前仍需处理 |

## 下一个窗口第一步

1. 核对 PR #464 的 CI 与独立审核；重点看 `memory_weights.py` 的事务/幂等/锁定守卫、`confirm_delivery()` 接线、`library()` 投影和记忆监控页是否与 S6-2 改动冲突。
2. `main` 若再变，重跑：
   - `python -m unittest tests.test_p6_s6_memory_weights`
   - `python -m unittest tests.test_p6_s5_retrieval_judge_association tests.test_brain_retrieval tests.test_brain_writer tests.test_brain_store tests.test_p4_p5_brain_routes tests.test_p6_s6_chat_evidence_budget tests.test_p6_s6_idempotent_writes tests.test_p3_1_brain_only`
   - `node --test desktop/tests/*.test.cjs`
   - 完整套件对比 `origin/main` 失败集合，不得新增失败。
3. 按 Lan 体验结果更新 ACCEPTANCE B-12；**不要自行合并或部署**。
4. S6-2 已合并，边界记录：只改 `natural_association.py` / 自主唤醒 / 相关提示词；合流时保留本单的送达记账、权重面板与记忆详情区域；共享函数（`confirm_delivery()`、`library()`）改动先在本 PR 评论说明。S6-3 用 `MemoryWeights.propose()`，不要绕过锁定/版本策略。

## 本单已知边界

- 活跃度排序默认关闭（#455 `AB-R5b` 证据不足）；无送达记录时开关严格无操作，但排序净收益尚未证明。
- 重评只在送达后/手动/API 触发，没有夜间定时批处理；多日灰度与巩固由 S6-3 负责。
- 自动调整只到 7；8+、降权、锁定/置顶都需 Lan 确认，不会自动降权重要共同经历。
- 衰减是展示/可选排序的派生分，不写回 `importance`，不删除/隐藏/归档。
- 投递事件默认保留 20000 条（可调），淘汰只删最旧事件，聚合计数不回退。
- 实验关系条目 `s61-lab-rel` 与合成记忆 `s61laba/b/c` 只在 veil-lab run 内；未提交、未外发。
- 未验证：真实主聊天模型下与判官的联合行为、S6-2 合流后的页面布局（已做 jsdom 结构验证、未真机点击）、多日真实日常的重评节奏、Windows 真机 UI 手工体验。

## 环境与工具注意

- 正式目录 `.git` 写保护，不在正式目录开分支、提交或改文件。
- 本机 `git` 走 HTTPS 拉取可能因 schannel 凭据失败；可用 `gh api` 推送分支，不改全局 git 配置。
- 本机 Node 24 用于桌面测试；Python 使用 `veil-agent/.venv`。
- VEIL Lab：run `s6-1-activation-20261128T004338` 的代码是从本 worktree 复制到 run 内 `code/` 并改 `run.json.code_root`（不是快照钉住代码）；`run verify` 因快照代码 `2a264a7` 早于 S3，额外加了一条合成 Lan 关系条目（`s61-lab-rel`）才通过；该 run 的所有写入都在 lab 内。
- 临时基线 worktree `worktrees/462-baseline`（detached `81dba35`）用于失败集合对比，可删除；删除前确认无未提交修改。
- 既有失败（Maintenance CRLF、OpenCode 未安装、APPDATA desktop-data）在基线同样复现，不是本单引入。

## 红线（不变）

- 不启停正式 Peter、不写正式 `data/peter`、不向正式 Peter 发测试消息。
- 正式恢复必须先协同 bundle + dry-run，经 Lan 确认后才 `--apply`。
- 换 Embedding 模型必须用 `scripts/embedding_reindex.py`（先 dry-run 再 `--apply`），不依赖后台重试。
- 不提交人设正文、密钥、真实聊天/记忆、本机绝对路径、日志；VEIL Lab 快照含真实数据，只留本地。
