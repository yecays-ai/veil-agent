# P2-B 自审（2026-11-24）

## 已完成（隔离）

- 开场/搜索/context、判官/assisted 向量、页面/版本/事件读取接 Brain。
- `test_recall_*.py` 234 条通过；Brain 组测试通过。
- 隔离 Ombre 回退演练（前一轮会话）：P2-C 清理后的副本 → 新增/修改/删除 canary → 同 ID 导出 →
  启动独立 Ombre → 新增+修改可搜到、删除不复活。证据不提交仓库。

## 质量证据与局限

- 50 条真实聊天旧注入 A/B：Brain 24/50，旧 Ombre 14/50；延迟 173 ms vs 482 ms。
- 该集没有人工真值，且不能覆盖全部场景；不足以单独证明长期质量。
- 负向问题（无答案）没有可靠拒绝证据，不能宣称通过。

## 新增验收（本轮）

- 页面读取方法级形状测试：panel 字段、versions、archived 通过。
- 备份/恢复测试：digest、integrity、counts、字段一致。
- 失败降级测试：Brain 搜索异常回退到旧 backend，记录 fallback。
- 旧机制审计表：`docs/tickets/p2/P2-B-old-mechanisms.md`；后台经验整理/事件测试 54+28 条通过。
- C 数据回退：`p2b_rollback_check.py` 实际启动 Ombre，288 记忆/284 版本/171 sidecar/1 tombstone/72 事件，
  canary 新增+修改可搜到、删除不复活。

## 未完成

- 页面 API 仅方法级形状测试，未做前端端到端；旧机制审计见 `P2-B-old-mechanisms.md`，后台经验整理未跑通；
  备份恢复/失败降级/字段核验有单测，但不是完整切换验收；P2-C 数据回退仅有隔离脚本，未在正式切换路径验证。
- 切换前冻结机制：`scripts/p2b_freeze.py` 创建 `brain-write-freeze`，authority 拒绝新的
  hold/trace；导出后解冻。正式切换协议仍未执行，由 Lan 授权。
- 事件/碎片不重复注入未在真实聊天中单独验收。

结论：P2-B 读取与隔离回退可用，但按工单尚未完成，不能合并后直接切换。
