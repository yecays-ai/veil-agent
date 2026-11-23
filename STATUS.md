# 当前状态

## 工单 429 · VEIL Brain P0-B：安全影子同步（本 PR，待 Lan 审核）

- 前置：P0-A 已验收（隔离导入与对账）。
- 已施工：新增 `brain_sync.py`（outbox + 后台 worker + 周期哈希对账）与 26 条用例；`Memory.call()`
  在确认写入后只入队，影子故障不影响 Ombre 保存；幂等 upsert 不产生重复行；删除：源文件消失且账本无内容时，影子库只留空墓碑并清掉正文/版本/向量（跟随 Ombre 的擦除承诺）；扫描不完整时不推断删除。
- 报告区分「已知历史引用」（#426 删掉的 7 个 feel id，含 7 个事件来源与 8 条 sidecar 链接）与意外差异；
  影子库不反向写、不自动修复。审查后补：向量双向差异与安全删除、bucket 缺失不误删、sidecar 重写重建状态、事件差异计入意外。开关：`[memory.brain] shadow` 与 `brain_sync.py shadow off/on`。
- 未合并、未在正式库启用：合并后先用 `brain_sync.py init --source <隔离副本>` 建 `data/peter/brain.db`，
  重启后开始 7 天观察；P1 不启动。
- 验收：`docs/tickets/429-veil-brain-p0b.md` 的 6 项 + 7 天逐项对比。
