# 082 桌面 UI 第一轮整合改版

## 目标

让 lan 能在半透明 VEIL 聊天窗中聊天、调整 Peter 与窗口尺寸、查看真实自主活动，并从统一入口访问 Ombre 日常记忆和高级设置。

## 当前状态

改版已在独立 worktree `E:\Peter-Agent\VEIL\veil-agent-codex` 的 `ticket/082-desktop-ui-first-pass` 完成开发，用户已授权推送并等待审核。沿用现有 Electron 窗口、IPC、本地 Agent 状态和 `desktop.json` 持久化。外观设置包括窗口与分页面透明度、气泡透明度、背景图和自定义 CSS；日夜主题共用透明窗口设置。Peter 保持宽高比并可连续拖角缩放，聊天窗口支持自由缩放与位置记忆。活动页展示 Agent 的实际状态、下次唤醒原因、最近成果和历史。

日常记忆页通过 Ombre Memory 的 `memory_catalog` 与 `memory_read` 只读接口显示真实可见记录；Ombre Dashboard 为次级高级入口。本轮不迁移、改写或删除真实记忆。

顶部栏最终显示 Peter 与居中的「自主活动」状态、「下次唤醒」时间，不显示服务运行状态。CodeRabbit 本次不请求；等待常规 PR 审核。开发检查：`desktop/tests/ui.test.cjs` 3 项、desktop 套件 5 项、`tests/test_autonomy.py` 26 项、`tests/test_native_memory.py` 52 项、Ombre Memory 只读 HTTP 接口 1 项通过。Windows 实际体验验收未完成，自动检查和本地预览图均不替代该验收。

## 验收步骤

1. 在 Windows 桌面拖动 Peter、连续调整尺寸并拖动/缩放聊天窗；确认人物不变形、输入区可用，重启后尺寸和位置恢复。
2. 调整窗口与气泡透明度并切换日间/夜间主题；检查聊天、活动、记忆、更多页面、背景图、CSS 设置及图片、语音、审批、消息记录入口。
3. 打开活动页检查真实状态、完整的下次唤醒时间、最近成果与历史；打开 Ombre Memory 日常入口和高级 Dashboard；重启后检查外观设置恢复。
