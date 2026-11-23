# 当前状态

## 工单 431 · P4 开源记忆界面复用（本 PR，待 Lan 体验验收）

- 施工完成：`desktop/brain/` 只读原型（brain.db 兼容 API + vendored mnemory 前端）、桌面菜单入口和独立窗口；
  工单 428 的 `brain.db` 作为唯一数据源，P0-A 的 schema 语义未改。
- 实测真实数据：244 条非删除记忆（另有 39 条墓碑）、281 条版本、244 条向量；窗口能浏览、搜索、看正文/状态/来源/版本。
  健康检查为本地确定性检查，当前查出 5 个问题（4 个事件来源断链、1 条隐藏记忆），与 428 记录的 7 个悬空来源一致。
- 验证：`desktop` 下 `npm test` 38 条通过（新增 `tests/brain.test.cjs`，含写入 403）；Electron 实测窗口能打开。
- 开源结论：选 mnemory 前端（Apache-2.0）做只读适配；其余候选与被否决理由见工单。
- 前置：工单 428 P0-A 已验收（见 429 工单记录）。
- 下一步：Lan 按 `docs/tickets/431-brain-ui-reuse.md` 验收；通过后再决定是否继续适配，以及补召回诊断（候选/判官/注入）。
- 本单未做：写入、修复、语义检索、召回诊断、P0-B 影子同步、P1 检索；没有改 Peter 的聊天或记忆保存链。

## 工单 429 · VEIL Brain P0-B：安全影子同步（本 PR，待 Lan 审核）

- 前置：P0-A 已验收（隔离导入与对账）。
- 已施工：新增 `brain_sync.py`（outbox + 后台 worker + 周期哈希对账）与 26 条用例；`Memory.call()`
  在确认写入后只入队，影子故障不影响 Ombre 保存；幂等 upsert 不产生重复行；删除：源文件消失且账本无内容时，影子库只留空墓碑并清掉正文/版本/向量（跟随 Ombre 的擦除承诺）；扫描不完整时不推断删除。
- 报告区分「已知历史引用」（#426 删掉的 7 个 feel id，含 7 个事件来源与 8 条 sidecar 链接）与意外差异；
  影子库不反向写、不自动修复。审查后补：向量双向差异与安全删除、bucket 缺失不误删、sidecar 重写重建状态、事件差异计入意外。开关：`[memory.brain] shadow` 与 `brain_sync.py shadow off/on`。
- 未合并、未在正式库启用：合并后先用 `brain_sync.py init --source <隔离副本>` 建 `data/peter/brain.db`，
  重启后开始 7 天观察；P1 不启动。
- 验收：`docs/tickets/429-veil-brain-p0b.md` 的 6 项 + 7 天逐项对比。
