# 备份与恢复（Windows）

## 备份了什么

- 项目里的 `data\`（聊天、费用账本、Brain 记忆与事件、授权、长期任务、workspace……）、`characters\`、`persona\`、`.env`。
- 桌面版自己的设置：`%APPDATA%\VEIL Desktop\desktop.json`（窗口位置等）和你选的聊天背景图 `appearance-background.*`。
- 不备份：`%APPDATA%\VEIL Desktop\attachments`（附件缓存，原件在 `data\<角色>\media`，已被备份）、`session-data`（浏览器缓存）、`.venv`、`.deps`、桌面 exe（都可重新生成）。
- 不备份的运行残留：`data\web_service.json`、`data\` 与 `data\<角色>\` 下遗留的 `*.tmp`、锁文件和运行日志。

## 备份放哪里

在 `.env` 里加一行（路径必须在项目目录之外，建议另一块磁盘）：

```
VEIL_BACKUP_DIR=X:\VEIL-backups
```

手动备份：先退出 Peter，再运行 `.venv\Scripts\python.exe -m veil.system.backup_restore create --destination "X:\VEIL-backups"`（根目录 `backup_restore.py` 过渡兼容入口同样可用）。

> 如需在**不拉取最新 `main`** 的情况下将程序代码回退到某个历史提交并重新打包桌面版，请使用独立离线回滚脚本 `scripts\windows\rollback-version.cmd <目标提交>`（详见 `docs/installation.md`）。

## 更新前自动备份

双击 `update.bat` 时：

1. 先检查有没有新版本；已经是最新就不备份。
2. 有新版本时，先退出 Peter，更新程序会备份到 `VEIL_BACKUP_DIR`。
3. 备份失败（没设置位置、磁盘空间不足、位置不可用……）就**停止更新**，屏幕写明原因；你的程序和数据都没有被改动。
4. 这一次确实不想备份，运行 `update.bat --skip-backup`；屏幕会写明「没有备份」。

## 恢复

```powershell
$backup = "X:\VEIL-backups\VEIL-Agent-Backup-实际时间戳"
.venv\Scripts\python.exe backup_restore.py verify --backup $backup
.venv\Scripts\python.exe backup_restore.py restore --backup $backup --target "D:\VEIL-recovery" --replace
```

- 桌面版设置只在「桌面版没有运行，并且当前位置还没有这些文件」时写回原位置；否则放进恢复目录所在文件夹里新建的 `restored-desktop-data-*` 文件夹，屏幕会写明；要使用就先完全关闭 VEIL，再手动复制进 `%APPDATA%\VEIL Desktop`。恢复不会覆盖正在使用的桌面配置。
- 恢复旧备份后，旧记忆/事件回到备份当时的版本；备份之后新增的记忆和事件会合并回 live 库（同时保留 safety copy 与可读导出），不会被旧备份直接清空。费用账本会自动与恢复前的账本对账，补回备份之后的花费（见 docs/data-repair-and-ledger.md）。供应商已收的费用不会撤销，恢复后请用 `/cost` 核对今日与本月花费。

## 恢复被打断了怎么办

症状：启动 Peter 时提示「检测到未完成的备份恢复」。这是保护：数据目录可能是半恢复状态，程序不会使用它。

1. 先完全关闭 VEIL，不要删除任何 `*.before-restore-*` 文件夹。
2. 提示里写了「你原来的数据保存在：……」，那个文件夹就是你被替换之前的真实数据，请保留。
3. 按提示里的命令再运行一次 `restore --replace` 完成恢复。再次运行后可能又多出一个 `*.before-restore-*`：里面可能只是上次中断时的半恢复内容；**最早的那个才是你原来的数据**。程序不会删除其中任何一个。
4. 确认 Peter 能正常使用、数据都在之后，再自己决定是否清理安全副本。

## 看看哪些东西在堆积

`.venv\Scripts\python.exe backup_restore.py usage` 只统计数字，不删除任何东西：旧版留下的 `memory.backup-*`、遗留 `.tmp`、`*.before-restore-*` 安全副本、备份位置里的备份和中断留下的临时目录。


### Brain 数据库专用恢复（重要）

官方恢复流程使用 `brain_backup.py` 的 SQLite 在线备份/恢复接口时：

1. **停服与锁**：先完全退出 Peter，确认没有 VEIL 进程；恢复会检查 `brain-write.lock`，仍在运行会拒绝。
2. **WAL**：检测到 `brain.db-wal` / `brain.db-shm` 时拒绝直接覆盖；先正常退出程序让 SQLite checkpoint，不能手工删除 WAL 后覆盖。
3. **完整校验**：读取备份时重新计算 `quick_check`、canonical digest 和表计数，并核对备份内保存的 digest；只有全部通过才允许替换。
4. **原子替换**：先写临时文件、fsync 并再次校验，再用原子 rename 替换目标；替换前自动保留 `brain.db.pre-restore-<时间>` 和一份可读导出 `...-export/`。
5. **保留备份后新增数据**：恢复旧备份时，会把当前库里备份之后新增的 memory、事件、事件附账本和 state 合并回恢复后的 live 库；旧备份中的同一 ID 仍保持旧版本。新增数据也同时保留在 safety copy 和 export 里，冲突时可人工核对，**不会直接用旧备份清空后来产生的新记忆和事件**。

如果恢复过程在替换阶段中断，目标会保持替换前状态或安全副本；不要删除临时/安全文件，先运行 `brain_backup.snapshot`/`verify` 检查，再重试。
