# 当前状态

更新：2026-10-21 · 工单 037

## 当前目标与现状

- **037 主动消息后的记忆重启：** 最新 main `96a91d9` 可用隔离虚构数据在 Extract 与 Native 模式复现；分支 `ticket/037-memory-proactive-restart` 已完成轮次统计修复和持久化重启回归，PR 即将创建。CI、评审和 Windows 体验待完成。\n- **036 Peter 身份分层：** 本机人设 v3（身份 + How you act，同一文件，加载方式不变）交付在项目共享文件夹；程序事实改为网页/终端都可聊天。全量单元测试通过。Windows 体验尚未验收。
- **034 新对话：** PR #66 已合并，#71 去掉写死的英文提示（按钮等同 `/new`）。Windows 体验尚未验收。
- **035 英文模型上下文：** PR #70 已合并。Windows 体验尚未验收。

## 下一步

完成 037 PR 的 CI 和评审后合并，再由 Lan 按下方步骤验收；未验收前不报告体验完成。之后继续处理 036、034、035 尚未完成的 Windows 验收。

## Windows 验收\n\n- **037：** 按 `docs/tickets/037-memory-proactive-restart.md` 正常聊天后让 Peter 自然主动发消息，退出并重开服务和网页，确认历史、主动消息、记忆启动和召回正常。

- **036：** 按 `docs/tickets/036-persona-layers.md` 替换本机人设并检查闲聊、自主判断、纠正、边界、重开。
- **034：** 按 `docs/tickets/034-fresh-english-chat.md` 检查新会话、旧聊天与数据保留、重启及工具/审批。
- **035：** 按 `docs/tickets/035-english-model-context.md` 检查中英文对话、自主记录新旧格式、记忆操作和中文界面。
