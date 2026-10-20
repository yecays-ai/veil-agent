# 工单 017：4D 最小聊天多气泡

## 目标

Peter 的一条普通中文回复可在网页上按完整句子显示成多个气泡，原消息、模型调用和保存记录不变。

## 当前状态

现有网页把一次 assistant 事件显示为一个气泡；聊天记录仍以一条原文事件保存。4D 范围只涉及 `web/index.html` 的显示。旧只读仓库 `yecays-ai/veil` 的 `components/chat/messages/message-list.tsx` 以一条 `ChatMessage` 作为完整操作单元，气泡显示与编辑、引用等仍绑定原消息。成熟方案核对：SillyTavern `public/scripts/message-formatter.js`（固定提交 `06bde939fb1e9c4c8d8641d810f0a916b5bce127`，约 3.47 万星，AGPL-3.0）提供分阶段消息格式化，不含气泡切分器；不复制 AGPL 代码。使用 ECMA-402 原生 `Intl.Segmenter` 按句分段，不新增依赖；只在含中文且未检测到 Markdown 语法的回复上启用，纯英文、Markdown、代码块维持一个气泡；不支持该 API 时退回单气泡。分段只作用于网页显示，不更改 Hub 事件或聊天账本。

## 验收步骤

1. 在 Windows 网页聊天让 Peter 回复几句连续中文，确认每个完整句子显示为分开的连续气泡；刷新页面后气泡分段一致。
2. 分别让 Peter 回复纯英文、含 Markdown 的标题/列表/链接/粗体、以及代码块，确认文本与结构完整且没有被拆断。
3. 确认同一条回复只进行一次模型请求，聊天记录中仍是一条完整原文；原有消息复制、选择和滚动行为可用。
