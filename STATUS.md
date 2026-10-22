# 当前状态

更新：2026-10-22 UTC · Codex。

## 当前目标与现状

- **069 Ombre 重启后记忆调用补发（Claude 修复）：** lan 机器日志显示 Ombre 进程退出后，第一次 breath_search/hold 连接被拒（ConnectError），程序已成功重启 Ombre，但仍把这次调用报为失败。现在只有“连接被拒、请求根本没发出去”且重启已验证时，才把同一调用补发一次；请求可能已到达 Ombre 的失败仍不重放。成功调用后恢复次数清零，长时间运行不会在两次恢复后永久失去自动恢复。Windows 验收：关掉 Ombre 进程后直接聊天，Peter 搜记忆应成功，不报错。

- **067 能力变化通知可靠交付：** PR #102 已于 `12fdb3586f91614cb38a0144344aebedda04d513` 合并；GitHub CI 通过，Lan 的 Windows 实际体验验收仍待执行。
- **068 Windows 真实运行验证：** PR #104 仍在审，基于 main `12fdb3586f91614cb38a0144344aebedda04d513`；隔离虚构角色的 Windows 验证不能代替真实聊天、记忆读写或 Lan 体验验收。
- **069 breath_search 失败阶段诊断：** 分支 `ticket/069-breath-search-diagnostics`，基于 main `12fdb3586f91614cb38a0144344aebedda04d513`。为避免重放未确认的检索，MCP 会返回 `MemoryToolError/unknown`，但此前会丢失安全失败阶段；现以固定的 HTTP、MCP 初始化、工具执行或恢复阶段记录在角色私有诊断中，聊天提示相同且不含私人内容。

## 下一步

工单 069：运行完整离线回归，推 PR 并核对 Cubic 与最新 CI；合并前不宣称真实 Windows 聊天验收已经完成。

## Lan 的体验验收

1. **067 待验收：** 给 Peter 增加 `lab_file_digest` 与 `move_lab_file`，确认面板和 Peter 收到同一完整英文更新，之后多轮不重复。
2. **069 待验收：** 正常连续与 Peter 聊天并询问已记住的事项，确认不会再出现不含阶段的检索失败；若出现检索故障，保留角色私有诊断供 agent 判断，勿发送聊天或记忆正文。
