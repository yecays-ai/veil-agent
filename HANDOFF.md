# 交接

## 当前进度

- GitHub `main` 最新为 PR #133（工单 089）合并提交 `21a0a70`。
- 架构审计报告：https://claude.ai/artifact/Xiav6ADUtAg1TxskoTCQRd 。
- 工单 090 位于分支 `ticket/090-simplify-memory-commands`：删除 extract 后台补记与 `/forget`、`/restore`；`[memory] mode` 只接受 `native`；旧状态里的 `cursor`/`pending` 读取时丢弃。

## 下一个窗口第一步

确认 090 的 PR 自动检查和合并状态；之后按 `STATUS.md` 继续：删除改真删（先给 Lan 看要抹掉的清单）、记忆状态文件只写最新、停用 `web/index.html`、上次自主活动查询工具。

## 边界

- 正式运行目录保持 `main`；开发使用工单分支。
- 不改写聊天、记忆或媒体数据；不提交 Persona、密钥、日志或本机数据路径。
- 删除记忆真删会动 Lan 的数据：先列清单给她确认。
