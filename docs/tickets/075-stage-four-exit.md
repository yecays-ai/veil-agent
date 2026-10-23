# 工单 075｜Reliability 第四阶段集中退出验收

## 目标

让 Lan 只验收开发端无法代替的真实设备与主观体验，并据此明确第四阶段是否退出。

## 当前状态

基线为 main `ae4c425ac64de147d605e5862e7198796f31533a`。PR #116（074）已合并，合并提交 `ae4c425`。PR head `287505e` 的 Tests #1034、Windows desktop #32、Dependency security #113 均成功；Cubic 实质意见已解决。

| 工单 | GitHub 合并证据 | 自动与隔离运行证据 |
| --- | --- | --- |
| 068 Windows 运行与能力快照 | PR #104 → `becf0e5`；PR #105 → `c796d1f` | 隔离 Windows Agent、Ombre、文件 MCP 与 Electron 运行通过；Tests #978/#973、Dependency security #85/#82 成功。 |
| 069 Ombre 恢复与阶段诊断 | PR #106 → `c45504c`；PR #107 → `e99572c` | 虚构数据覆盖拒绝连接后的安全恢复与仅对确认未送达的调用重试；Tests #947/#969、Dependency security #71/#81 成功。可能已送达或结果不确定时不重放。 |
| 070 更新器 | PR #110 → `20f106d` | 隔离 Windows Git 回归覆盖成功后留在 main 和失败时保护文件；Tests #992、Dependency security #92 成功。 |
| 071 图片 | PR #113 → `81af94f` | Tests #1014、Windows desktop #17、Dependency security #105 成功；Electron smoke 验证图片进入模型请求及历史媒体边界。 |
| 072 用户语音 | PR #114 → `b2d5c8b` | Tests #1024、Windows desktop #24、Dependency security #109 成功；隔离 Electron smoke 覆盖麦克风权限、录音、转写、失败重试与历史播放。 |
| 073 Peter 语音 | PR #115 → `39239e5` | Tests #1029、Windows desktop #28、Dependency security #111 成功；隔离 smoke 覆盖语音历史、播放和边界，未调用真实 MiniMax。 |
| 074 长期运行与恢复 | PR #116 → `ae4c425` | Tests #1034、Windows desktop #32、Dependency security #113 成功；本地 Python 408 项通过、2 项可选跳过，桌面 Node 2 项通过；隔离真实进程恢复、Electron/Web Agent 和双角色备份恢复通过。 |

这些证据足以免除 Lan 重跑自动检查、故障注入、服务恢复、备份恢复和权限边界。PR #113–115 的记录仍要求真实模型/设备体验；仓库尚无 Lan 完成这些体验的记录，不能将 CI 或开发端 smoke 当作她已验收。

当前唯一阻断项：Lan 尚未完成以下集中真实体验。完成后通过本单记录结果并退出第四阶段；若体验失败，只为具体失败开一张有边界的新工单。

## 验收步骤（Lan 在 Windows 上做）

1. 用 Electron 正常打开 Peter，在同一聊天发一张非私人测试图片并问一个具体问题，再追问一个细节；确认图片确实进入 Peter 的本轮回复，回复符合预期，桌面操作顺手。
2. 在同一会话按住麦克风说一句简短中文，确认转写和回复符合预期；请 Peter 发一条短语音并播放，确认录音、识别和克隆音色符合预期。一次 TTS 生成按 073 估算约 ¥0.1。

## 范围说明

本单只汇总已有合并与 CI 记录，不改产品代码、不重复运行 074、不调用真实模型或付费服务。复用仓库既有的 064 阶段退出记录结构；没有新增能力或测试框架。
