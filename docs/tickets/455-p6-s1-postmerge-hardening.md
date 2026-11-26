# 455 · P6-S1 合并后加固：换模型待索引恢复与诊断透出

- **属于哪一格**：A 陪伴核心（记忆注入与精修）。
- **做完 Lan 能多做的一件事**：以后给 Peter 换 Embedding 模型时，旧记忆不会永久卡在“待索引”，也不会每 60 秒重复付费算一遍再丢弃；按说明跑一次显式重建即可全部恢复。P4/P5 的诊断数据里能直接看到这一轮是不是共享 Embedding、判官是 embedding 失败还是模型超时、检索降级原因是什么。
- **她怎么验收**：在隔离副本上用两个不同名字的假 Embedding 模型验证“换模型后不再重复付费、显式重建后恢复”；查看 `/api` 诊断数据里出现 retrieval/degraded 字段；不需要她动手跑测试。

## 目标

修复 P6-S1（#452）合并后独立审查发现的两处阻断与若干诊断缺口，不改变已合并行为：

1. **换模型后的待索引（阻断）**：`retry_pending_vectors()` 先对每条 pending 调 Embedding，再在事务里做模型身份校验；模型切换后每次重试都为“必然被拒绝”的向量付费，pending 永远恢复不了，且 `_write()` 在写锁内触发重试。修复：重试前先读 meta 记录模型，不一致立即返回 `blocked=embedding_model_mismatch`（0 次付费、0 写入）；新增唯一显式操作 `scripts/embedding_reindex.py`（默认 dry-run，`--apply` 才清空旧向量、全部标 pending、移除旧模型身份）并配 `brain_writer.prepare_embedding_reindex()`；重建后正常 pending 恢复可用。
2. **诊断透出（阻断）**：新结构化诊断只落 `recall-diagnostics.jsonl`，`web_agent.live_context()` 与 `diagnostics.trace()` 白名单丢弃 `retrieval`/`degraded`，P4/P5 读不到。修复：两处 API 白名单透传这两个字段；桌面 UI 的展示接线按 P6-4 职责边界归 P6-S7（已在工单 452 验收步骤中注明）。
3. **非阻断诊断修正**：判官失败时保留真实候选数（不再覆盖为 0）；共享 Embedding 的实际等待时间写入 `embedding_wait_ms`；关键词降级时保留 `fallback`/`embedding_failure` 原因；`current_model` strip；检索拒绝 `date_from > date_to`。

## 当前状态

- 基线：`origin/main` `beb5fa3`（#450 S2 + #451 S2 加固 + #452 S1 已合并）。
- 改动：`brain_writer.py`（重试前置模型校验、`prepare_embedding_reindex`）、`brain_authority.py`（`_retry_pending` 在付费/写锁前跳过模型不一致）、`memory_agent.py`（等待时间与失败诊断）、`recall_judge.py`（模型名 strip）、`brain_retrieval.py`（日期区间/反转校验）、`web_agent.py`/`diagnostics.py`（API 透传）、`scripts/embedding_reindex.py`、Baseline-1 脚本与报告、新增 `tests/test_p6_s1_postmerge_hardening.py` 7 项回归。
- 验证：7 项新回归全绿；Baseline-1 由 18 项扩为 20 项全部通过（新增“换模型阻断/显式重建恢复”两项）；相关套件与全量套件对照结果见 PR。未改正式数据，未调用正式聊天，未启停正式进程。

## 验收步骤

1. 隔离环境跑 `python -m unittest tests.test_p6_s1_postmerge_hardening -v`：7 条全绿。
2. 跑 `python scripts/p6_s1_baseline.py --out baseline-1.json`：20/20 checks 通过，JSON 含 `model_switch` 组。
3. 用 `python scripts/embedding_reindex.py --db <隔离副本>/brain.db --model <新模型>` 先看 dry-run 数量，再 `--apply`；随后待索引重试恢复且 meta 记录新模型。
4. PR CI 全绿、CodeRabbit 实质意见处理完；正式 `data/peter` 与正式进程不动。
