# 工单 230：Claude Code 后台启动不弹终端窗

## 目标

在 Peter 聊天里使用 Claude Code 时不再弹出黑色终端窗口，同时保留聊天、工具、权限审批和停止功能。

## 当前状态

`ClaudeAgentChannel.run_turn` 直接用 `subprocess.Popen` 启动 `claude`，Windows 分支没有传隐藏控制台的启动标志，导致系统为命令行程序打开终端窗。仓库已有 `web_launcher.start_service` 等后台进程使用 `DETACHED_PROCESS | CREATE_NO_WINDOW`，本工单沿用该机制；现有标准输入/输出管道、`kill_tree` 停止流程保持原样。

## 验收步骤

1. 在 Peter 聊天窗口连续发送三条消息；确认没有弹出 `claude` 黑色终端窗，Peter 正常回复并能调用工具。
2. 完成一次权限审批，再点“停止”；确认审批和停止都正常。
3. 退出 Peter 后检查任务管理器，确认没有遗留的 Claude Code 进程。
