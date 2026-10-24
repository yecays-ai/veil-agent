# 交接

## 当前进度

- GitHub `main` 最新为 PR #132（工单 088）合并提交 `772a3bb`。
- 架构审计报告：https://claude.ai/artifact/Xiav6ADUtAg1TxskoTCQRd 。
- 工单 089 位于分支 `ticket/089-cleanup-capability-notices`：五个 `announce_*` 合并为 `_announce_once`；`chat-session.json` 不再写 `language`，读取兼容旧文件。

## 下一个窗口第一步

确认 089 的 PR 自动检查和合并状态；之后按 `STATUS.md` 的 Lan 决定继续：extract 退役、停用 `web/index.html`、记忆状态文件只写最新、记忆删除简化方案。

## 边界

- 正式运行目录保持 `main`；开发使用工单分支。
- 不改写聊天、记忆或媒体数据；不提交 Persona、密钥、日志或本机数据路径。
- “上次自主活动”每轮注入的做法未经 Lan 确认不改。
