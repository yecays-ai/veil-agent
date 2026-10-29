# 工单 163 · 小红书与 GitHub 迁入 MCP 管理

## 目标
Lan 可在 MCP 管理窗口统一查看、启停现有小红书和 GitHub 服务，Peter 继续沿用现有能力与权限边界。

## 当前状态
分支 `ticket/163-mcp-integrations` 基于 PR #210 合并后的 main。原 `github_read` 与 `xiaohongshu` 由 `tools_agent.py` 直接注册，现由 MCP 管理器统一启停；无 ID 冲突时工具名为 `mcp__github__github_read` 和 `mcp__xiaohongshu__xiaohongshu`。若已有本地服务占用 `github` 或 `xiaohongshu`，内置服务使用 `veil_github` 或 `veil_xiaohongshu`（必要时加数字后缀），工具名相应改为 `mcp__veil_github__github_read` 或 `mcp__veil_xiaohongshu__xiaohongshu`。首次启动将旧角色开关迁入每角色的 MCP 配置；已有内置适配器按 adapter 去重，重启不会重复添加。旧配置保留，便于兼容，迁移写盘失败会显示配置错误而不阻止聊天启动。GitHub 仍限公开仓库只读，小红书仍只允许状态/推荐/搜索/详情，专属账号、调用间隔/每日额度及停止恢复逻辑沿用原适配器；两项集成继续响应用户停止操作。内置适配器调用不额外要求审批；调用步数、预算和全局 MCP 开关沿用 MCP 接入层，原生记忆工具组合模式也会正确注册适配器。PR #211 仍待最新 CI/Cubic 复核；Windows 正式程序体验待验收。

## 验收步骤
1. Windows 更新后打开 MCP 标签，分别启停 GitHub 和小红书，并重启 VEIL 确认状态保留。
2. 让 Peter 读取公开 GitHub 文件与小红书内容；确认 GitHub 写操作、小红书发布/评论等写操作均不可用，错误和读取限制正常显示。
3. 关闭单项服务及 MCP 总开关，确认对应工具不可调用，聊天和其他 VEIL 工具仍正常。
