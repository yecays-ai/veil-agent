# 当前状态

更新：2026-10-21 · 工单 036、Peter 思考模式

## 当前目标与现状

- **Peter 思考模式：** `characters/peter/character.toml` 默认改为 `thinking = "enabled"`。19–20 日 Lan 本机开着思考模式，Peter 自己存记忆、连发气泡；20 日晚本机设置被更新覆盖为 disabled 后，输出 token 由每次约 400–900 降到 70–280，自主 hold 基本停止。Lan 本机重新打开后已看到他未经提示自己 breath_search 和 hold。后台记忆调用仍显式关闭思考。
- **036 Peter 身份分层：** 本机人设 v3（身份 + How you act，同一文件，加载方式不变）交付在项目共享文件夹；程序事实改为网页/终端都可聊天。全量单元测试通过。Windows 体验尚未验收。
- **034 新对话：** PR #66 已合并，#71 去掉写死的英文提示（按钮等同 `/new`）。Windows 体验尚未验收。
- **035 英文模型上下文：** PR #70 已合并。Windows 体验尚未验收。

## 下一步

Lan 按各工单验收步骤在 Windows 上确认；未验收前不报告体验完成。

## Windows 验收

- **036：** 按 `docs/tickets/036-persona-layers.md` 替换本机人设并检查闲聊、自主判断、纠正、边界、重开。
- **034：** 按 `docs/tickets/034-fresh-english-chat.md` 检查新会话、旧聊天与数据保留、重启及工具/审批。
- **035：** 按 `docs/tickets/035-english-model-context.md` 检查中英文对话、自主记录新旧格式、记忆操作和中文界面。
