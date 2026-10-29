# 工单 161 · 本地 MCP 基础模块与桌面管理窗口

## 目标

Lan 可在 VEIL 桌面添加、启停和查看本地 stdio MCP 服务，Peter 在原有工具循环与预算约束内调用其工具。

## 当前状态

- 分支 `ticket/161-mcp-foundation` 已实现独立 `mcp_services.py`、桌面 MCP 标签、配置持久化、逐服务权限（默认每次询问）、连接状态和最近错误；GitHub、小红书接入没有改动。
- 复用仓库已锁定的[官方 Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/client/transports.md) `mcp==1.30.0`：`StdioServerParameters`、`stdio_client`、`ClientSession` 负责协议和子进程生命周期；[SDK 为 MIT 许可](https://github.com/modelcontextprotocol/python-sdk/blob/main/pyproject.toml)，可在 Windows 使用。没有另写 MCP 协议。
- 本地 Windows 进程测试覆盖标准无账号服务的添加、发现、调用、停用、重启配置、启动失败和调用超时。Electron 虚构数据窗口测试通过；Lan 对正式更新版的实际体验尚未验收。

## 验收步骤

1. 更新 Windows 桌面程序，打开并拖出「MCP」标签，添加 `python` 和 `tests/fixtures/mcp_echo_server.py` 作为本地测试服务；打开总开关后看到 `echo`、`slow` 工具，再让 Peter 调用 `echo`。
2. 分别关闭该服务和 MCP 总开关，让 Peter 再次尝试调用；确认调用被拒绝，其他工具和聊天照常。重启 VEIL，确认配置与开关恢复。
3. 把启动命令临时改成不存在的程序，再改回；确认启动错误可见。调用 `slow` 等待约 20 秒确认超时错误可见、聊天不崩溃。确认 GitHub、小红书现有功能照常。
