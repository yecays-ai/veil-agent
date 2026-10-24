# 交接

## 当前进度

- GitHub `main` 最新为 PR #135（工单 091）合并提交 `41e149c`。
- 架构审计报告：https://claude.ai/artifact/Xiav6ADUtAg1TxskoTCQRd 。
- 工单 091 已合并：`Memory.erase` 在 Ombre 确认删除（文件已进 `archive/`）后删除该文件、`memory-versions.jsonl` 中该记忆的行，并清空 `delete-requests.jsonl` 中的标题/理由和状态里的标题/正文；启动时对已删除未抹掉的条目各执行一次。Ombre 自行衰减归档的记忆不受影响。
- 工单 092 位于分支 `ticket/092-memory-state-latest-only`：新增 `Store.replace`（临时文件 + fsync + `os.replace` 原子替换），`Memory.persist` 用它只保留最新一行状态。读取逻辑不变，旧的多行文件第一次保存后自动变成一行。

## 下一个窗口第一步

确认 092 的 PR 自动检查和合并状态；之后按 `STATUS.md` 继续：停用 `web/index.html`、上次自主活动查询工具。

## 边界

- 正式运行目录保持 `main`；开发使用工单分支。
- 不改写聊天、记忆或媒体数据；不提交 Persona、密钥、日志或本机数据路径。
- Ombre 的 `_ledger`、`dehydration_cache.db`、`memory.backup-*` 文件夹和聊天原文不在抹除范围内。
