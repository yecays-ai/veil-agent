# P2 验收步骤与证据（Lan）

## 1. 已交付/默认行为

- P2-A：Brain 独立写入、完整事件、同 ID 导出、备份恢复；默认 `write_authority=false`。
- P2-B：读取接 Brain、页面兼容、质量/连续性/失败/旧机制/切换与 Ombre 回退；正式切换未执行。
- P2-C：标签清理工具与隔离执行；正式库未清理。
- 不提交真实聊天/记忆正文/密钥；真实运行证据只留在本机仓库外。

## 2. 完整验收矩阵

逐项验收项、执行场景、预期/实际、证据位置、通过状态见
[`P2-acceptance-matrix.md`](P2-acceptance-matrix.md)；历史逐条清单见
[`historical-memories-verification.md`](historical-memories-verification.md)。

仓库外隔离证据包（不提交）：`p2-439/iso/evidence/`，包含
`data/migration-verification.json`、`desktop/desktop-e2e-result.json`、`restore/backup-restore-result.json`、
`failure/failure-direct.json`、`failure/failure-chat.json`、`quality/quality-summary.json`、
`continuity/continuity-summary.json`、`cutover/cutover-rollback.json`、`legacy/legacy-convergence.json`。

## 3. 隔离复核命令

- `python -m unittest discover -s tests -p 'test_brain_*.py'`：78 条。
- `python -m unittest discover -s tests -p 'test_recall_*.py'`：234 条。
- `cd tests && python -m unittest test_memory_panel`：8 条。
- `python -m unittest tests.test_web tests.test_veil_brain`：83 条。
- `tag_governance.py apply --db <隔离副本>/brain.db --write`：仅在副本执行。

## 4. 质量口径（不夸大）

- 50 条真实聊天旧注入 A/B（top-8）：Brain 29/50，Ombre 16/50；覆盖率 36.0% vs 22.8%；
  延迟 187 vs 517 ms；4 条 Ombre-only 残留。
- 8 条无答案问题最高绝对余弦 0.535（<0.6）；真实无答案聊天 judge=empty、injected=[]。
- 该集没有人工“重要度”真值；正式使用前需 Lan 体验确认。

## 5. 切换/回退

- 切换窗口、备份、冻结（`scripts/p2b_freeze.py`）、对账、锁单写、切换、验证：
  见 [`P2-B-runbook.md`](P2-B-runbook.md)。
- 隔离演练已通过：冻结/备份/导出 0 problems/异常中止/单写切换/重启/同 ID 导出/独立 Ombre 启动/
  Ombre 上保存修改检索；已 hard delete 的记忆不复活。
- 正式切换未执行；需 Lan 授权并先更新正式 `.env` 的 `EMBED_API_KEY`（当前 401）。
- 任何异常回退，不丢切换期新记忆；正式体验验收前不进入 P3。

## 6. 阻塞项

见 [`P2-acceptance-matrix.md`](P2-acceptance-matrix.md) §4：正式切换授权、正式库标签清理窗口、
正式 embedding key 失效、页面隐藏按钮（#431）。
