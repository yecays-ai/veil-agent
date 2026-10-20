# 工单 014：聊天稳定性与异常恢复

## 目标

让 Windows 网页聊天在发送失败、普通模型 API 故障、审批异常和重复发送时保住输入并安全恢复，费用与工具权限仍由现有程序控制。

## 当前状态

- 基于最新 `main`：`b1d8555`。`web/index.html` 先清空草稿，审批点击后立即隐藏按钮；`web_agent.py` 的 busy 检查与队列写入不是原子操作；`run_chat` 收到模型异常会退出循环。
- 复用调查：延续 013 对 Open WebUI、LobeChat 整套聊天界面的调查。本轮核对 Open WebUI（调查时 156,216 stars，最近推送 2026-10-19；仓库自带 BSD 风格许可）源码：[`Chat.svelte`](https://github.com/open-webui/open-webui/blob/91ea4e3421d1d8069f13d4a463fcb5986d94ea60/src/lib/components/chat/Chat.svelte) 和 [`utils/chat.py`](https://github.com/open-webui/open-webui/blob/91ea4e3421d1d8069f13d4a463fcb5986d94ea60/backend/open_webui/utils/chat.py)。它依赖完整 Svelte/FastAPI/WebSocket 聊天架构，不是可直接接入的状态修复组件；复制会重建前端并改变边界。因此不移植代码，沿用本仓库 Python 标准库服务、`run_chat`、预算预留和审批队列，不加依赖。
- 当前分支 `ticket/014-chat-stability`；保持原终端聊天、长期记忆、工具审批和预算行为。

## 验收步骤

1. Windows 网页输入长消息后断网或模拟请求失败，确认原文仍在输入框、提示未成功；结果不确定时页面不自动重发。
2. 模拟一次模型超时，确认 Peter 显示失败提示，聊天仍可继续，账本保留待核对费用且聊天记录一致；检查损坏账本仍安全停止。
3. 连点发送并处理一次同意和拒绝审批，确认没有重复模型/工具执行，审批状态只在服务器确认后变化；随后正常使用 `/memory`、`/cost` 和 `/quit`。
