# 工单 334：主聊天把浏览器和桌面动作交给宿主通道（执行层统一方案阶段 2）

## 目标

宿主通道（`executor.host_mode`）开着时，Peter 的日常聊天里：
- 浏览网页只经过 `workbench` 一条路，由 VEIL 检查点统一裁决、统一日志、统一停止；操作 Windows 软件默认仍用聊天自带的动作，`host_desktop_handover: true` 时也只经 `workbench`（此时工作台才带桌面服务）；
- 默认只交出浏览器：聊天里原来的 `browser`（独立浏览器）不再提供。桌面动作（下面一行）要设 `host_desktop_handover: true` 才交出，因为宿主桌面在 Lan 电脑上还没跑通（见 HANDOFF），聊天自带的桌面动作是现在能用的路。
- 桌面动作交出后收起：`windows_launch`、`windows_manage`、`windows_control_action`、`windows_canvas_action`、`windows_discover_apps`、`desktop_icons_arrange`、`desktop_icons_restore` 不再提供给模型；
- 看屏幕（`windows_observe` 等）、`web_search`、`web_fetch`、文件、记忆、消息不变。

## 做法

- `tool_groups.HOST_PATH_HIDDEN_*`：被收起的工具名；`Chat.host_hidden_tools()`：只有主聊天（不含自主醒来、瞥一眼）、`host_mode` 开、工作台可用、没设 `host_keep_old_tools` 时才收；并且只收这台电脑真能启动的那一边（`host_executor.available`：有 Node 才收浏览器，Windows 上有 uv 才收桌面动作），起不来的那边旧工具继续在。
- 系统提示加一句稳定说明（`host_handover_note`），只写真正被收起的工具名，告诉 Peter 这些活交给工作台，免得他去调不存在的工具。
- `host_keep_old_tools`（`workbench.json` 或 `character.toml` 的 `[executor]`）：设成 true 立刻把旧工具带回来，不删代码，方便出问题时退回。
- `workbench_switch` 加两个选项：开启/关闭新通道。
- 日志：`context-log.jsonl` 的请求记录加 `tool_schema_top`（最重的 10 个工具定义字节数），用来看工具说明到底占在哪。

## 不在本单

旧代码一行不删（旧通道继续给自主醒来、`host_mode` 关时使用）；阶段 3 另见 HANDOFF。

## 验收（Lan 体验）

1. 开启新通道后重开 Peter，对他说“去查一下今天有什么科技新闻，打开两个网页读一读告诉我”：不弹浏览器窗口，你照常用电脑也不被打断。
2. 说“打开记事本，写上 hello，不要保存”：记事本里真的有 hello，他如实汇报。
3. 随时想退回：`workbench-switch.cmd` 选 6，重开 Peter。
