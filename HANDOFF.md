## 当前工单 249（2026-11-07 UTC）

- 审批误判已由 PR #303 合入 main。本分支是桌面布局和双向虚拟转账、礼物，PR #302。
- 左右栏改为布局列。矮窗口左栏可滚动，很窄时只留图标，右栏收起时隐藏开关。输入行在上，工具托盘在下；麦克风在框内，请求回复在框外。
- 虚拟转账和礼物不是真实资金或物品。Lan 从加号托盘发送，Peter 用 `send_message` 的 `transfer` 或 `gift` 发送，用 `card_action` 处理发给自己的卡片。只有接收方能收下、拒绝；已接收的转账可以退回。结果另写一条消息。发送卡片不自动开新一轮回复。
- 实现：`virtual_cards.py`、`veil_agent.py`、`web_agent.py`、`tools_agent.py`、`desktop/chat.js`、`desktop/chat.html`、`desktop/desktop-visual-system.css`。没有复制 InternalBeyond。
- 本地检查：`tests/test_virtual_cards.py`、相关聊天测试、`node --check desktop/chat.js`、`desktop/tests/ui.test.cjs` 通过。Windows Electron 实机未验。不调用付费模型。

## 下一个窗口第一步

PR #302 合并后，让 Lan 更新 Peter，按 `docs/tickets/249-desktop-composer-layout.md` 在 Windows 检查左栏、输入区、表情包和虚拟卡片。不要再改审批分类，除非实机发现新的误判。

## 背景和边界

- PR #303 已合并。下面是当时的审批分类记录，不要按其中「尚未合并」的句子再操作。
- OpenCode v1.19.5 的 shell permission 请求 `patterns` 是逐条命令片段，`metadata.command` 是原始命令；复用现有请求机制，不新增权限系统。依据见 `docs/tickets/249-opencode-approval-classification.md`。
- 聊天与自主活动共用 `executor_opencode.run` 权限回调；费用预算、Docker 隔离、密钥保护、停止和快照实现未改。
- 未安装 OpenCode，真实 OpenCode API 集成测试和 Windows Peter 体验验收未完成；不调用付费模型，不操作真实项目。
