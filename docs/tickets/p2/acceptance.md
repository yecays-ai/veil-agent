# P2 验收步骤（Lan）

## 1. 代码/工单

- 已合并：P2-A #434、P2-B #436、P2-C #435。
- 自审：`docs/tickets/p2/P2-A-review.md`、`P2-B-review.md`、`P2-C-review.md`。
- 默认 `write_authority` 关闭，正式 Peter 仍是 Ombre 写 + Brain 召回。

## 2. 隔离证据（可复核）

- P2-A：`python -m unittest discover -s tests -p 'test_brain_*.py'`（60 条）与 memory panel 8 条。
- P2-B：`scripts/p2b_ab_quality.py` 50 条 A/B；`scripts/p2b_rollback_check.py` 实际启动 Ombre 回退。
- P2-C：`tag_governance.py` 隔离副本清理；报告在本地 `.local-tmp/p2c/`。

## 3. 你要体验/决定

1. 是否认可 A/B 结果与回退演练，允许进入正式切换窗口。
2. 切换窗口内 Peter 会暂停保存约 2–3 分钟；由你决定时间。
3. 切换后你实际保存/修改/删除/隐藏/锁定/归档/事件各一次，再决定是否继续 P3。
4. 任何异常按 `P2-B-runbook.md` 回退；回退不依赖旧备份，不清空切换后新记忆。

## 4. 边界

- 开发者不自行切换、不重启正式 Peter、不写正式记忆。
- P2 未通过你的体验验收前，不报告 P2 完成，不进入 P3。
