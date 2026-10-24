# 交接

## 当前进度

- GitHub `main` 最新为 PR #134（工单 090）合并提交 `c7527f6`。
- 架构审计报告：https://claude.ai/artifact/Xiav6ADUtAg1TxskoTCQRd 。
- 工单 091 位于分支 `ticket/091-erase-deleted-memories`：`Memory.erase` 在 Ombre 确认删除（文件已进 `archive/`）后删除该文件、`memory-versions.jsonl` 中该记忆的行，并清空 `delete-requests.jsonl` 中的标题/理由和状态里的标题/正文；启动时对已删除未抹掉的条目各执行一次。Ombre 自行衰减归档的记忆不受影响。

## 下一个窗口第一步

确认 091 的 PR 自动检查和合并状态；之后按 `STATUS.md` 继续：记忆状态文件只保留最新一份、停用 `web/index.html`、上次自主活动查询工具。

## 边界

- 正式运行目录保持 `main`；开发使用工单分支。
- 不改写聊天、记忆或媒体数据；不提交 Persona、密钥、日志或本机数据路径。
- Ombre 的 `_ledger`、`dehydration_cache.db`、`memory.backup-*` 文件夹和聊天原文不在抹除范围内。
