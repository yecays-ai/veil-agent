# 交接

- 2026-10-22 Codex：工单 067 PR #102 已合并，merge commit `12fdb3586f91614cb38a0144344aebedda04d513`；Lan 的 Windows 场景验收仍待做，不能用 CI 代替。
- 当前工单 068 分支 `ticket/068-live-capability-awareness`，PR #105。截图证明 Peter 会搜索 workspace 猜能力；已以 `Chat + 实际工具 schema + 假模型` 复现旧请求缺少可回答的当前能力清单。修复使动态英文快照按本轮实际开放 schema、策略和运行状态生成，模型与能力面板共用同一来源；工具调用循环中会按实际可调用工具刷新，自主活动启停写入日期历史。
- PR #105 首轮 Cubic 提出的 5 项实质问题已在 `82cdd99` 修正并标记解决；该提交的 GitHub CI（Windows/Ubuntu、Python 3.11/3.12、依赖安全）通过。同步 main `c45504c` 后，Cubic 第二轮发现两个新问题：无工具配置时的角色目录重定向安全边界，以及 UTC 测试允许非零偏移。第一个已先由虚构 symlink 角色红测复现（断言 UserError 未触发），后修复为所有配置分支先校验角色目录；第二个已改为断言偏移为零。
- 第二轮修补提交 `7af7547` 已推送到 PR #105。最新本地完整回归（仓库 `.venv`）365 项通过、3 项可选跳过，`git diff --check` 通过。注意系统 Python 缺依赖，不可用它代替仓库解释器。该提交 GitHub Windows/Ubuntu、Python 3.11/3.12、`audit`、`scanner-report-smoke` 和依赖安全均通过；Cubic 将第二轮两条意见标记解决，但该 head 的 Cubic check 仍显示 `in_progress`。
- main 已同步到 `c45504c`（PR #106 / 工单 069 独立合入），STATUS/HANDOFF 冲突已按两单并列状态解决。
- PR #105 描述与 `docs/tickets/068-live-capability-awareness.md` 已补上能力目录和 UTC 时间线：逐项列明 PR #13、#16、#34、#48、#56、#61、#97、#102 的合并时点、Peter 获得的能力及限制，并列出本单修补内容。日期只代表已验证的 GitHub 合并时间；Peter 本机启用时间只能取自私人本机记录，未虚构。
- main branch-protection 端点虽返回 403，但 repository ruleset `VEIL Main Protection` 可读：main 禁删除/强推，PR required approval 数为 0，严格要求四组 Windows/Ubuntu Python 3.11/3.12、`audit`、`scanner-report-smoke` 六项检查；没有 bypass actor。不得绕过规则。Lan 的 Windows 自然聊天验收尚未做，不能宣布完成。
- PR #105 描述已加入完整能力工具目录和 PR #13/#16/#34/#48/#56/#61/#97/#102 的 UTC 合并时间线，以及本单修补历史；PR 正文注明各日期是代码合并日期，不冒称 Lan 实际启用时间。Cubic 已将第二轮两条 inline 意见标记解决；Cubic check 仍在 `in_progress`。
- Cubic 第三轮发现：能力历史启用提示不应依赖文件工具开关；修复应在仍有自主活动/记忆/消息工具的配置中写入现有能力历史，且兼容记录文件尚不存在。已移除错误的 enabled 条件、支持首次创建记录，并新增“无文件工具也记录自主活动提示”的假数据回归；该测试修复前因提示为空、移除条件后又因文件不存在而失败，修复后通过。仓库 `.venv` 全套 365 项通过、3 项可选跳过，`git diff --check` 通过。
- 修正 PR #105 时间线：该 PR 仍未合并，不能把 2026-10-22 标为合并时间；本单日期标记为修补记录日期，待实际合并后补写合并时间。能力表覆盖 PR #13/#16/#34/#48/#56/#61/#97/#102 的已核验合并时间、获得能力及限制，并列出本单不新增能力的各项修复。
- 下个窗口第一步：更新 STATUS/HANDOFF、提交并推送第三轮修补，更新 PR 正文并请求 Cubic 第三轮复审；随后等最新六项 required checks 和 Cubic check 通过，再按 ruleset 正常合并。保持未跟踪 `.worktrees/` 不动。Lan 的自然聊天体验验收仍待做：面板与 Peter 一致，Peter 不经用户解释能说出当前能力与限制。
- 范围：不改记忆系统、Ombre、长期任务执行或真实文件权限；只用虚构数据和假模型。
- 当前工单为 068 Windows 真实运行验证，分支 `ticket/068-windows-runtime-validation`，基于 main `12fdb3586f91614cb38a0144344aebedda04d513`。066 曾被并行工单占用，067 现已由 PR #102 能力通知工单使用；本工单统一顺延到 068。原 067 运行验证 PR #103 不再使用，后续以 #104 为准。
- PR #102 已合入 main；合入提交为 `12fdb3586f91614cb38a0144344aebedda04d513`。head `83a8a091f77cf28ba03ebb512693fa067967c77e` 的 Tests #934、Dependency security #65 成功，Cubic 意见已处理。
- 开发端 Windows 证据：Electron fake-service smoke、桌面后端 2 项、Python 离线回归 350 项（跳过 3 项）、Ombre 隔离恢复门禁 1 项；实际启动、退出、重启了虚构角色 Python Agent/Ombre/MCP，后端认证复用成功，MCP 虚构文件 CRUD 和目录穿越拒绝均通过，聊天文件重启前后字节一致。
- 未触碰 `data/peter`，未调用模型或付费服务。Electron 窗口到真实 Python Agent 合并链路、聊天模型与记忆集成、模型驱动工具、自主调度、长时间运行和桌面备份恢复仍未验证。虚构角色配置及数据留在本机忽略目录，不能提交。
- 原 PR #103 因 main 更新且工单编号冲突被新 PR #104 取代。新 PR 最新 head 的 CI 与 Cubic 尚待核验；合并后按 STATUS 的三步交给 Lan 真人验收。第四阶段仍进行中，未启动第五阶段。

