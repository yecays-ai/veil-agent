# 工单 022：Windows 启动时发现 Node.js

## 目标

双击 Start Web 后，文件工具能使用 Windows 当前系统或用户 PATH 中已安装的 Node.js/npm，不再因启动器继承旧环境而消失。

## 当前状态

网页提示“文件工具不可用”对应 `tools_agent.py` 的 `shutil.which('node'/'npx')` 检查。启动器在 `web_launcher.py` 以分离进程启动服务，但当前 Codex 启动环境只继承到 Codex 自带 node，没继承 Windows 注册表 PATH 中的 `E:\Program Files\`，其中有 node.exe 和 npx.cmd。复用调查：检查了 [mafik/automat 的 RefreshPath](https://github.com/mafik/automat/blob/ba5950d9f959fc34484c27da1068a50df737ef74/run_py/windows_deps.py)，该实现从系统和用户注册表读取 PATH（MIT）；其做法整体替换进程 PATH，不适合 VEIL 保留现有启动器环境的需求，因此只借鉴读取来源，用 Python 标准库为服务进程合并去重后的注册表 PATH，不增加依赖。

## 验收步骤

1. 更新后退出当前 Peter 服务，再双击 `start-web.bat`；确认文件工具不再显示 Node/npm 不可用提示。
2. 在网页让 Peter 读取一个工作区文件，并审批一次明确的文件写入；确认文件工具可用且审批仍按原规则执行。
3. 确认普通聊天和 `/memory`、`/cost` 仍可用。
