# 当前状态

## 工单 430 · VEIL Brain P1：自有检索引擎（本 PR，默认 ombre，等回放评估）

- 新增 `brain_retrieval.py`：向量 + jieba/BM25 融合，输出与 `breath_search` 相同的文本契约；
  `Memory.search_candidates()` 按 `[memory.retrieval] backend` 选择，默认 `ombre`，brain 失败自动回退。
- 只换自动召回的候选来源；判官、去重、冷却、预算、事件层和正式读写链未动。
- 回放工具 `scripts/brain_retrieval_replay.py` 对比真实 query；重合率只作诊断，不作门槛。
- 下一步：合并 #429 后 seed `data/peter/brain.db`，跑回放与 11 题盲测；证明不低于 Ombre 才切，
  否则保留旧检索。

## 工单 429 · VEIL Brain P0-B：安全影子同步（已合并 `b1e51f1`）

- 影子库跟随 Ombre 写入、周期哈希对账、已知历史引用与意外差异分开报告；开关 `[memory.brain] shadow`。
- 生产 `data/peter/brain.db` 尚未初始化和挂载；验收步骤见 docs/tickets/429-veil-brain-p0b.md。

## 工单 428 · VEIL Brain P0-A：隔离导入与对账（已合并 `d9406e7`，Lan 已确认）

- 283 记忆 / 281 版本 / 244 向量 / 69 事件映射导入 `brain.db`；正式库前后校验和一致。
