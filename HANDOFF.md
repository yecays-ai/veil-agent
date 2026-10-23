# 交接

## 上一位停在哪

- 073 在独立 worktree `.worktrees/ticket-073-peter-voice`、分支 `ticket/073-peter-voice`；已重放到 #114 合并后的 main `b2d5c8b`。只做 Peter 输出语音，同时保留 #114 的用户录音、麦克风和 SenseVoice 路径。
- 只做 Peter 输出语音：MiniMax `t2a_v2`、`send_message voice:true`、`data/<角色>/media/*.mp3`、assistant 历史相对路径与原文、网页播放/展开、共享预算、失败降级和能力通知。没有改 072 worktree、用户录音输入、麦克风或 SenseVoice。
- 自动验证：Cubic 实质意见集中修复后，`.venv` 下 405 项 Python 测试通过（3 项真实外部适配器按设计跳过）；`node tests/web_pacing.cjs` 通过；桌面后端 2 项通过；复用正式目录已安装的 Electron 对本 worktree 跑 smoke，通过图片、用户录音、Peter 输出语音历史、播放控件、文字展开、重开与刷新。
- 尚未验证：真实 MiniMax key/voice_id/模型/区域、与小手机音色一致、真实 `/cost` 金额、退出服务再启动后的人工播放。这些必须按 `docs/tickets/073-peter-voice.md` 原验收步骤由 Lan 完成，不能用自动测试代替。

## 下一个窗口第一步

检查 PR #115 最新 head 的 Cubic 复核和六项必需 CI；实质问题已修完后合并，不追风格和低影响建议。

## 仍然成立的背景与注意

- PR #113、#114 已合并；不要复制或回退到 #112。
- `MINIMAX_API_KEY`、`MINIMAX_VOICE_ID`、`MINIMAX_MODEL`、`MINIMAX_REGION` 只从本地 `.env` 读取；四项不全或区域不是 `cn/global` 时，voice 参数和语音身份事实都不出现。`MINIMAX_CNY_PER_10K_CHARS` 默认 3.5。
- 复用结论已写入 073 工单：小手机实现与 MIT 的 MoneyPrinterTurbo 只提供请求和边界契约，本仓库没有新增依赖。
- 正式运行目录只保持 main，未跟踪 `.worktrees/` 不动；所有施工都在 073 worktree。
