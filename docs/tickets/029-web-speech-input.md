# 工单 029：VEIL 网页语音转文字（最小版）

## 目标

Lan 可在 VEIL 网页聊天输入框旁启动中文或英文语音识别，检查、编辑并沿用现有发送流程提交给 Peter；识别本身不发送消息。

## 当前状态

- 开工时从最新 main `a7ab98718875ac1ecd8b6c1bd855b1beba14465c` 独立施工，不基于 PR #59 分支。施工期间 PR #59 已合并，当前 main 为 `15ede99be00c07b1645a7359262e944ad3acb359`；本 PR 将基于当前 main。
- `web/index.html` 使用浏览器原生 `SpeechRecognition` / `webkitSpeechRecognition`，无新增依赖。已有 `textarea#box`、`send()`、发送去重/失败保留和高度自适应逻辑。
- 复用调查：原生接口直接满足自由口述需求。检查了 [annyang 3.1.1 固定提交](https://github.com/TalAter/annyang/tree/616fb0e9c4b7355ede19d5909cbcf7b6c9783cac)：MIT、轻量无依赖，但核心提供语音命令匹配与回调，不能替代本单的自由转写输入管理，因此不引入。
- 原生识别受浏览器支持、麦克风权限、网络及识别服务影响；不保证离线或本地处理。Lan 已在 Windows Chrome 演示页实测工单 A、B 的中英文例句均识别出文字，浏览器版本未确认。
- 当前实现进行中；未接入 Whisper、付费 API、音频上传或后端语音服务。

## 验收步骤

1. 在 Windows Chrome 中选择中文和 English，分别说工单 A、B 例句；确认识别文字可编辑，并且只有点击发送后 Peter 才收到消息。
2. 输入 `Peter,` 后说工单 C 英文例句；确认原文保留，识别内容追加一次且顺序正确。手动停止、刷新页面后继续普通聊天，确认键盘输入、Enter、Shift+Enter 和发送可用。
3. 拒绝麦克风权限或在无识别服务时启动；确认提示清楚，已有输入与已确认文字保留，且不会自动重试或发送。

