# 交接

- 071（发图片）、072（发语音）工单已在 main：Lan 要能感受到的新功能，优先于加固类工单。图片直接用 Peter 当前模型看（不另接描述模型）；语音识别复用 `EMBED_API_KEY` 的硅基流动 key。参考实现在 Lan 的旧项目 `yecays-ai/veil`。073 Peter 说语音用 Lan 在 MiniMax 克隆好的音色（与小手机同一套 key/voice_id/区域，放本地 .env），费用 Lan 已同意。
- PR #112 的 071 初版只做本机图片气泡，没有把图交给模型，并被审出发送崩溃、busy 卡死、异步串图、孤儿上传等问题；不要合并。Codex 在 `ticket/071-image-upload-rebuild` 基于 main `55d7df6` 接管，复用其 UI 外壳但重建上传提交边界和真实多模态消息。Fount 只借“await 前冻结附件快照”的契约，不复制其 JS 多标签页结构。
- 2026-10-22 Claude：PR #105（068 能力认知）与 PR #104（068 Windows 运行验证）已合并，main `becf0e5`。两个 PR 都编号 068 是并行工单撞号；下一个新工单从 070 起，开工前先查 main 和打开的 PR 里已用的编号。
- PR #108（fork）指出 #104 的验收步骤依赖开发端忽略目录 `.local-tmp/ticket-067-runtime`，Lan 的仓库里没有。本次已把 STATUS 和 `docs/tickets/068-windows-runtime-validation.md` 的验收改为现有 Peter 正常入口；#108 本身因整段覆盖 STATUS/HANDOFF 会丢掉 #105 的内容，未合并。
- 本次同时把 STATUS/HANDOFF 里多轮合并叠出来的过期段落（“PR 仍在审”“下一步合并 #10x”等）清掉，只保留当前事实。各 PR 的详细过程见对应 PR 正文和 `docs/tickets/`。
- PR #109：`CURRENT CAPABILITIES` 和 `CAPABILITY RECORD` 不再每轮注入模型上下文。Lan 的原意是“加了新东西就通知他一次，他自己知道去哪查”：一次性通知保留（#102），新增只读工具 `capability_history` 返回带时间戳的记录。不要再往每轮上下文里加能力说明或清单；能力面板仍向 Lan 显示快照与记录，但能力是做给 Peter 的，Peter 必须能自己查到。
- 开发端事实：仓库测试必须用 `.venv` 解释器；系统 Python 缺依赖。main ruleset `VEIL Main Protection` 要求六项检查（Windows/Ubuntu × Python 3.11/3.12、`audit`、`scanner-report-smoke`），不得绕过。
- Lan 的运行目录只留在 main，更新用 `git pull --ff-only`；Codex 在 `.worktrees/` 工作，不要动运行目录里的未跟踪 `.worktrees/`。
- 未处理风险：Ombre 进程退出原因（退出码 −1）仍未查明。
- 工单 070 分支 `ticket/070-main-updater-stays-main` 基于已合并 #109 的 main `d17cfec`。修复成功更新后又切回旧 ticket 分支的问题；成功路径停在 main，安全检查失败仍回原分支。Windows 隔离 Git 回归已同步该契约，等待 Cubic 与必需 CI。

## 下一个窗口第一步

检查 PR #113 最新提交的 CI 和 Cubic 复审，不用旧提交的绿灯或线程关闭状态代替新结果。本轮补全发送的 finally 收尾（保存失败前/后分别报告是否已持久化）、历史媒体 MIME 与 5 MB 读取边界、慢磁盘下的提交预留；事件锁不再包住磁盘操作，预留失败自动释放，避免并发重复入队。新增保存失败、损坏历史、文件增大和慢磁盘并发测试。Windows 实际体验尚未验收；合并后关闭被替代的 #112，再请 Lan 按 `docs/tickets/071-send-photos.md` 验收。
