# 工单 079：修复 Main Update 恢复扫描过慢

## 目标

本地 `main` 与 GitHub 分叉时，Main Update 快速完成安全检查和恢复，不因大量本机忽略文件卡在备份提示后。

## 当前状态

现有 `update-main.cmd` 为检查是否会覆盖文件，逐个启动 Git 扫描所有 ignored/untracked 路径。运行目录有约 58,395 个候选文件，造成漫长等待。复用 Windows 自带 PowerShell 和 Git 的路径比较，只检查 GitHub 相对本地 `main` 新增的文件；保留新增路径冲突时停止且不覆盖本地文件的保护，不引入外部依赖。

## 验收步骤

1. 在本地 `main` 有领先提交时运行 Main Update；确认保留备份，快进失败不再冒出 fatal 提示，扫描后更新、打包并停留在 `main`。
2. 有 3,000 个无关 ignored 文件时仍能快速完成，文件保持原样；若本地文件与 GitHub 新增路径冲突，更新停止且文件未覆盖。
