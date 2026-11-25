# P3-4 维护窗口清理手册

本工具只在维护窗口手动运行，程序启动不会自动执行。不可逆步骤必须由 Lan 在维护窗口确认名单后执行。

## 前置

1. 完全关闭 VEIL/Peter，确认没有网页服务、Brain 写锁、Ombre 端口 18011。
2. 在项目目录之外做完整冷备并验证：
   ```powershell
   .venv\Scripts\python.exe backup_restore.py create --destination "D:\Peter backup"
   .venv\Scripts\python.exe backup_restore.py verify --backup "<刚创建的备份目录>"
   ```
3. 用该备份在另一个临时目录做一次恢复演练，确认可启动/可读取；演练目录不要覆盖正式目录。
4. 把 Ombre 安装包（`.local-tmp/ombre-115d831.zip`）复制到仓库外离线保存；确认再执行物理卸载。

## 生成名单（只读）

```powershell
.venv\Scripts\python.exe scripts\p3_4_cleanup.py --root . --role peter ^
  --backup "<冷备目录>" --archive-dir "D:\P3-4-archive" --plan "D:\P3-4-plan.json"
```

检查 plan 中的 `tombstones`、`letters_plans`、`file_targets`、`p6_before`、`db_sha256`。
工具不修改数据；计划绑定备份清单和数据库哈希。

## 执行

```powershell
.venv\Scripts\python.exe scripts\p3_4_cleanup.py --root . --role peter ^
  --backup "<冷备目录>" --archive-dir "D:\P3-4-archive" --plan "D:\P3-4-plan.json" ^
  --apply --confirm P3-4-cleanup
```

只会：

- 把已删除/墓碑记忆的正文、raw、版本、向量、关系等移除，保留最小 tombstones 标记；
- 把 letters/plans 的 DB 重复行归档到 `D:\P3-4-archive` 后删除并留最小标记；
- 删除旧 vault、memory.backup、event-import backup、旧 role backups、旧 Ombre 运行目录；
- 把 Ombre 安装包复制到 archive 后删除运行目录。

保留：`data/<role>/memory/_agent`、`memory/_media`、`data/<role>/archive/pre-p3-2-*`、
正常记忆及 P6 raw/frontmatter/tag_migration/legacy 值/relations/sources/quotes。

## 清理后

1. 用清理后的 `brain.db` 做一次导出和备份恢复复验；确认 quick_check、digest、页面/事件正常。
2. 启动 Peter，确认聊天/记忆/事件/召回/页面正常，已删除记忆不复活，letters/plans 不在记忆库。
3. 确认无 Ombre 进程、18011 端口和 `.deps/ombre`；安装包只在仓库外 archive。
4. 把 plan、清理结果、备份路径和恢复演练证据写进 STATUS/HANDOFF。

## 回退

清理前完整冷备是唯一回退来源；清理后新增记忆不能靠旧备份直接覆盖。恢复前按
`docs/backup-and-restore.md` 的 Brain 恢复规则保留 safety copy/export。


## 安全边界（复审补充）

- `--plan` 必须是不存在的路径；工具独占创建，绝不覆盖已有文件。`--archive-dir` 不能与冷备目录相同或嵌套。
- 生成计划会逐个文件核对冷备 `payload` 中的路径、大小和 sha256；`brain.db` 也必须存在且哈希一致。`.deps/`、`.local-tmp/` 是可重装运行依赖，不要求进入冷备，但 Ombre 安装包必须先进入 archive。
- JSONL 清理使用明确废弃白名单，绝不使用 `*.jsonl` 通配；聊天、费用、工具、上下文、incoming/provider/message 和 `memory/_agent` 等活跃记录全部保留。
- 计划包含每个待删文件的 sha256 和每个目录的完整成员/tree hash；同大小替换也会被拒绝。中断恢复时，剩余待删文件必须仍属于批准指纹（缺失可以继续，新增/修改会拒绝）。
- 归档只复制 letters/plans 迁移重复行，不把已删除墓碑正文重新写进归档；清理前后对账使用同一组已批准删除事件排除范围。
- 通过父目录联接点解析到仓库外的目标不会进入删除名单。
- 数据库删除在一个事务内先核对当前墓碑/letters/plans 集合与批准计划完全一致；不一致时不删除任何行。删除会清掉该记忆的 event_ledger/已关闭删除申请/操作记录残留，但保留其他记忆和共享事件。
- 删除事务提交后写 archive 状态文件；若 VACUUM、完整性对账或文件清理中断，用同一 plan/archive 重试会从已提交阶段继续，不会重新删除记录。
- 清理前后对保留的 memories/events/stages/versions/vectors/relations/sources/quotes/event_ledger/event_state 做逐行内容指纹对账；不一致则停止。

