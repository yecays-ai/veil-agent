# P2 验收步骤（Lan）

## 1. 已交付/默认行为

- P2-A：Brain 独立写入、完整事件、同 ID 导出、备份恢复；默认 `write_authority=false`。
- P2-B：读取接 Brain、隔离 Ombre 回退演练；正式切换未执行。
- P2-C：标签清理工具与隔离执行；正式库未清理。
- 不提交真实聊天/记忆正文；真实运行证据只留在本机仓库外。

## 2. 隔离复核命令

- `python -m unittest discover -s tests -p 'test_brain_*.py'`：60 条。
- `python -m unittest discover -s tests -p 'test_memory_panel.py'`：8 条。
- `python -m unittest discover -s tests -p 'test_recall_*.py'`：234 条。
- `tag_governance.py apply --db <隔离副本>/brain.db --write`：仅在副本执行。

## 3. 质量口径（不夸大）

- 50 条真实聊天旧注入 A/B：Brain 24/50，旧 Ombre 14/50；延迟 173 vs 482 ms。
- 该集无人工真值；负向问题没有可靠拒绝数据，不能宣称负向通过。
- 不足以单独证明“不低于 Ombre”，正式切换前需你体验确认。

## 4. 切换/回退

- 切换窗口、备份、冻结、对账、锁单写、切换、验证：见 `docs/tickets/p2/P2-B-runbook.md`。
- 回退：导出同 ID vault → 启动独立 Ombre → 核对新增/修改/删除；已演练，但正式切换未执行。
- 任何异常回退，不丢切换期新记忆；P2-B 未通过前不进入 P3。
