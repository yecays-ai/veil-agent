# 工单 080：Main Update 复用 Electron 缓存

## 目标

双击 Main Update 后，桌面程序使用本机已下载的 Electron 运行包完成更新，不再重复等待长时间下载。

## 当前状态

`desktop/package.json` 直接运行 Electron Packager，Windows 更新时仍会尝试重新下载 Electron。桌面目录已有 Electron 45 的完整缓存包；打包入口需要把这个缓存包明确交给 Packager，并在首次没有缓存时保存到固定缓存目录。

## 验收步骤

1. 双击 `update-main.cmd`，确认更新与桌面打包直接复用已有 Electron 缓存并正常结束。
2. 双击 `start-desktop.bat`，确认从原有桌面程序位置启动，并能打开 Peter 聊天。
