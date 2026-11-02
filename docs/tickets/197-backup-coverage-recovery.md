# 工单 197：备份覆盖与恢复体验

来源：lan 2026-11-02 批准的数据完整性审计后续（A 工单）。

## 目标

1. 桌面版自己的设置（`%APPDATA%\VEIL Desktop\desktop.json`）和聊天背景图（`appearance-background.*`）进入备份并能恢复。附件缓存（`attachments`，原件在 `data\<角色>\media`）和浏览器缓存（`session-data`）是可再生副本，不备份，避免每次更新复制大量缓存。
2. 恢复时，桌面端数据不能覆盖正在使用的桌面配置：只有桌面版没在运行、且当前位置没有这些文件时才写回原位置；否则放进恢复目录旁新建的 `restored-desktop-data-*` 文件夹并在屏幕写明如何使用。
3. 恢复被打断后：Peter 启动时的提示写明「你原来的数据保存在哪个文件夹（不要删）」和要运行的完整命令；重新恢复时屏幕再次说明最早的安全副本才是原数据，程序不删除任何安全副本；重新恢复再失败时仍保持「未完成」状态，不会让半恢复的目录看起来已经完成。说明文档：`docs/backup-and-restore.md`。
4. 更新前备份：`update.bat` 先检查有没有新版本，有才备份到 `.env` 的 `VEIL_BACKUP_DIR`；备份失败（没设置位置、空间不足、位置不可用等）默认停止更新，写明原因，程序和数据保持不变，不静默跳过。明确不想备份时用 `update.bat --skip-backup`，并在屏幕写明没有备份。
5. 备份空间不足时，在复制之前就报告需要多少、现有多少。
6. 备份不再带 `data\web_service.json` 和 `data\`、`data\<角色>\` 下遗留的 `*.tmp`；Ctrl+C 打断备份后不留临时目录。
7. `backup_restore.py usage` 只统计 `memory.backup-*`、遗留 `.tmp`、`*.before-restore-*`、备份位置里的备份与中断留下的临时目录，不删除任何东西。

## 现状

- 代码、自动测试（`tests/test_backup_restore.py`、`tests/test_update_bat.py`）完成。
- 备份格式版本仍是 1；没有桌面数据的旧备份照常校验和恢复；含桌面数据的备份在清单里多一个 `desktop-data` 组件（更旧的程序不能读它）。
- Windows 真机验收：未验证。

## 验收步骤（Windows，只用隔离的假数据目录，不碰真实聊天和记忆）

1. 在 `.env` 里设置 `VEIL_BACKUP_DIR=另一块磁盘上的文件夹`。关闭 VEIL，手动备份一次；备份目录的 `manifest.json` 里有 `payload/desktop-data/desktop.json` 和（如果设置过）背景图，没有 `attachments`、`session-data`。
2. 用 `restore` 恢复到一个全新目录：桌面版没运行且 `%APPDATA%\VEIL Desktop` 没有这些文件时，屏幕写明已恢复到原位置；若已有设置文件，屏幕写明放在了 `restored-desktop-data-*`，原有设置文件内容没变。桌面版运行时恢复同样不改动当前配置。
3. 恢复进行中强行结束进程，再启动 Peter：提示里有「你原来的数据保存在：…」和完整命令；按命令重新恢复成功后，屏幕写明最早的安全副本才是原数据，所有安全副本都还在。
4. 有新版本时运行 `update.bat`：先出现备份结果再更新。把 `VEIL_BACKUP_DIR` 指到不存在的盘符再试：更新在改代码之前停止，写明原因，代码和 `data\` 不变。加 `--skip-backup` 时屏幕写明没有备份。已经是最新代码时不生成新备份。
5. 备份位置盘空间不足时，备份在复制前失败并写明需要和现有空间，原有数据不变。
6. 运行 `usage`：只显示数字，没有任何文件被删除。
