# P2-B 切换/回退演练手册（隔离）

**禁止在正式 Peter 上执行；所有步骤用独立副本/端口，Lan 授权后才可切换。**

## 切换前

1. 冻结写入（Lan 授权窗口）：
   - `python scripts/p2b_freeze.py freeze --data-root <隔离 data>`；写文件存在时 authority 会拒绝新的
     hold/trace；本次演练已验证冻结后写入被拒绝。
   - 备份：`python scripts/p2b_cutover.py backup --db <隔离>/brain.db --out <备份>`。
   - 检查：`python scripts/p2b_cutover.py check --db <隔离>/brain.db --out <报告>`，要求
     `digest_ok=true`、`pending_vectors=0`、`event_stories=事件数`、`memories_with_tags=0`。
   - 本次演练：287→289 记忆、284→288 版本、72 事件/73 阶段、0 pending、tags=0，digest 一致。
2. 最终对账：同 ID 导出 + `verify` 回导 0 problems；本次 289 记忆/288 版本/177 sidecar 导出 0 problems。
3. 故障中止：在副本里制造 pending 或篡改导出，`check`/`digest` 必须不通过并停止切换；本次两种都验证 abort。
4. 锁定单一权威：config `write_authority=true` 且 `shadow=false`；两处同时开启会被拒绝。

## 切换

5. 启动 Brain 写入的独立实例，执行新增/修改/删除/隐藏/锁定/归档/事件，重启，页面/召回正常；
   本次全部通过，重启后数据保持。
6. 只有全部通过才让正式 Peter 使用；失败立即回退，不双写。

## 回退

7. 导出：先确认冻结，再执行 `python scripts/p2b_cutover.py export --db <隔离>/brain.db --out <vault>`；
   导出后解冻才恢复 Brain 写入。
8. 校验：`verify` 回导 0 problems；启动**独立 Ombre**（独立 vault/端口）读取同一批新增/修改/删除；
   本次修改 canary 可搜到、hard delete 不复活、72 事件故事在导出中，Ombre 上 save/modify/search 成功。
9. 全部通过才把正式 backend 切回 ombre；正式切换仍由 Lan 授权。

## 当前状态

- 隔离演练已通过（证据：仓库外 `p2-439/iso/evidence/cutover/cutover-rollback.json`）。
- **正式切换已于 2026-11-25 执行完成**：见 `P2-formal-cutover-report.md`；正式库标签已清理，
  `EMBED_API_KEY` 已验证有效，Brain 为唯一读写权威，Ombre 18011 保留为回退。
- 回退导出已生成并回导 0 problems；正式体验验收前不进入 P3，不卸载 Ombre。