### 068 Windows 运行验证：下一个窗口第一步

动态读取 PR #104 当前 head、Cubic 审查、必需 CI 和 main 保护状态。实质风险修完并满足门禁后合并；再请 Lan 使用 STATUS 中的隔离启动器完成体验验收。
- 当前目标：处理 069 Ombre 重启后自动记忆检索仍失败。PR #107 由诊断阶段和故障表现定位到恢复后没有补发确定未送达的调用；未把私人聊天或记忆内容写入仓库。
- PR #107 在 `ticket/069-retry-unsent-memory-calls`。代码仅在连接被拒且 Ombre 重启经验证成功后补发一次；异常组的所有成员都必须证明未送达。可能已到达服务的调用不重放，补发失败仍返回 `unknown` 以保留结果不明写入保护。健康回复清零连续恢复计数，累计计数保留。
- Cubic 的两项实质意见已在代码提交 `7fe426a` 修复，内联线程已解决。STATUS/HANDOFF 文档更新后会产生新 head；CI 需按最新 head 重新核对。
- CI 工作流覆盖 Windows/Ubuntu、Python 3.11/3.12，运行隔离 Ombre 生命周期恢复及完整离线测试。当前执行结果以最新 PR head 的 GitHub 检查为准。
- 本机一次 MCP 测试在测试构造阶段因当前受限运行时无法创建 Windows asyncio Proactor socketpair 而卡住；这不是 Ombre 检索的产品结果，也不能替代 CI。不要重复跑这个受阻路径。
- 下一步：确认最新 head 的必需 CI、Cubic 实质意见和实际分支保护；全部通过后合并 PR #107。合并后只请 Lan 做正常聊天、已存记忆回忆、重启后继续聊天的体验验收，不要求她制造故障或传聊天/记忆正文。
