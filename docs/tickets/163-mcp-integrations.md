# 工单 163 · 小红书与 GitHub 迁入 MCP 管理

## 目标
Lan 可在 MCP 管理窗口统一查看、启停现有小红书和 GitHub 服务，Peter 继续沿用现有能力与权限边界。

## 当前状态
分支 `ticket/163-mcp-integrations` 基于 PR #210 合并后的 main。原 `github_read` 与 `xiaohongshu` 由 `tools_agent.py` 直接注册，现由 MCP 管理器统一命名为 `mcp__github__github_read` 和 `mcp__xiaohongshu__xiaohongshu`，状态显示在 MCP 窗口并可单独启停。首次启动将旧角色开关迁入每角色的 MCP 配置；旧配置保留，便于兼容。GitHub 仍限公开仓库只读，小红书仍只允许状态/推荐/搜索/详情，专属账号、调用间隔/每日额度及停止恢复逻辑沿用原适配器。内置适配器调用不额外要求审批；调用步数、预算和全局 MCP 开关沿用 MCP 接入层。待完成静态核查、PR 与 CI；Windows 正式程序体验待验收。

## 验收步骤
1. Windows 更新后打开 MCP 标签，分别启停 GitHub 和小红书，并重启 VEIL 确认状态保留。
2. 让 Peter 读取公开 GitHub 文件与小红书内容；确认 GitHub 写操作、小红书发布/评论等写操作均不可用，错误和读取限制正常显示。
3. 关闭单项服务及 MCP 总开关，确认对应工具不可调用，聊天和其他 VEIL 工具仍正常。
