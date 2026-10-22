# 交接

- 072 正在本地分支 `ticket/072-voice-messages`、独立 worktree `E:\Peter-Agent\VEIL\worktrees\072-voice-messages` 施工；已变基到 PR #113 当前最新 head `b60912d`，不开 PR、不 push，等 #113 合并后再同步最终 main、解决差异、完整复测并集中 push。
- 072 首轮链路已完成：MediaRecorder 最长 120 秒/10 MB；音频先持久化再调用 SenseVoiceSmall；成功后模型收到且聊天保存精确的 `[voice message] <转写>`，失败不发模型、保留录音并手动重试；历史重开可回放。Electron 只允许当前本机认证聊天窗口申请 audio，video、宠物页和外部来源拒绝。变基后 389 项 Python（3 项约定跳过）、网页节奏、Electron 单测、隔离 smoke 通过；真实麦克风和真实转写尚未验收。
- 旧 071 Codex worktree 已从 Git 移除，未提交脏改动和备份均已删除，本地旧分支也已删除；旧外部路径只剩被进程占用的空目录。不得从 #112 或该目录恢复任何内容。
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

先核对 PR #113 是否已合并；未合并就继续在当前临时底座做只与 072 有关的本地审查，已合并则同步最终 main、解决差异并重跑完整验证。不要从旧 #112 分支或旧 worktree cherry-pick。
