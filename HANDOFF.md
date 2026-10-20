# 交接

更新：2026-10-20 18:38 +08:00 · Claude · 写好 020 常驻服务工单

## 做到哪

- 019 补充：`tools_agent.py` 的能力清单新增 `messaging`，send_message 上线或关闭时触发一次能力变化记录；新增测试 `test_existing_manifest_announces_send_message_once`。

- 工单 `docs/tickets/019-multi-message.md`；独立 worktree 分支 `ticket/019-multi-message`，基于 main `38b8826`。用户明确要求旧目录及全部未提交改动原样保留，不覆盖、清理、stash 或提交；后续继续在本 worktree 操作。
- 已实现应用自有 `send_message`、纯消息步骤不追加请求、混合工具继续原有流程、最终文字去重、assistant 开头的历史兼容，以及网页 Unicode 字数节奏和 15 分钟分组。中文、英文及混合语言均适用；不改人设、不按句子拆分原文、不新增主动消息调度。
- [PR #41](https://github.com/yecays-ai/veil-agent/pull/41) 的实现提交为 `0cb0ec8`，GitHub Windows/Linux、Python 3.11/3.12 四项 CI 通过；CodeRabbit 评审已完成，无可操作评论。本次收尾提交只更新文档。
- Windows 离线全量测试 170 项：167 项通过，3 项可选集成跳过；包含假模型、HTTP 事件和实际页面脚本的虚拟时钟/DOM 验证。无真实模型或付费接口调用；真实 Windows/DeepSeek 体验待 lan 验收，不能写成通过。

## 独立记忆质量实验 017

- 报告：`docs/reports/017-serein-vs-ombre.md`。记忆实验重用了已用于 4D 气泡的 017 编号，不改变 4D/019 当前状态。
- 单批虚构样例显示 Serein 保存/证据较强、召回弱；Ombre 本次自动保存为零。后续需用其他样例复核 Ombre 零保存，不应泛化。
- 不重跑模型、不迁移数据。费用账本保留，不纳入提交。

## 下一步

按 `docs/tickets/020-resident-service.md` 施工 020 常驻服务，分支 `ticket/020-resident-service`，基于 main。只做服务、状态和暂停开关，不加任何会自己运行的后台任务；托盘/开机自启动只调查。

先核对 PR #41 的合并状态；合并后按 `STATUS.md` 的 019 步骤安排体验。旧启动目录尚未切到 019，不得切换或覆盖旧目录；启用新版本时仍需保留旧目录和用户文件。

## 注意

- `send_message` 是应用本身的工具，不通过 filesystem MCP 注册；MCP 不可用但工具配置启用时仍能发消息。其他工具继续走原 allowlist、审批、预算及长度检查。
- 纯消息步骤不预留下一次请求。消息先保存再输出，最终回复只与本轮已发文字作精确去重；聊天记录保留独立条目和 `origin=reply`。
- 连续 assistant 历史目前原样交给接口，尚无真实 DeepSeek 拒收证据；若实际拒收，只在请求层合并，本地记录不得改写。
- 页面首轮加载和历史重放不等待；实时消息的排队仅影响显示。节奏测试不等同于人工浏览器体验验收。
- Git HTTPS 推送不可达，已用 GitHub API 发布，并核对实现 tree 与本地已验证内容逐字节一致。本地初始提交与远端提交的元数据不同；后续开发先以 GitHub PR head 为准同步，不强推旧本地提交覆盖远端。
- 015 删除语义、lab 写入边界、成长日记授权、12 步上限及费用硬上限不变。014、015、018 及 011 剩余场景仍待原有体验验收。
