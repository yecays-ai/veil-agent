# 工单 234：Claude Code 只读查询不再弹同意

## 目标

在 Peter 聊天里使用 Claude Code 时，查文件、搜内容、列目录这类只读查询直接执行，不再每一步弹同意卡片。写文件、执行命令、访问网页仍然先问 lan。

## 当前状态

`ClaudeAgentChannel.command` 只把 VEIL 的工具桥放进免询问列表，Claude Code 自带的 Read、Grep、Glob 每次都转成同意卡片。`.env`、secrets 的拒绝规则写成 `**/.env`，只匹配 Claude Code 工作目录之内的文件，匹配不到 `veil-agent\.env` 和 `E:\Peter-Agent\secrets`（已在真实 claude 上复现）。本工单把 Read、Grep、Glob 加入免询问列表，同时把拒绝规则改成 `//**/` 前缀（匹配电脑上任意位置），再保留原规则。

## 验收步骤

1. 重启 Peter，选 Claude Code 通道，让他查一个本地问题（例如“查一下 windows 观察为什么失败”）：搜索和读文件不弹同意卡片，写文件或执行命令仍弹卡片。
2. 让他读 `E:\Peter-Agent\VEIL\veil-agent\.env`：被拒绝，不显示内容，也不弹卡片。
3. 让他在文件夹里搜 `VEIL_CLAUDE_PATH` 或列目录：结果里不出现 `.env` 的内容。
