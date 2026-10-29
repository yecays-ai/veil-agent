# 工单 170：update.bat 能装上乌有乡

## 目标
Lan 双击 update.bat 后，乌有乡安装步骤能找到安装脚本并正常执行，不再出现“install-nowhere.ps1 does not exist”。

## 现状
update.bat 为避免被 git pull 覆盖，会复制到 `.local-tmp` 里运行。安装乌有乡那一行用 `%~dp0` 找脚本，指向 `.local-tmp\scripts`，所以报找不到文件，乌有乡没装上；其余更新步骤不受影响。setup.bat 不复制自己，路径正确。

## 验收（Lan 操作）
1. 退出 Peter，双击 update.bat。
2. 屏幕上不再出现红字 “install-nowhere.ps1 ... does not exist”；乌有乡步骤按 setup.bat 同样的方式运行。
3. 更新结束后正常打开 Peter。
