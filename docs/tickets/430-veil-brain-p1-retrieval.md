# 工单 430 · VEIL Brain P1：自有检索引擎

## 背景

P0-A 建立了 `brain.db`，P0-B（#429，已合并 `b1e51f1`）让影子库跟随 Ombre 的写入。P1 只换**自动召回的候选来源**：
用 Brain 自己的向量 + 中文词法检索，替代 `breath_search` 的候选；召回判官、去重、冷却、预算和正式
读写链都不动。默认仍走 Ombre。

## 目标

- 新模块 `brain_retrieval.py`：从 `brain.db` 读可见记忆与 1024 维向量；jieba 分词 + rank-bm25 关键词
  通道；向量余弦与 BM25 归一化融合；输出与 Ombre 相同的文本契约（`[bucket_id:…]` + result-ids 块），
  上层 `ordinary_hits()` 不用改。
- `Memory.search_candidates()`：`[memory.retrieval] backend = "ombre" | "brain"`，默认 ombre；
  brain 失败时自动回退 Ombre，并把原因写进诊断，不隐藏问题。
- 过滤：只取 `dynamic/permanent`，排除墓碑、已删除、隐藏、`dont_surface`、归档、letter/plan/i/feel；
  召回后仍走原有可见性复核。
- 回放工具 `scripts/brain_retrieval_replay.py`：645 条真实 query 对比新旧候选、列出旧有新无、新有旧无、
  延迟；重合率只作为诊断，不作为达标门槛。
- 依赖：jieba / rank-bm25 / numpy 进 `requirements.txt` 与 `requirements.lock.txt`。

## 验收（按 Lan 2026-11-23 修订）

1. 645 条真实 query：对比正确命中、重要遗漏、无关召回、延迟；重合率只记录。
2. 11 题盲测按证据评分，不低于旧系统基线。
3. 真实场景覆盖具体事件、人物、时间、关系经历和不同措辞；重点看能否准确找到相关记忆。
4. 隐藏/删除/墓碑/不应展示的记忆不得进入正常召回。
5. 默认 `backend = "ombre"`；隔离回放验收通过前不启用为正式检索；切换后必须能改回。
6. 若评估无法证明 Brain 不低于 Ombre，就保留旧检索，不为完成工单强行切换。

## 边界与回退

- 只换候选来源：不改判官、去重、冷却、预算、事件层、记忆页和正式写入。
- 回退：配置改回 `backend = "ombre"` 即恢复原检索，不需要改数据。
- 回放脚本只读 `brain.db` 与本地账本，不写记忆、不调付费接口以外的东西；向量通道需要
  `EMBED_API_KEY`，缺失时报告会注明「关」。
