# 当前状态

更新：2026-10-22 12:12 UTC · Codex。

## 当前目标与现状

- **067 能力变化通知可靠交付：** PR #102 已于 `12fdb3586f91614cb38a0144344aebedda04d513` 合并；GitHub CI 通过，Lan 的 Windows 实际体验验收仍待执行。
- **068 Windows 真实运行验证：** PR #104 仍在审，基于 main `c45504c`；隔离虚构角色的 Windows 验证不能代替真实聊天、记忆读写或 Lan 体验验收。
- **069 breath_search 失败阶段诊断：** PR #106 已合入 main `c45504c`。为避免重放未确认的检索，MCP 会返回 `MemoryToolError/unknown`，并以固定的 HTTP、MCP 初始化、工具执行或恢复阶段记录在角色私有诊断中，聊天提示不含私人内容。
- **068 当前能力认知与能力面板一致性：** PR #105，分支 `ticket/068-live-capability-awareness`。旧实现请求级红测确认模型请求缺当前清单；动态当前快照由实际工具 schema、授权策略和运行状态生成，网页与模型同源。PR 已列出能力获得与修补时间线；PR #105 未合并，相关日期明确为修补记录日期而非合并日期。Cubic 第三轮指出：无文件工具但仍有自主活动时，启用通知错误依赖文件工具状态。本地已修复并新增虚构角色回归；全套 365 项通过、3 项可选跳过，`git diff --check` 通过。该最新修补尚未提交、推送或经过 GitHub CI/Cubic 复审。Lan 的 Windows 体验验收未执行。

## 下一步

工单 068：提交并推送第三轮修复及时间线更正，更新 PR 正文，请 Cubic 复审；确认最新六项 required checks 全绿后，按 `VEIL Main Protection` ruleset 合并。Lan 的 Windows 体验验收仍待做，合并不代表体验验收完成。工单 069 已独立合入 main，不混入本单实现。

## Lan 的体验验收

1. **067 待验收：** 给 Peter 增加 `lab_file_digest` 与 `move_lab_file`，确认面板和 Peter 收到同一完整英文更新，之后多轮不重复。
2. **068 施工中：** 打开能力面板确认当前真实能力和权限；问 Peter “What tools can you use right now, and what are their limits?”，确认回答无需搜索文件且与面板一致。
3. **069 待验收：** 正常连续与 Peter 聊天并询问已记住的事项，确认不会再出现不含阶段的检索失败；若出现检索故障，保留角色私有诊断供 agent 判断，勿发送聊天或记忆正文。

- **067 能力变化通知可靠交付：** PR #102 已合入 main；对应提交 `12fdb3586f91614cb38a0144344aebedda04d513`。head `83a8a091f77cf28ba03ebb512693fa067967c77e` 的 Tests #934 与 Dependency security #65 成功；Cubic 实质意见已处理。Windows 真人体验仍待验收。
- **068 Windows 真实运行验证：** 分支 `ticket/068-windows-runtime-validation`，PR #104，基于 main `12fdb3586f91614cb38a0144344aebedda04d513`。使用虚构角色与隔离目录，未访问或修改 `data/peter`，未调用模型或付费服务。
- **开发端已验证：** Windows Node 24.22.0、npm 11.19.1 按 lockfile 安装 Electron 45.0.0。`desktop/npm test` 2 项通过；`desktop/npm run test:windows` 真实打开 Electron 窗口并通过历史显示、单次发送、拒绝审批、收起/重开、缩放和沙箱检查。Python 离线回归 350 项通过、3 项跳过；隔离 Ombre 恢复门禁 1 项通过。
- **真实 Windows 服务验证：** 虚构角色 Python 服务、Ombre 子进程及固定版本文件系统 MCP 实际启动；桌面 backend 认证复用成功。服务退出重启后记忆和文件能力重新就绪；MCP 对虚构 Lab 文件创建、读取、追加成功，并拒绝目录穿越。服务重启前后聊天文件字节一致。
- **尚未验证：** Electron 窗口与真实 Python Agent 合并链路、模型回复、聊天内记忆写入/检索、模型驱动工具调用、自主活动调度、长期运行、桌面备份/恢复。未复现阻断产品故障，无生产代码修复。第四阶段仍进行中；不得启动第五阶段。

### 068 Windows 运行验证：下一步

合并 PR #104 后，请 Lan 按下方三步在本机隔离虚构角色上验收；据验收结果继续补足未覆盖的 Windows 场景。

### 068 Windows 运行验证：Lan 的体验验收

1. 从仓库根目录打开 `.local-tmp/ticket-067-runtime/.local-tmp/`，先双击 `Start Fictional Agent.bat`，再双击 `Start Fictional Desktop.bat`。该虚构角色及数据已由开发端预先准备在 `.local-tmp/ticket-067-runtime`，只使用离线占位模型地址和 `data/runtime-fixture`。桌面形象出现后先右键选择“更换 Agent 文件夹”，在文件选择框中选 `.local-tmp/ticket-067-runtime`（其中有 `start-web.bat` 和虚构角色配置）；确认后再点击形象打开聊天。若该隔离目录不存在，停止验收，不要选择 Peter 的目录。确认虚构历史显示，输入 `/cost`、`/memory`、`/autonomy` 查看本地状态，不要发普通聊天。
2. 输入 `/memory` 和 `/autonomy`，确认本地状态可读取；桌面 Memory / Ombre 入口显示不可用提示。
3. 在 Agent 控制台按 `Ctrl+C` 停止服务，再双击 Agent 启动器；在桌面形象上点右键，选“重新连接聊天”。确认虚构历史保留、账本无重复记录。不要操作真实 Peter 的服务或目录。
- **PR #106 / 069 失败阶段诊断：** PR #106 已合并到 main，提交 `c45504c11e4b74f9803506741c4fd9db1b8dc34b`；GitHub Tests 和 Dependency security 通过。真实 Windows 聊天验收仍未完成。
- **069 Ombre 重启后的检索失败：** 安全诊断序列确认了故障路径：`breath_search` 连接被拒（`ConnectError`），Ombre 重启成功，但原调用仍以 `unknown` 失败。原因已定位到恢复后没有重发这次确定未送达的调用。
- **PR #107 修复范围：** 只在连接被拒且 Ombre 重启经验证成功时补发一次；异常组内所有原因都必须证明未送达。任何可能已到达服务的调用不重放；补发失败保持 `unknown`，写入结果不明保护不变。健康响应清零连续恢复计数，累计恢复次数保留。
- **PR #107 当前状态：** 基于 main `c45504c`，head `7fe426a`。Dependency security 已通过；Tests 仍在运行。Cubic 指出的两项实质安全问题已在该 head 修正，两个评论线程已解决。实际 Windows 验收尚未完成。
- **068 Windows 验证：** PR #104 仍打开，基于较早的 `12fdb358`；不与本次记忆故障修复混在一起。


1. 确认 PR #107 最新 head 上所有必需 CI 完成并通过，复核 Cubic 实质意见及仓库分支保护。
2. 满足门禁后合并 PR #107。
3. 合并后只做正常聊天体验验收：连续对话、询问一条已保存信息、重启后继续聊天和检索。无需人为关闭服务或反复触发故障；如遇异常，只需告知 agent，不发送聊天正文或私人记忆日志。


1. 连续与 Peter 正常聊天，确认检索异常不会挡住回复。
2. 询问一条已经保存的事实，确认 Peter 能从记忆中准确检索。
3. 重启 Peter 后继续聊天并再次询问已有事实。
4. 无需主动制造故障；故障诊断由 agent 使用隔离虚构数据处理。
