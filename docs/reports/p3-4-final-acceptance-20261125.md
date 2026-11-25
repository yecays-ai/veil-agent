# P3-4 最终验收报告（2026-11-25）

## 结论

P3-4 维护窗口已完成：正式清理、Ombre 物理卸载、P3-3/P3-4 启动、数据完整性对账、导出与恢复复验、功能验收全部通过。P3 到此结束，未启动 P4。

## 1. 前置确认

- main：`160b0ec`（#445 P3-4 + #447 维护兼容修复均合并）。
- 正式目录已更新到 main，桌面重新打包；正式 Peter 清理前完全关闭。
- 正式清理脚本：实际执行使用本地确定性修复版 `p3_4_cleanup-fixed.py`，sha256 `d4e19e31c852dfaa8c052f08c2aa992a826af5c8f02bfbf67446013efd95f32e`。
- 已合并 main 的脚本 sha256 `97d5de6efc4a5035813f498a175e10ba1317029d0a1b92a94d9fc7a4a072f20a`；两者差异仅为稳定排序补丁（`p3_4_cleanup-deterministic.patch`），随下一次开发 PR 同步。
- 冷备：`D:\Peter backup\VEIL-Agent-Backup-20261125T130930.936293Z-8d5609ad`，verify 5428 文件。
- 冷备恢复演练：`E:\Peter-Agent\VEIL\P3-4-maintenance\restore-drill-20261125T2110`，恢复 5427 文件；brain quick_check/digest ok，289 memories / 75 events / 76 stages / 591 ledger。
- 原批准 plan 与确定性修复后的三次 plan：
  - 原 hash `7652242a94e5442f5db6c6874108e9b4bee2edf75980ef028aa0336f13b1603f`；
  - 新 hash `fe54030225fc2b36cc8647702ed4c408abfa31419f89438fe07311e87583cd16`，三次独立进程一致；
  - 目标集合、逐文件内容指纹、DB sha256、冷备覆盖、tombstones/letters/plans 集合、P6 快照全部与原批准 plan 一致；差异仅为 file_targets 排序。
- 6 条旧 plan 已核对：均有完整 DB 行、vault 文件、冷备文件；迁移去向为正式 archive 的完整行归档，不是丢失。

## 2. 正式清理执行

- apply 结果：tombstones 39 条、letters/plans 10 条、文件目标 24 个。
- 归档：`P3-4-maintenance/formal-archive/letters-plans-fe54030225fc2b36.json`，完整 10 行（4 letters + 6 plans），只归档迁移重复行，墓碑正文未写入。
- 6 条 plan：`1e30f4d6004b`、`65d03df8e211`、`68e468d38d91`、`830b63fefd1a`、`ef34e23f2dc5`、`fe334e7f273d`。
- Ombre 安装包：`P3-4-maintenance/formal-archive/ombre-115d831.zip`；运行目录 `.deps/ombre`、`.local-tmp/ombre-*` 已删除；无 18011 端口/Ombre 进程。
- 仅保留范围外残留 `.local-tmp/ombre-requirements.txt`（不在批准文件名单，未扩大清理）。

## 3. 数据完整性

清理后正式 DB：

- memories 240、versions 234、vectors 240、events 75、event_stages 76、event_ledger 593（含启动后正常 dream 新增 1 条）、event_state 1、relations 120、sources 24、quotes 6、tombstones 50、delete_requests 0、write_journal 9。
- 与冷备逐项对比：正常 memories 240 条 P6 字段 mismatch 0；versions/vectors/relations/sources/quotes 哈希完全一致；events 75、stages 76 完全一致；旧 event_ledger 行 0 缺失；启动新增 1 条 event-dream-report、event_state 更新，属 P3-3 正常启动行为。
- quick_check ok，digest ok；tombstone 正文行 0、letters/plans DB 行 0。
- 活跃业务记录 chat/tools/context-log/incoming/provider/message/long-task-history 与 `memory/_agent`、`_media`、P3-2 archive 保留。

## 4. 启动与功能验收

- 正式 Peter 已重启：health ready、桌面进程运行。
- API：`/api/health` 200、`/api/memory/panel` 200（224 项）、`/api/memory/events` 200（74 项）、`/api/memory/catalog` 200、`/api/peter-page` 200、`/api/events` 200。
- 无 18011，无 Ombre 运行目录/进程；旧隐藏行迁移完成。
- 未向正式 Peter 发送聊天或写正式记忆。

## 5. 导出与恢复复验

- Vault 导出：`P3-4-maintenance/post-cleanup-export`，3.1 MB；manifest：240 memories / 234 versions / 75 events / 76 stages / 50 tombstones；memory 文件 241 个。
- Brain 备份：`P3-4-maintenance/post-cleanup-brain-backup.db`，sha256 `7443e8da86193bc7a24b8f209a20deffbbc2f8aa5b8034ebefb47b36faf3c3b1`，quick_check ok、digest ok。
- 恢复副本：`P3-4-maintenance/post-cleanup-restore.db`，restore quick_check/digest ok；240 memories / 234 versions / 240 vectors / 75 events / 76 stages / 593 ledger / 50 tombstones；无 letter/plan、无 tombstone 正文。

## 6. 未做 / 边界

- 未扩大清理范围；未删除批准名单外文件。
- 未启动 P4；未改权限/预算/模型配置。
- 本地确定性修复尚未进入 main，只保留在维护目录和 patch 中，随下一次开发 PR 同步。
- 正式 STATUS/HANDOFF 已更新，随下一次开发 PR 一起提交；不单独开 PR 等 CI。
