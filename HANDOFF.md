# 交接

## 当前进度

- 正式运行目录 `E:\Peter-Agent\VEIL\veil-agent` 保持 `main`，已快进到 PR #126 合并提交 `fb8c1bbff7af418a79b668b947894a529eed663a`。
- PR #126（工单 082）已 squash 合并；Tests、Windows desktop、Dependency security 均成功。UI 的 Windows 真实体验仍待 Lan 验收。
- 工单 083 的修复已在独立 worktree `E:\Peter-Agent\VEIL\worktrees\083-chat-reliability`、分支 `ticket/083-chat-reliability` 集中完成，正准备推送并开单独 PR。
- 已实现 provider token usage 校准与上下文分项估算、截图中的 DSML 变体拦截、完整聊天账本语音去重和有效时长事件更新。
- `test_chat` 全部 61 项与两个目标 Web 用例通过；Node 语法与 `git diff --check` 通过。Web 全套尝试运行时沙箱拒绝本地回环端口，CI 尚待。

## 下一个窗口第一步

复核工单 083 和最终 diff，确认校准只按相同角色/模型复用且不保存聊天文本、旧语音以完整角色聊天账本判定、DSML 只被拦截而从不解析执行。随后为这批改动创建一个提交并只推一次，开 PR 后等 CI 和 CodeRabbit。

## 边界

- 生产目录只保持并更新 `main`；代码继续只在工单 083 独立 worktree 中改。
- 不输出或提交真实聊天内容、记忆、媒体、Persona 或密钥；context calibration 只存 provider 输入 token 数和请求估算字节数。
- 不降低 provider 的 token 上限，不缩短记忆，不改成本预留或模型；界面提示明确标注分项为估算值。
- PR 合并前保留工单 worktree。桌面 UI 仍需 Lan 的 Windows 体验验收。
