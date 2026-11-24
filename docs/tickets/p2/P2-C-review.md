# P2-C 自审（2026-11-24，复查完成版）

对照 `docs/tickets/p2/P2-C.md` 逐条核查：

## 1. 标签膨胀源头治理（已修复）

- **问题**：此前仅做了历史标签清理脚本，但 `brain_authority.py` 的 `_hold` 写入路径仍接受并落盘 caller 传入的 `tags` 和 `domain`，导致自由标签持续膨胀。
- **修复**：修改 `brain_authority.py`，在 Brain 作为写入权威时，强制将 `tags` 和 `domain` 置为空列表 `[]`，彻底切断自由标签新增源头。
- **验证**：
  - 单元测试 `test_hold_does_not_write_free_tags_to_brain` 确信验证通过。
  - 隔离 Peter E2E 脚本中故意传入自由 tags，落盘后校验 `tags_json='[]'`，阻断确凿生效。

## 2. 分类方案审计与结论

- 审计结果：按提议的七类映射，仅有 36.8% 的历史记忆能明确对应，高达 63.2% 落在 `unclassified`。
- **结论**：**不采用固定七类方案**，避免制造新的分类冲突。保持纯语义检索 + 结构化元数据字段，`tags` 仅作为只读历史归档。

## 3. 145 条历史记忆核实清单

- 详尽核对正式库 286 条记忆与 69 篇事件故事的关系，生成了核实报告：[`docs/tickets/p2/historical_memories_verification.md`](file:///E:/Peter-Agent/VEIL/veil-agent/docs/tickets/p2/historical_memories_verification.md)。
- **核心发现**：
  - 217 条非事件记忆中，**140 条已作为事件来源被 69 篇事件无损吸收**；
  - 39 条为墓碑/软删除记忆；
  - 仅余 **38 条独立有效记忆**，多为人物独立偏好或见解，建议保留独立，**严禁盲目粗暴归组**。

## 4. 待 Lan 授权事项

- 正式库 `brain.db` 的历史标签清理（`tag_governance.py --write`）需在正式维护窗口由 Lan 授权后执行。
