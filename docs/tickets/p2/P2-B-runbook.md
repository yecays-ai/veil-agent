# P2-B 切换/回退演练手册（草案，隔离）

**禁止在正式 Peter 上执行；所有步骤用独立副本/端口，Lan 授权后才可切换。**

## 切换前

1. 冻结旧写入（Lan 授权窗口）：停止 Ombre 接收新写入，确认无进行中的保存。
2. 备份：`python scripts/p2b_cutover.py backup --db data/peter/brain.db --out <备份>`。
3. 检查：`python scripts/p2b_cutover.py check --db data/peter/brain.db --out <报告>`，要求
   `digest_ok=true`、pending_vectors=0、event_stories=事件数、tags 已按 P2-C 处理。
4. 最终对账：P2-A 的 brain_sync reconcile 0 意外差异；有差异停止切换。
5. 锁定单一权威：config `write_authority=true` 且 `shadow=false`；两处同时开启会被拒绝。

## 切换

6. 启动 Brain 写入的独立实例，跑保存/修改/删除/隐藏/锁定/归档/事件/重启；页面/召回正常。
7. 只有全部通过才让正式 Peter 使用；失败立即回退，不双写。

## 回退

8. 导出：`python scripts/p2b_cutover.py export --db data/peter/brain.db --out <vault>`。
9. 校验：`verify` 回导 0 problems；启动 **独立 Ombre**（独立 vault/端口）读取同一批新增/修改/
   删除，核对 ID/正文/版本/状态/sidecar/事件；hard delete 不得复活。
10. 在 Ombre 上实际聊天/保存/修改/检索；全部通过才把 backend 切回 ombre。

## 当前状态

- 手册未在真实 Ombre 上跑完；P2-B 尚未验收，不得用于正式切换。
