# 工单 164：Peter 专属工具别名与移除 Windows 媒体工具

## 目标

Peter 可用已选定的角色别名调用现有工具，原权限和数据兼容保持有效；其他角色不继承这些别名，`windows_now_playing` 不再可用。

## 当前状态

- 别名放在 `characters/peter/character.toml` 的 `[tool_aliases]`，通用映射层位于 `tool_aliases.py`。工具 schema（含描述中的交叉引用）按角色改名，统一执行入口在 `Chat._execute_tool_call` 还原真实名称后再走原分发、权限、审批、MCP、预算和长任务检查点。未知、未提供及被别名替代的原名会拒绝；日志保留模型调用别名，安全监控按底层名称判断。历史记录和任务文件不迁移、不改写。
- 普通聊天、自主醒来、长任务、消息与桌面工具、MCP 工具均使用同一 schema/执行入口；自主瞄一眼中的程序内部观察继续直接用原工具，不作为模型工具名暴露。原生 `thought` 和全部 Ombre 工具保持原名，`desktop_create_file` 保持原名。
- GitHub 复用调查：OpenAI Agents SDK 提供工具名覆盖选项，但本项目已有受限工具注册、schema 构造和统一权限分发；引入另一个 Agent 框架不能解决角色配置隔离，因此复用 VEIL 现有入口，在别名层内完成映射，不增加依赖或模型请求。[OpenAI Agents SDK 工具名覆盖](https://openai.github.io/openai-agents-python/ref/tool/)；[OpenAI 函数调用协议](https://developers.openai.com/api/docs/guides/function-calling)。
- `windows_now_playing` 的实现、Peter 开关配置、调用分支、专属 WinRT 依赖及测试已移除；`windows_foreground`、`windows_list`、`windows_observe` 保留。没有新增桌面观察权限。
- 已通过：别名/桌面观察定向测试 29 项（2 项环境跳过）；工具权限、工具循环、消息与对话定向测试 52 项（2 项跳过）；聊天、长任务、运行时监控 106 项；新增别名专项测试 9 项。测试中的本地 filesystem 服务单例在当前环境超过 55 秒无输出，未记作通过。PR 自动检查与评审待完成，Windows 实际体验待验收。
- 上游 Agent SDK 与现有 MCP 注册方式均未复制代码；别名规则只调整模型看到的名称，执行仍使用现有工具身份。

## 验收步骤

1. 更新 VEIL 后请 Peter 查看电脑并找桌面文件；确认分别使用 `peek_screen`、`rummage_desktop` 和 `sniff_out_file`，且能读到真实前台与桌面文件。
2. 让 Peter 继续一份 Lab 作品并发一条消息；确认 `case_the_lab`、`read_the_notes`、`scribble`、`test_drive`、`bubble_up` 生效，原文件版本与聊天消息顺序保留。
3. 让 Peter 自主醒来继续长期任务并重启 VEIL；确认 `check_the_quest` 等别名仍可用，未授权操作仍拒绝或询问审批，工具列表没有 `windows_now_playing`，Windows 窗口观察仍可看到屏幕上显示的网易云信息。
