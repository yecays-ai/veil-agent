# 备份与恢复（Windows）

## 备份了什么

- 项目里的 `data\`（聊天、费用账本、Ombre 记忆、授权、长期任务、workspace……）、`characters\`、`persona\`、`.env`。
- 桌面版自己的设置：`%APPDATA%\VEIL Desktop\desktop.json`（窗口位置等）和你选的聊天背景图 `appearance-background.*`。
- 不备份：`%APPDATA%\VEIL Desktop\attachments`（附件缓存，原件在 `data\<角色>\media`，已被备份）、`session-data`（浏览器缓存）、`.venv`、`.deps`、桌面 exe（都可重新生成）。
- 不备份的运行残留：`data\web_service.json`、`data\` 与 `data\<角色>\` 下遗留的 `*.tmp`、锁文件、Ombre 进程标志和日志。

## 备份放哪里

在 `.env` 里加一行（路径必须在项目目录之外，建议另一块磁盘）：

```
VEIL_BACKUP_DIR=X:\VEIL-backups
```

手动备份：先退出 Peter，再运行 `.venv\Scripts\python.exe backup_restore.py create --destination "X:\VEIL-backups"`。

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

- 桌面版设置只在「桌面版没有运行，并且当前位置还没有这些文件」时写回原位置；否则放进恢复目录旁新建的 `restored-desktop-data-*` 文件夹，屏幕会写明；要使用就先完全关闭 VEIL，再手动复制进 `%APPDATA%\VEIL Desktop`。恢复不会覆盖正在使用的桌面配置。
- 恢复旧备份后，聊天、记忆、费用账本都回到备份当时的样子。

## 恢复被打断了怎么办

症状：启动 Peter 时提示「检测到未完成的备份恢复」。这是保护：数据目录可能是半恢复状态，程序不会使用它。

1. 先完全关闭 VEIL，不要删除任何 `*.before-restore-*` 文件夹。
2. 提示里写了「你原来的数据保存在：……」，那个文件夹就是你被替换之前的真实数据，请保留。
3. 按提示里的命令再运行一次 `restore --replace` 完成恢复。再次运行后可能又多出一个 `*.before-restore-*`：里面可能只是上次中断时的半恢复内容；**最早的那个才是你原来的数据**。程序不会删除其中任何一个。
4. 确认 Peter 能正常使用、数据都在之后，再自己决定是否清理安全副本。

## 看看哪些东西在堆积

`.venv\Scripts\python.exe backup_restore.py usage` 只统计数字，不删除任何东西：Ombre 升级前留的 `memory.backup-*`、遗留 `.tmp`、`*.before-restore-*` 安全副本、备份位置里的备份和中断留下的临时目录。
