# 工单 331：宿主通道真机实测发现的问题

在 Lan 电脑的测试副本里按工单 329 的场景实测，两个场景都在 2.8 秒内停在 `gateway_failed`，没有调用模型，花费 ¥0。

## 原因和做法

1. 后台浏览器起不来：`npx` 在 Windows 上是 `npx.CMD`，VEIL 经 `cmd /c` 启动，而 `--proxy-bypass <-loopback>` 里的 `<` `>` 被 cmd 当成“从文件读输入”。去掉这个参数（Playwright 自己会加同样的规则，云端 Chromium 进程参数里已确认），并让 `mcp_services._stdio_server_command` 给含 `< > | & ^` 的参数加引号。
2. 日志只有 `unhandled errors in a TaskGroup (1 sub-exception)`：`executor_opencode.error_text` 展开 ExceptionGroup，写出第一个具体异常。
3. `uvx` 被 pip 装在 Python 的 Scripts 目录，不在 PATH：`host_executor._python_script` 也在当前 Python 的 Scripts 里找，避免桌面悄悄关掉。

## 一个工具起不来，只少这一个

范围：只在宿主通道（`host_mode`，桌面、浏览器、文件三个网关都走 `host_gateways`）；旧通道行为不变。

以前浏览器或桌面任何一个启动失败，整个任务就判失败。现在只丢掉起不来的那个，记 `tool_unavailable` 日志，并告诉模型“这次没有 X”，其余照常跑（Lan 明确要求）。

## 验收

单元测试：含 `<` `>` `|` 的参数被引号包住；浏览器参数里没有 `< > | & ^`。真机：合并后在测试副本更新，重跑“网页搜索写 notes.md”和“打开记事本输入 hello 不保存”。
