# VEIL 全局配置审计（2026-11-25）

- 审计对象：`yecays-ai/veil-agent` **main @ 6efd35c**（P3-4 合并后）。取证主体在 `e7e7992`（P3-3）完成，P3-4 合并后逐项复核了差异；审计期间正式运行目录为 `f63e58a`（P3-2），见 §1.3 环境漂移。
- 性质：**只读审计**。未修改任何运行逻辑、配置值、权限、预算或现有 UI；未启动、停止或触碰正式 Peter。
- 用途：作为「VEIL 全局管理中心 + UI 改造」的施工依据。
- 配套文件：
  - `config-inventory.csv`：全量配置清单（一行一项，可直接筛选取值/分类/归属页面）。
  - `evidence/A-budget.md` … `evidence/H-safety-maintenance.md`：八个分域的取证底稿（含逐项 file:line）。
- 证据口径：所有结论以源码 `file:line`、本机只读配置文件、以及正式实例的只读快照为准；无法读取实际值的项明确写「无法读取」。

---

## 0. 一页结论（给 Lan）

### 0.1 现在你实际能从页面改什么

| 页面 | 能改的 | 生效 |
| --- | --- | --- |
| 设置 → 模型 | 5 个用途各用哪个模型；每天/每月费用上限；接口地址与 Key；自建模型的价格、计费方式、最长输入、思考开关 | 聊天模型和费用上限**下一句生效**；记忆后台模型**重启生效**；委派/自主/瞄一眼按各自下一次任务 |
| 设置 → 工作台 | 工作台开关、项目文件夹、允许访问的网站 | **重启生效**（页面会提示） |
| 设置 → 可操作的软件 | 允许 Peter 操作哪些已打开的软件 | 立即 |
| 设置 → 外观 | 主题、透明度、背景图、自定义 CSS、Peter 大小 | 立即（存在浏览器/桌面壳，不进备份） |
| 自主活动 | 自主活动总开关、夜间安静开关、瞄一眼开关、Codex 监视开关、待批准卡片 | 立即 |
| 记忆 | 经历整理开关、想起判官开关；记忆条目的编辑/删除/归档/锁定/版本 | 立即（下一轮读取） |
| Peter | 人设、背景资料、关于你、他记的你（事实/关系/成长/正在进行）、人设历史版本 | **下一句生效**（人设会热加载） |
| 指令 | 99 条程序指令原文里的可编辑条目（含自主醒来规则、记忆提示、说话样例等） | 下一句生效 |
| 上下文 | 说话样例开关与正文、工作断点结束 | 立即 / 下一句 |
| MCP | 启用 MCP、增删本地服务、每个服务「每次询问 / 直接调用」 | 立即（下一次组装工具） |
| 生活记录 | 日程/待办/计划、提醒、聊天约定自动记日历开关 | 立即 |

### 0.2 现在**不能**从页面改、只能改文件的（清单见 §4）

- **自主活动**：90 分钟一次的频率、每天最多 12 次、每天 ¥3、单次 ¥0.25、单次最多 60 步工具、安静时段具体时间（当前 01:00–06:00）、跨唤醒线索开关、自主活动专用输入窗口。
- **瞄一眼**：15–40 分钟间隔、空闲 300 秒、单次最多 4 步、每天 ¥1。
- **记忆**：经历整理阈值（3000 字/空闲 30 分/每天 ¥1/最近 3 天 1500 字）、召回冷却/去重/摘要长度、联想阈值、判官候选数与熔断、事件归组与故事上限、检索权重。
- **模型与上下文**：内置模型（deepseek/gpt）的价格与输入上限（在 `character.toml`）、历史轮数 80、输出上限 1024、工具输出 4096、工具结果 8 KB、工具步数 20。
- **工作台**：执行时限 600 秒、重复上限 3、单次 ¥0.5、自主唤醒 ¥0.3、维护仓库/工作树/GitHub 仓库/维护时长 2 小时。
- **通道与语音**：QQ/邮箱轮询与每日发送上限、语音价格 `MINIMAX_CNY_PER_10K_CHARS`。
- **系统**：备份位置（`.env`）、读取型 GitHub 研究的 token、自我维护的 GitHub token 文件路径。

### 0.3 最该先修的 7 件事

1. **诊断页会误报「模型钥匙没配」**：它读的是进程环境变量，而钥匙只存在 `.env`（`diagnostics.py:415` vs `model_settings.py:574`）。正式实例现在就一直显示这条错误，实际钥匙是好的。这是「页面显示值 ≠ 实际生效值」的实例。
2. **两个 GitHub 权限体系互不相通**：普通只读研究用 `VEIL_GITHUB_READONLY_TOKEN`（只认进程环境变量，写进 `.env` 不生效，`github_readonly.py:288`）；自我维护用 `本机 secrets 目录的 github-token.txt`（当前不存在，`maintenance.py:591`）。现在研究 token 没配、维护 token 文件也没有，维护实际依赖机器上 `gh` 的登录态——权限边界不可见。
3. **自主/瞄一眼/记忆的预算和频率全是文件配置**，而你已经在页面给了它们总开关。关掉能关，想调小/调大必须改代码文件，且 **`character.toml` 是 git 跟踪文件**：上游恰好也改这个文件时，`update.bat` 的 `git pull --ff-only` 会失败；`update-main.cmd` 则先把它 stash 再对齐 `origin/main`（`update-main.cmd:47,106`），改动从工作区消失。
4. **临时提额只活在内存里**：`raise_today` 只加在今天的内存变量上，重启即失效（`veil_agent.py:1343`），并且只对工作台单次任务开放，上限 ¥50。页面无法查看「今天提过多少、还剩多少」。
5. **配置没有单一真源**：同一个开关常有 TOML 默认值 + `data/peter/*.json` 覆盖两层（经历整理、判官、安静时段、瞄一眼、自主活动、Codex 监视都是）。现在只有代码知道谁覆盖谁，页面只显示一层。
6. **死键/遗留键还在**：`[memory] dimensions`（代码不读）、`ombre_model`、`ombre_port`、`[memory.brain] write_authority`（仅测试用）、`.env` 的 `JIUSHI_API_KEY`、`VEIL_CLAUDE_PATH`。两个已无人调用的 Ombre 入口模块已在审计期间由 P3-4 #445 删除。
7. **启用≠生效**：`[email] enabled = true` 但三个邮箱凭据都没配（通道静默不动）；`[qq] enabled = true` 且 `QQ_LAN_CONTACT` 已配；内置 `gpt` 档 `OPENAI_API_KEY` 为空但页面不提示。功能开关需要「已启用 / 已就绪 / 缺什么」三态，而不是一个布尔。

### 0.4 关键数字（2026-11-25 20:30 只读快照）

- `character.toml` 解析出 **137 个叶子配置项**（其中 47 个工具别名），另有注释里可选的 6 组表（recall / recall_judge / recall_context / relationship_base 等）。
- `data/peter/` 下 **32 个 JSON 状态/配置文件**；`.env` **16 个键**；可编辑指令 **99 条**（其中约 60 条可编辑，其余为程序协议/安全文本）。
- 正式实例：自主活动 **当前关闭**（2026-11-25 05:41 关），瞄一眼 **开**，安静时段 **开**，Codex 监视 **关**，MCP **开**（4 个服务全部「直接调用」）。
- 费用：日上限 ¥30 / 月上限 ¥800（`models.json`）；**今天已花 ¥0.947**（聊天 0.806、经历整理 0.071、判官 0.046、自主 0.013、瞄一眼 0.013），**本月已花 ¥165.23**，当前无待核对预留。

---

## 1. 审计范围、方法与边界

### 1.1 范围

覆盖 VEIL 全系统的可配置面：模型与接口、预算与账本、自主活动与后台、记忆、工具与权限、工作台/GitHub/维护、交互与个性化、诊断与备份、以及程序内部的运行限制。范围不限于工单列出的九类，审计中发现的其他可配置项（提示词注册表、桌面 localStorage、Approval TTL、工具清单预算、指令覆盖、指令指纹等）一并纳入。

### 1.2 方法与证据口径

1. **静态取证**：对 main 快照逐文件追踪「谁读、谁校验、谁使用、缺省怎么办」，保留 `file:line`。
2. **实际值取证**：只读打开正式实例的 `characters/peter/character.toml`、`data/peter/*.json`、`models.json`、`.env`（**只读键名与长度，不读值**）、`costs.jsonl`，统计到 2026-11-25 20:30。
3. **链路复核**：从页面入口（`desktop/chat.html`、`chat.js`、`peter-page.js`、`web_agent.py` 路由）反向追到写入函数与运行时对象，判断「页面显示值 = 实际生效值」是否成立。
4. **交叉检查**：同一参数的多来源、死键、只解析不使用、启动即失败的硬约束、启用但未就绪的功能。
5. 不采信聊天记录或历史文档结论；与前序文档冲突时以本次 `file:line` 为准。

### 1.3 环境漂移（只读观察，不是本次操作）

| 维度 | 状态 |
| --- | --- |
| GitHub main | `6efd35c`（P3-4：维护窗口数据清理 + 物理卸载 + 最终验收，#445 已合并）；取证主体在上一提交 `e7e7992`（P3-3）完成 |
| 正式运行目录 | `f63e58a`（P3-2），**落后 main 两个合并提交**；main 上的 `STATUS.md`/HANDOFF 仍把 P3-3/P3-4 写成「施工中」 |
| 审计期间合并 | #445 P3-4（`6efd35c`）只改了 9 个文件：删除 `ombre_entry.py`/`ombre_host.py`、去掉 prompt 引用，新增清理工具/runbook/测试，更新 STATUS/HANDOFF；**不涉及其他配置读写代码**，已逐条复核 |
| 正式实例运行态 | 自主活动关闭、瞄一眼开、Codex 监视关、MCP 开、无待批准卡片 |
| 本审计基线 | 以 main `6efd35c` 为准。P3-3 的影响仅为删除 hidden/dont_surface 行为与 hold 的 `dont_surface` 参数；P3-4 仅删除两个死模块与 prompt 引用。两次合并都未新增/删除配置文件键 |

> 说明：正式目录落后两版不影响本次结论——P3-3/P3-4 的 diff 已逐条核对；配置读取/写入代码在这两次合并中未变。

### 1.4 本次不做

- 不修改任何配置值、权限、预算、UI 或运行逻辑；本 PR 不含代码改动。
- 不启动/重启/停止正式 Peter，不触发真实对话，不写正式记忆。
- 不对正式数据做迁移、清理或重算；不删除死键（清理清单见 §6，留待后续工单与 Lan 授权）。

---

## 2. 配置架构总览

### 2.1 八个配置面

VEIL 的「配置」不是一处，而是八处，各有不同的读写者、生命周期和风险：

| # | 配置面 | 位置 | 谁写 | 是否进 git | 是否进备份 | 生效 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | **代码常量与表态** | `*.py`（`tool_policy`、`tool_groups.BUDGET`、`autonomy.MAX_*`、`executor.DEFAULT_LIMITS` …） | 开发者 | 是 | 随代码 | 重启/发版 |
| 2 | **角色基线** | `characters/<id>/character.toml`（137 键） | 开发者/Lan 手改 | **是** | 是 | **重启**（启动时一次性校验） |
| 3 | **本机覆盖与运行态** | `data/<id>/*.json`（32 个） | 页面/程序 | 否 | 是 | 多数**立即**或下一轮 |
| 4 | **密钥与小配置** | `.env`（16 键） | Lan 手改/模型页 | 否 | 是 | 模块启动时（部分每次读） |
| 5 | **模型与价格** | `data/<id>/models.json` | 设置页 | 否 | 是 | 聊天/上限**立即**，后台**重启** |
| 6 | **文本配置** | `persona/*.md`、`persona/instructions/*.md` | Peter 页/指令页 | 否 | 是 | 下一句 |
| 7 | **界面偏好** | Electron `localStorage` / `desktop.json` | 桌面壳 | 否 | **部分**（`desktop.json` 进，localStorage 不进） | 立即 |
| 8 | **运行时数据** | `brain.db`、`data/costs.jsonl` | 程序 | 否 | 是 | 立即（账本/记忆） |

`data/`、`persona/`、`.env` 都在 `.gitignore` 里，因此页面能改的东西**天然活过更新**；被 git 跟踪、页面又够不到的配置只有两处：`characters/<角色>/character.toml` 和 `characters/<角色>/windows-apps.toml` 的内置软件清单——这是当前架构最大的错位。

### 2.2 启动链（谁在什么时候读配置）

```
web_agent.main()                                    web_agent.py:3699
  └─ Store(data_dir) + 进程锁                        :3700-3701
  └─ Budget(store)                                  :3702   ← daily=10/monthly=300 的代码默认
  └─ load_character(config_root, character)         :3703   ← 读并校验 character.toml（失败=起不来）
       ├─ 读 persona 文件、快照版本                    veil_agent.py:839-848
       ├─ toml_provider()（内置模型价格/HTTPS/上限）     :854-887
       ├─ ModelSettings(models.json) + startup_choice  :888-898
       ├─ [tools]/[memory]/[autonomy]/[glance]/… 校验  :899-1155
       └─ workbench.json 覆盖 [executor]               :1131-1134
  └─ budget.autonomy_daily / glance_daily / limits  :3710-3713  ← 预算真值在此汇合
  └─ make_model / make_voice                        :3720-3722
  └─ EmailChannel.from_env / QQChannel.from_env     :3725-3727  ← 通道在启动时决定是否启用
  └─ ActivityState(resident-state.json)             :3728
  └─ Hub(cfg.name, activity, …)                     :3732
  └─ FilesystemTools → Memory(Brain) → Chat         :3750-3764
  └─ serve_chat(…, channels)                        :3764
```

关键含义：
- **任何 `character.toml` 字段非法，Peter 直接起不来**（`load_character` 抛 `UserError`，`veil_agent.py:1169-1174`）。这是保护，也是风险：页面级写入必须先复用同一套校验。
- **预算三层在此汇合**：代码默认（10/300）→ `models.json.limits`（30/800）→ 类目预算（`character.toml` 的 3/1/1）。三者都进同一个 `costs.jsonl`。
- **通道是否启用是启动时决定的**：`EmailChannel.from_env`/`QQChannel.from_env` 返回 `None` 就不启动；之后改 `.env` 必须重启。
- `--acceptance` 测试实例会强制 `daily=2/monthly=20` 并关闭记忆、语音、通道、窗口、桌面读写、瞄一眼、执行器（`web_agent.py:3704-3719`），且拒绝使用正式 config/data 目录（`:3695-3696`）。这是现成的「安全沙箱」范例。

### 2.3 写入链（页面 → 文件 → 运行时）

| 页面动作 | API | 写入文件 | 运行时如何生效 |
| --- | --- | --- | --- |
| 保存模型/接口/上限 | `POST /api/models` | `models.json` + `.env`（key） | `hub.save_models`：立即改 `chat.budget.daily/monthly`，必要时 `chat.switch_model`（`web_agent.py:1518-1539`） |
| 切换某用途模型 | `POST /api/models/use` | `models.json` | 聊天立即切换；其他用途下一次取用（`:1541-1555`） |
| 自主活动开关 | `POST /api/activity` | `resident-state.json` | `ActivityState.set` + 控制器立即读（`veil_agent.py:1468-1478`） |
| 安静时段开关 | `POST /api/quiet-hours` | `autonomy-quiet.json` | `autonomy.set_quiet_enabled`，立即（`autonomy.py:849`） |
| 瞄一眼开关 | `POST /api/glance` | `glance-settings.json` | `GlanceController.set_enabled`，立即 |
| Codex 监视 | `POST /api/codex-watch` | `codex-watch-settings.json` | `CodexWatchController.set_enabled`，立即 |
| 经历整理开关 | `POST /api/experience-memory` | `experience-memory.json` | `experience.set_enabled`，下一轮读（`experience_memory.py:133-145`） |
| 想起判官开关 | `POST /api/recall-judge` | `recall-judge.json` | 每轮读文件（`recall_judge.py:152-166`） |
| MCP 服务 | `POST /api/mcp` | `mcp-services.json` | 保存后刷新工具 schema（`web_agent.py:2836-2853`） |
| 工作台 | `POST /api/workbench` | `data/peter/workbench.json` | **重启**（`workbench_switch.py:2`，页面提示「重启后生效」） |
| 可操作软件 | `POST /api/windows-apps` | `windows-approved-apps.json` | 立即（`web_agent.py:3075-3091`） |
| 人设/背景/关于你/他记的你 | `POST /api/peter-page` | `persona/*.md`、`learned-context.json` | 人设 `reload_persona` 立即；背景/学习上下文下一句读取 |
| 指令文本 | `POST /api/instructions` | `persona/instructions/<id>.md` | 下一句（`PromptStore.text` 每次组装都读） |
| 说话样例 | `POST /api/voice-style` | `voice-style.json` + `persona/peter.voice.md` | 立即 |
| 外观 | 桌面壳 IPC | `localStorage` / `desktop.json` | 立即 |

**共同模式**：页面只写「本机文件」，从不改 `character.toml`；这是有意的（更新安全），也是为什么大量参数没有页面。

### 2.4 校验与失败语义

| 层 | 校验 | 失败后果 |
| --- | --- | --- |
| `character.toml` | 启动时全量类型/范围/路径校验（`veil_agent.py:832-1174`） | **进程起不来**，提示「角色配置无法读取或字段无效」 |
| `models.json` | `ModelSettings._clean` 全量校验 + 保存前校验 + 文件指纹并发检查（`model_settings.py:346-449,625-698`） | 保存被拒，返回中文原因；坏文件会阻止启动并保留原文件 |
| `workbench.json` | 字段白名单 + 类型（`veil_agent.py:814-829`） | 启动失败，提示删/修 workbench.json |
| `mcp-services.json` | id/命令/权限/revision 校验（`mcp_services.py:97-124`） | 保存被拒；revision 冲突要求刷新 |
| 指令覆盖 | `instructions.save` 按 registry 的 editable/max_bytes 校验 | 拒绝并提示 |
| 页面其他开关 | API 层 `isinstance(bool)` | 400，状态不变 |
| 预算 | `reserve` 前置检查 + 预留上限（`veil_agent.py:1537-1563`） | `BudgetExceeded`，请求**不发** |
| 账本 | 每次读校验；待核实即冻结所有付费请求（`ledger_recovery.is_unverified`） | 停止付费，提示跑 `data_repair.py ledger-status` |

**重要**：目前只有 `models.json` 有「保存事务（journal）+ 崩溃恢复」（`model_settings.py:298-326,677-698`）。其他 JSON 配置是「临时文件 + 原子替换」，没有跨文件事务。统一中心若要一次改多项，必须复用 `models.json` 的事务做法，或保证单项原子性。

### 2.5 持久化 / 更新 / 备份矩阵

| 配置面 | 重启后 | `update.bat` 后 | `update-main.cmd` 后 | 备份/恢复后 |
| --- | --- | --- | --- | --- |
| `character.toml` | 保留 | **若上游改了同文件则 pull 失败/冲突**；否则保留本地改动 | **被 stash 移出工作区，再对齐 origin/main**（`update-main.cmd:47,106`），改动只剩在 stash 里 | 恢复（在 `characters/` 内） |
| `data/<id>/*.json` | 保留 | 保留（`--exclude` 从不触碰 `data\`） | 保留（未跟踪文件进 stash，但 updater 只对 tracked 冲突 reset） | 恢复 |
| `.env` | 保留 | 保留 | 保留 | 恢复（**明文**，含 API Key） |
| `models.json` | 保留 | 保留 | 保留 | 恢复 |
| `persona/instructions/` | 保留 | 保留 | 保留 | 恢复 |
| 桌面 `desktop.json` | 保留 | 保留 | 保留 | 恢复（`desktop-data` 组件） |
| 渲染层 `localStorage` | 保留（同一系统用户） | 保留 | 保留 | **不恢复**（备份明确排除 Chromium 缓存/会话，`backup_restore.py:24-28`） |
| `brain.db` / `costs.jsonl` | 保留 | 保留 | 保留 | 恢复（含 digest 校验与未核实标记） |

（`update.bat` 的「不碰 data/persona」来自其头部注释：`update.bat:3`，pull 见 `:58`；`update-main.cmd` 的 stash 见 `:47`、`reset --hard` 见 `:106`。）

---

## 3. 正式实例实际生效值（只读快照）

> 采集时间 2026-11-25 20:30；来源 `veil-agent` 正式目录（f63e58a）。密钥只判断「已设置/未设置」，不读取内容；聊天/记忆正文不纳入。

### 3.1 模型与费用上限

| 项 | 实际值 | 来源 |
| --- | --- | --- |
| 聊天模型 | `gemini-4-hydrogen`（接口 topapi，按 token 计费） | `models.json use.peter=m41f634` |
| 自主活动模型 | `grok-4.6`（小鸡农场，¥0.04/¥0.12 每百万） | `use.autonomy=m3eeee7` |
| 记忆后台模型 | `builtin:deepseek`（`deepseek-flash`） | `use.background` |
| 瞄一眼模型 | `builtin:deepseek` | `use.glance` |
| 委派模型 | `builtin:deepseek` | `use.delegate` |
| 每天上限 / 每月上限 | **¥30 / ¥800** | `models.json limits`（代码默认是 10/300） |
| 接口数 / 自建模型数 | 4 / 5 | `models.json` |
| 密钥状态 | `DEEPSEEK_API_KEY`、`EMBED_API_KEY`、`MINIMAX_API_KEY`、4 个 `VEIL_KEY_*` 已设置；`OPENAI_API_KEY` 为空 | `.env` 键名检查 |
| 诊断页报错 | **「模型接口的钥匙没有配好」——误报**（钥匙在 `.env` 里已设置） | `diagnostics.py:415` 读 `os.environ` |

### 3.2 预算消耗（`costs.jsonl` 全量只读汇总）

| 项 | 值 |
| --- | --- |
| 账本跨度 | 2026-10-19 起，21,757 条预留 / 21,758 条结算，损坏行 0 |
| 今天（2026-11-25）已花 | **¥0.9472** / 30（3.2%） |
| 其中聊天 | ¥0.8059 |
| 其中经历整理 `experience_memory` | ¥0.0705（类目上限 ¥1/日） |
| 其中想起判官 `recall_judge` | ¥0.0455（**无类目上限**） |
| 其中自主活动 | ¥0.0126（类目上限 ¥3/日） |
| 其中瞄一眼 | ¥0.0126（类目上限 ¥1/日） |
| 本月已花 | **¥165.23** / 800（20.7%） |
| 待核对预留 | ¥0 |
| 今日按模型 | gemini-4-carbon ¥0.506、gemini-4-hydrogen ¥0.231、deepseek-flash ¥0.192、grok-4.6 ¥0.017、BGE-M3 ¥0 |

> 注：`gemini-4-carbon` 已不在当前 `models.json` 列表里，但今天仍有消费记录——模型被移除后账本仍保留历史，这是正常行为，页面应能按模型展示历史花费。

### 3.3 开关与运行态

| 开关 | 实际值 | 存储 | 覆盖谁 |
| --- | --- | --- | --- |
| 自主活动 | **关**（2026-11-25 05:41） | `resident-state.json` | — |
| 夜间安静 | **开** | `autonomy-quiet.json` | 覆盖 `autonomy.quiet_hours_enabled=true` |
| 自然醒来节奏 | `silent` 到 2026-11-23 07:02（已过期） | `autonomy-rhythm.json` | Peter 用工具自设 |
| 瞄一眼 | **开** | `glance-settings.json` | 覆盖 `[glance] enabled=true` |
| Codex 监视 | **关** | `codex-watch-settings.json` | — |
| 经历整理 | 未写开关文件 → **跟随 TOML `enabled=true`** | （缺省） | — |
| 想起判官 | 未写开关文件 → **跟随 TOML `enabled=true`** | （缺省） | — |
| MCP | **开**；4 个服务（github / xiaohongshu / nowhere / galatea_garden）全部 `direct` | `mcp-services.json` | — |
| 工作台 | **开**；项目文件夹空；镜像 `veil-workbench`；允许网站空 | `workbench.json` | 覆盖 `[executor]` |
| 可操作软件 | fastorange / wps / everything / cloudmusic | `windows-approved-apps.json` | — |
| 待批准卡片 | 0 条 | `pending-approvals.json`（24h TTL） | — |

### 3.4 文件侧限制（`character.toml`，只能改文件/重启）

| 项 | 实际值 | 代码默认 |
| --- | --- | --- |
| 历史轮数 | 80 | 20 |
| 输出上限 | 1024 | 1024 |
| 自主：间隔 / 每日次数 / 日预算 / 单次预算 / 步数 / 安静时段 | 90 分 / 12 次 / ¥3 / ¥0.25 / 60 / 01:00–06:00 | 180 / 6 / ¥2 / 无 / 无 / 无 |
| 瞄一眼：间隔 / 空闲 / 步数 / 日预算 | 15–40 分 / 300 秒 / 4 / ¥1 | 15–40 / 300 / 4 / ¥1 |
| 经历整理：触发字数 / 空闲 / 日预算 / 最近天数 | 3000 / 30 分 / ¥1 / 3 天 | 3000 / 30 / ¥1 / 3（但代码 `enabled` 默认 **false**，靠 TOML 打开） |
| 工具：步数 / 结果字节 / 输出上限 / 工具组 | 20 / 8192 / 4096 / 12 组 | 6 / 8192 / 1024 / 全组 |
| 工作台：时限 / 重复 / 单次预算 / 唤醒预算 / 维护时长 | 600 秒 / 3 / ¥0.5 / ¥0.3 / 2 小时 | 600 / 3 / ¥0.5 / ¥0.3 / 2 |
| 通道：QQ 轮询/空闲/日发 | 20 秒 / 120 秒 / 100 条 | 20 / 120 / 100 |
| 邮箱：轮询/日发 | 60 秒 / 20 封（**凭据缺失，实际未启动**） | 60 / 20 |
| 语音价格 | `MINIMAX_CNY_PER_10K_CHARS` 已设置 | 3.5 |

### 3.5 凭据与权限现状

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 只读 GitHub 研究 token | **未设置** | `VEIL_GITHUB_READONLY_TOKEN` 只从进程环境读；写进 `.env` 不生效（`github_readonly.py:288`） |
| 自我维护 GitHub token | **文件不存在** | 默认路径 `本机 secrets 目录的 github-token.txt`（`maintenance.py:173,591`；`veil_agent.py:2283`） |
| 维护实际凭据 | 推断依赖本机 `gh` 登录态 | token 文件缺失时 `_token_env()` 返回空（`maintenance.py:588-594`）；本机 `gh auth status` 为 yecays-ai，仓库声明的 token 机制并未生效 |
| 邮箱凭据 | 全部缺失 | `PETER_EMAIL_ADDRESS`/`PETER_EMAIL_AUTH_CODE`/`LAN_EMAIL_ADDRESS` |
| QQ 联系人 | 已配置 | `QQ_LAN_CONTACT` |
| 备份位置 | 已配置且在 D 盘（另一块盘） | `VEIL_BACKUP_DIR`；最近备份 2026-11-25 08:41 共 3 份可枚举 |
| 语音密钥 | 已配置 | `MINIMAX_API_KEY` |

### 3.6 死键 / 遗留键（本机与代码双向确认）

| 键 | 位置 | 证据 |
| --- | --- | --- |
| `[memory] dimensions` | `character.toml:42` | 全仓非测试代码零引用（仅测试 fixture 用） |
| `ombre_model` | `memory_agent.py:57-58` | `character.toml` 已无此键；只在无 Brain 权威的测试分支可达 |
| `[memory.brain] write_authority` | `memory_agent.py:114-119` | 生产 `backend=None` 恒为 True；该键只为旧测试保留 |
| `ombre_port` | `veil_agent.py:1062` | 仅 `pop` 兼容，无行为 |
| `JIUSHI_API_KEY` | 本机 `.env` | 全仓零引用（该接口用 `VEIL_KEY_EA0DCDA`） |
| `VEIL_CLAUDE_PATH` | 本机 `.env` | 全仓零引用（Claude Code 通道已在工单 317 下线） |
| `VEIL_OMBRE_STOP_PATH` | 原 `ombre_entry.py:89`、`ombre_host.py:54` | **已随 P3-4 #445 删除**（两个模块与 prompt 引用一并移除），全仓不再存在 |
| `ombre_entry.py` / `ombre_host.py` | 仓库根 | 审计期间（e7e7992）已无调用方；**#445 已删除** |

---

## 4. 分域清单与要点

> 每域给出：本域配置从哪来、最关键的结论、最该进统一页面的项。**全量 661 项见 `config-inventory.csv`**（列含义见 §9.2），逐项证据见 `evidence/` 下的八份分域底稿。

### 4.1 预算与费用（62 项：U6 / A18 / S3 / R17 / P18）

**配置来源三层**：`models.json limits`（页面可改）→ `character.toml` 类目预算（只能改文件）→ 代码常量（安全上限）。所有花费最终落进同一本 `data/costs.jsonl`。

- 全局日/月上限页面可改，保存即改内存预算（`web_agent.py:1518-1539`）；代码兜底 10/300（`model_settings.py:29-30`、`veil_agent.py:1328`）。
- 类目上限：自主 ¥3、瞄一眼 ¥1、经历整理 ¥1（含事件合并共用）；判官、委派、日历、短期便签、工作台**没有类目上限**，只受全局约束。
- 执行顺序：类目上限 →（可选 category_daily）→ 全局日/月 + 待核对预留（`veil_agent.py:1544-1553`）。预留金额取「最大输入×输入价 + 最大输出×输出价」，是单次请求的硬上限。
- 临时提额：只对工作台单次任务、上限 ¥50、审批卡同意后只加在内存（重启清零），同时抬高日/月上限（`veil_agent.py:5019`、`:1354-1358`）。
- 账本待核实会冻结**所有**付费请求，解除只能命令行 `data_repair.py ledger-verify`（`veil_agent.py:1365-1369`）。
- 页面只有一个「费用上限」输入框；没有预算仪表盘、没有类目、没有提额状态、没有冻结横幅。

**最该进页面的 6 项**

| 项 | 现状 | 建议 |
| --- | --- | --- |
| 日/月上限 | 页面可改 | 保留（U） |
| 自主/瞄一眼/经历整理日预算 | 仅 TOML | 预算页可改（A，需 Lan 同意） |
| 单次/唤醒预算（自主+工作台） | 仅 TOML | 预算页可改（A） |
| 已花/预留/剩余/按类目/按模型 | 只有 `/cost` 文本 | 预算仪表盘（R） |
| 今日临时提额 | 不可见 | 显示额度与到期（R） |
| 账本冻结状态 | 不可见 | 顶部横幅 + 修复入口（R） |

### 4.2 自主活动与后台（84 项：U6 / A29 / R8 / P41）

- 总开关存 `resident-state.json`，**缺文件默认暂停**（`veil_agent.py:1189-1190`）；当前正式实例是关。
- 频率/次数/预算/安静时段/单次上限/步数全在 `[autonomy]`，重启生效；`max_tool_steps` **只在 `run_budget_cny` 存在时才生效**（`veil_agent.py:1039-1041`），单写它是死键，会退回 `[tools].max_steps`。
- 安静时段有「窗口」和「开关」两层：窗口在 TOML，开关在 `autonomy-quiet.json`；窗口为空时开关无效（`autonomy.py:836-851`）。
- 瞄一眼：间隔/空闲/步数/日预算在 TOML，开关在 `glance-settings.json`；`max_steps>4` 启动直接失败（`veil_agent.py:1051-1052`）。
- Codex 监视：缺设置文件默认**开**（`codex_watch.py:111-119`），不占自主活动开关，却消耗瞄一眼日预算（`codex_watch.py:356-359,417-419`）。
- 「暂停自主活动」并不等于全停：`reply_later` 到期唤醒（`veil_agent.py:3280-3294`）和每日待办提醒（`web_agent.py:1381-1393`）不查活动开关。
- 通道轮询（QQ 20 秒 / 邮箱 60 秒）与每日发送上限在 TOML，且通道在启动时决定是否启用。

**最该进页面的 6 项**：自主间隔、每日次数、自主日预算、安静时段窗口、瞄一眼间隔/预算、Codex 监视间隔（现在页面写死「10 秒」）。

### 4.3 记忆系统（91 项：U3 / A47 / S11 / R7 / P17 / X6）

- 两层结构最明显：页面开关（`experience-memory.json`、`recall-judge.json`）覆盖 TOML 默认（`experience_memory.py:133-145`、`recall_judge.py:151-166`），页面写过一次后 TOML 的 `enabled` 永远不再生效。
- 只有两个记忆开关有页面：经历整理、想起判官。召回冷却/去重/摘要、联想阈值、判官候选与熔断、事件归组/故事上限、检索权重、关系底座上限**全部只能改文件**。
- 经历整理与事件合并**共用**每天 ¥1 的额度（`experience_memory.py:128,177`），关掉经历整理不会关事件合并。
- 召回预算不是固定字符数：它是「provider 输入上限减去必需块」后的比例分配（`recall_budget.py`），所以调 `max_input_tokens` 会间接改变能想起多少条。
- 死键：`dimensions`（代码写死 1024）、`ombre_model`、`[memory.brain] write_authority`（仅测试）、`random_chance`（只当 0=关的开关用）。
- 语义召回会静默降级：缺 `EMBED_API_KEY` 或模型字段时退成纯 BM25，不报错只「回忆变差」（`memory_agent.py:1064-1146`）。
- 未文档化的键：`[memory.events]` 的 evidence_items / evidence_chars / search_query_chars / investigate_rows；`[memory.recall]` 的 directory / directory_members / stage_slots。

**最该进页面的 6 项**：整理触发阈值与日预算、召回冷却/去重、判官候选与熔断、联想阈值、关系底座上限、事件故事上限。

### 4.4 模型与上下文（72 项：U20 / A20 / S3 / R9 / P19 / X1）

- 模型三层：`[providers.*]`（内置，TOML）→ `models.json endpoints/models`（页面）→ `use.{peter,autonomy,background,glance,delegate}`（页面）。内置模型的价格/输入上限页面改不了。
- 五个用途的生效时间不同：聊天立即、记忆后台重启、自主/瞄一眼/委派各自下一次任务。
- 自主活动「跟随聊天」时会把 provider 的输入窗口替换成 `autonomy.max_input_tokens`（`veil_agent.py:4015-4022`）；如果聊天模型本身只有 32768，这会把请求抬到 100000 再发给只支持 32768 的接口。
- 页面保存模型会丢 `reasoning_field`（`chat.js:2821` 不回传，`model_settings.py:650-661` 只保留 input_modalities）；保存不带 `limits` 会把预算重置成 10/300（`:662-663`）。
- 上下文管理：`history_rounds=80` + 按 provider 输入上限做字节→token 估算裁剪 + 最新图片折叠；**没有摘要式压缩**。`max_retries=0`、请求超时 180 秒（`veil_agent.py:6426,6452`），没有自动降级模型。
- 指令：99 条注册表（约 60 条可编辑），默认在仓库、覆盖在 `persona/instructions/`，整条覆盖、无版本回滚；单条上限 8000 字节（个别 3000–30000），文件总上限 40000（`prompts/store.py:20`）。

**最该进页面的 6 项**：内置模型价格、历史轮数、输出上限、工具输出上限、请求超时、按用途的模型就绪状态。

### 4.5 工具与权限（115 项：U11 / A17 / S20 / R8 / P54 / X5）

- 权限四层：代码策略 `tool_policy()`（allow/ask/deny/workspace/lab/web/desktop_*，`tools_agent.py:92-109`）→ 功能开关（是否注册工具）→ 持久授权（`append-grant.json`、MCP `ask/direct`、执行器任务级 fingerprint）→ 调用时审批。
- `capabilities.json` 不是可编辑清单：每次启动按策略重算并覆盖（`tools_agent.py:455-546`），手改无效；它写失败会直接停掉全部文件工具。
- `[tools].enabled=false` **关不掉** Windows 观测/桌面读写/screen_pop：这些工具在 enabled 判断之前就装配进列表（`tools_agent.py:250-326`），最终仍会出现在请求里。
- `max_result_bytes` 会被静默钳制：Windows ≤8192（`windows_interaction.py:1040`）、小红书 ≤8192（`xiaohongshu_tools.py:364`）；web_research 最低 12288。
- MCP 有两个开关：MCP 页的 `enabled` 是运行时开关；`[web].github_readonly/xiaohongshu_readonly` 只决定适配器是否注册，且装配时被强制置 True（`tools_agent.py:242-247`）。GitHub 适配器关掉后 MCP 页仍可能显示 connected（`mcp_services.py:853-860`）。
- 审批有效期两套：对话内 600 秒（`web_agent.py:111`）、持久待批准 24 小时（`pending_approvals.py:13`）；`KEEP_DAYS=7` 是死常量。
- 工具清单预算（always_on 36000 / tool 1500 / group 12000）只在测试里校验，运行时不受约束。

**最该进页面的 6 项**：工具权限矩阵（只读）、MCP 服务权限、持续追加授权撤销、软件白名单、工具步数/结果上限、待批准列表。

### 4.6 工作台、GitHub 与个人项目（73 项：U7 / A14 / S7 / R5 / P39 / X1）

- 页面只覆盖 4 个键：`enabled`、`project_roots`、`container_image`、`web_hosts`（`data/peter/workbench.json`）；`[executor]` 的 12 个键（时限 600 秒、重复 3、单次 ¥0.5、唤醒 ¥0.3、维护仓库、工作树、GitHub 仓库、token 文件、维护时长、镜像、命令、项目根）**全部只能改文件**。
- 页面「添加项目文件夹」会拒绝包含 `data/characters/persona` 的路径（`workbench_switch.py:46-60`）；但 `workbench_switch.py:12` 硬编码 `CHARACTER='peter'`，多角色会串。
- `web_hosts` 只在容器模式且非维护任务时生效（`veil_agent.py:5127`、`executor_opencode.py:1171-1175`），宿主模式静默忽略。
- GitHub 三套权限互不相通：只读研究 token（`os.environ`，`.env` 无效）、自我维护 token 文件（当前不存在）、MCP github（direct）。`[web] github_repository` 只拼提示，不限制可读仓库（`github_readonly.py:90-93`）。
- 维护任务强制容器（`executor_opencode.py:1165` 起），且验收实例会**复制真实 `.env`** 到 `data/peter/maintenance/acceptance-*`（`maintenance.py:481-483`）——崩溃可能残留全部 API Key。
- 工作台花费计入全局账本，按 `run_budget_cny` 预留、按实际用量结算（`veil_agent.py:5079-5092`）；非自主任务在类目上算「聊天」。

**最该进页面的 6 项**：工作台预算/时限、维护仓库与分支策略、GitHub token 就绪状态、允许网站、项目根、镜像状态。

### 4.7 交互与个性化（61 项：U26 / A7 / S4 / R8 / P16）

- 外观分两处：`desktop.json`（窗口、背景图，进备份）与渲染层 `localStorage`（主题、透明度、Peter 大小、自定义 CSS、侧栏、最近表情，**不进备份**）；主题在页面和菜单窗还是两个来源。
- 人格文本有四种上限：人设文件**没有字节上限**（`web_agent.py:623-632` 只校验非空）、关于你 4000、Learned Context 4000、说话样例 8000；人设过长会挤占上下文。
- 语音：`[voice]` 只有 provider；`.env` 的 5 个 MiniMax 键缺一个就整体禁用，页面无提示；价格 `MINIMAX_CNY_PER_10K_CHARS` 在 `.env`。
- 通道：QQ/邮箱 `enabled=true` 但凭据缺失时静默不启动；QQ 有 `QQ_LAN_CONTACT` 可用，邮箱三个键全缺。
- 日历/生活记录：提醒提前量（准点/15/30 分钟）在页面可选，聊天约定自动记日历有开关（`/api/life`）；心情与记录存 `life-*.json`。
- 死入口：菜单 `size` 分支不可达（`main.cjs:802` vs `menu.html`）。

**最该进页面的 6 项**：外观统一存储（进备份）、人设字数上限提示、语音就绪状态、QQ/邮箱就绪状态、日历提醒默认值、桌面通知开关。

### 4.8 系统安全、维护与诊断（103 项：U3 / A7 / S13 / R39 / P38 / X3）

- 备份：`VEIL_BACKUP_DIR`（本机在 D 盘）、冷备份覆盖 `data/characters/persona/.env` + `desktop.json`；**没有保留策略**，`usage` 只统计不删除（`backup_restore.py:923-944`），备份盘会一直涨。
- 两个更新器安全级别不同：`update.bat` 先冷备份再 `git pull --ff-only`（`:50-58`）；`update-main.cmd` 只 `git stash`（`:47`）后对齐 `origin/main`（`:106`）。诊断页的更新提示只指向后者（`diagnostics.py:1134-1135`）。
- 诊断：快照 300 秒缓存、问题上限 200 → overview 60 → 页面 30；报告窗口写死 72 小时；配置检查误报钥匙（F-01）。
- 停止：`/api/stop` 停当前回合、`/api/exit` 停服务、活动开关暂停自主；没有全局「紧急停止一切」按钮。
- 日志：`.gitignore` 忽略 `logs/`、`*.log`，代码里没有 `diagnostic.log` 的写入点；失败记录在 `tool-errors.jsonl`、`runtime-status.json`、`data/peter/memory/_agent/process.log` 等处。
- 维护：任务状态在 `data/peter/maintenance/`，阶段/合并/部署均有状态机；token 缺失时只给一句提示（`maintenance.py:947-955`）。

**最该进页面的 6 项**：备份状态与保留、诊断总览（只读）、冻结/异常横幅、审批历史、更新通道选择、维护任务状态。

---

## 5. 全局架构问题清单（按影响排序）

> 每条都给「现象 / 证据 / 影响 / 建议」。分类码见 §6。

### F-01 诊断页把「钥匙已配置」误报成「没配」（页面值 ≠ 实际值）

- **现象**：诊断页「配置校验」长期显示红字「模型接口的钥匙没有配好：环境变量 VEIL_KEY_EEC29EA 是空的（模型 gemini-4-hydrogen）」，但该键在 `.env` 里已设置，聊天也正常花钱。
- **证据**：`diagnostics.py:415` 用 `os.environ.get(env_name)` 判断；而项目从不把 `.env` 注入 `os.environ`，模型密钥只由 `model_settings._env_values()` 读 `dotenv_values`（`model_settings.py:574-579`）。正式实例 `data/peter/diagnostics-snapshot.json` 里这条 error 仍在。
- **影响**：Lan 会以为模型坏了去改 `.env`；开发 Agent 可能被误导去排查不存在的故障。这是「统一管理中心」必须先修的可信度问题。
- **建议**：与 `_key_available` 共用同一个「读 `.env` 判存在」的函数；并把这条检查升级为「已设置 / 未设置 / 只在内置档使用」三态。

### F-02 两个 GitHub 权限体系，页面都看不见，而且现在都没真正配好

- **事实 A（只读研究）**：`github_readonly.py:24` 的 `TOKEN_ENV='VEIL_GITHUB_READONLY_TOKEN'` 只在 `os.environ` 里读（`:288-290`）；写进 `.env` **不生效**，`.env.example` 和 README 都没有这个键。正式实例未设置 → 匿名访问，`search_code` 不会走带 token 的路径。
- **事实 B（自我维护）**：`maintenance.py:173` 的 `TOKEN_FILE_NAME='github-token.txt'`，默认路径 `本机 secrets 目录的 github-token.txt`（`veil_agent.py:2283`）；正式机器上**该文件不存在**。此时 `_token_env()` 返回空（`maintenance.py:588-594`），`gh` 退回到机器登录态（本机 `gh` 当前是 yecays-ai）——权限实际来自 shell 登录，不是仓库声明的 token。
- **事实 C（第三套）**：MCP `github` 服务在 `mcp-services.json` 里是 `direct`，与上面两套都无关，页面 MCP 卡片可见、但看不出它和「只读研究」「自我维护」的关系。
- **影响**：Lan 无法判断「Peter 现在能不能推代码、以谁的身份、能读写哪些仓库」；换机器或 `gh` 登出后行为会静默改变；审计时无法复现权限。
- **建议**：并成一张权限表（研究只读 / 自我维护 / MCP 三个用途），每行显示「凭据来源、是否已就绪、范围、最后验证时间」；研究 token 改读 `.env`；维护 token 缺失时在页面显式标红并禁止 PR 操作，而不是退回机器登录态（或至少显示「正在使用机器 gh 登录态」）。

### F-03 `character.toml` 是 git 跟踪文件：页面永远够不到，手改又会被更新流程破坏

- **证据**：137 个叶子键里，页面能间接影响的只有模型相关（读 `models.json` 覆盖）与少量开关文件；其余 90 个功能键只能改 `characters/peter/character.toml`。该文件被 git 跟踪（`git ls-files` 可见），同目录的 `windows-apps.toml` 内置软件清单也一样只能改文件。`update.bat` 用 `git pull --ff-only`（`:58`）、`update-main.cmd` 先 `git stash`（`:47`）再 `reset --hard`（`:106`）。
- **影响**：Lan 想调「自主活动一天几次」必须让开发 Agent 改 tracked 文件；上游改到同一文件时会更新冲突，走 `update-main.cmd` 时改动从工作区消失，且每次都要重启。
- **建议**：新增 `data/<id>/settings.json` 覆盖层（§7）；`character.toml` 降级为「出厂基线」，页面显示「跟随基线 / 已被本机覆盖」。

### F-04 同一个开关有两层来源，页面只显示一层

- **证据**：自主活动（`resident-state.json` vs `[autonomy]`）、瞄一眼（`glance-settings.json` vs `[glance] enabled`）、安静时段（`autonomy-quiet.json` vs `quiet_hours_enabled`）、经历整理（`experience-memory.json` vs `[memory.experience] enabled`）、想起判官（`recall-judge.json` vs `[memory.recall_judge] enabled`）、Codex（`codex-watch-settings.json`）、工作台（`workbench.json` vs `[executor]`，本地覆盖 TOML，`veil_agent.py:1131-1134`）。
- **影响**：Lan 在页面关掉、又在 TOML 打开时，看到的是页面值，但实际可能是文件里的旧值；排查时无法回答「为什么它还醒着」。
- **建议**：统一中心显示「生效值 + 来源层 + 另一层的值」；提供「让两边一致」的一键操作。

### F-05 预算的中心可见性不足

- **证据**：页面只提供全局「每天/每月上限」（`chat.html:199`）；类目预算（自主 ¥3、瞄一眼 ¥1、经历整理 ¥1）只在 `character.toml`；单次上限（工作台 ¥0.5/¥0.3）在 `[executor]`；临时提额只在内存（`veil_agent.py:1343-1352`），页面看不到；`/cost` 是聊天命令，不是仪表盘。
- **影响**：Lan 只能靠问 Peter「今天花了多少」或输入 `/cost`；无法知道「剩余多少」「哪类超了」「提额还剩多少」。
- **建议**：预算页展示 §3.2 的全部数字 + 类目层级 + 提额状态；编辑入口分级（U：日/月上限；A：类目预算，需 Lan 同意）。

### F-06 「启用」不等于「就绪」：三个真实例子

1. `[email] enabled=true`，但 `PETER_EMAIL_ADDRESS`/`PETER_EMAIL_AUTH_CODE`/`LAN_EMAIL_ADDRESS` 全缺失 → `EmailChannel.from_env` 返回 `None`，通道静默不启动（`email_channel.py:29,103`）。
2. `[providers.gpt]` 存在但 `OPENAI_API_KEY` 为空；页面模型列表把它列为内置模型，不提示不可用（`model_settings.py:591-595` 只显示 `key_set`）。
3. 瞄一眼未显式选择时：若已配置 grok-4.6 会自动选它，否则跟随聊天（`model_settings.py:490-503`）——页面提示只有「只用于瞄一眼」，没说明这个自动选择。
- **建议**：每个功能开关加「就绪检查」：缺什么、影响什么、怎么修；页面三态显示「开且就绪 / 开但缺条件 / 关」。

### F-07 死键与遗留配置（清理清单）

见 §3.6。补充两条：
- `[memory.recall] random_chance` 仍被解析并校验范围（`recall_rotation.py:29,75`），但语义已退化为「0 = 关闭自然联想」的开关（`natural_association.py:49`），不再是概率。注释里已说明，但键名会误导。
- `ombre_entry.py`/`ombre_host.py` 已在审计期间由 P3-4 #445 删除（`prompts/inline.py` 的引用一并移除）；因此 `VEIL_OMBRE_STOP_PATH` 也不再存在。
- **建议**：死键进「冗余/废弃」列表，页面不展示；清理单独开单、需 Lan 同意（涉及删配置与代码）。

### F-08 多角色 / 测试实例的隔离缺陷

- **证据**：`workbench_switch.py:12` 硬编码 `CHARACTER='peter'`，`settings_path()` 永远写 `data/peter/workbench.json`；`web_agent.py:3097,3111` 的 `/api/workbench` 用 `ROOT` 而不是 `config_root`；`veil_agent.py:5032` 的 `_run_workbench` 读 `ROOT/'.env'` 而不是本实例 config root。
- **影响**：未来 Ghost 或验收实例打开设置页，会读写 Peter 的工作台配置；`--acceptance` 已禁用 executor，所以当前不泄漏，但这是隐患。
- **建议**：所有路径参数化（`root`/`character`），把「实例根」作为一等参数；验收测试覆盖「两个角色各自的工作台互不影响」。

### F-09 大多数 TOML 段不拒绝未知键，拼错静默无效

- **证据**：只有 `[memory.experience]` 显式拒绝未知键（`experience_memory.py:85-90`）；`[autonomy]`、`[glance]`、`[memory]`、`[tools]`、`[web]` 都是 `dict(...)` + 逐键 `get`，多写的键被静默忽略（`veil_agent.py:1016-1151`）。
- **影响**：Lan 或开发 Agent 拼错一个键，看起来「配了」，实际没生效——这是最典型的「配置无实际作用」。
- **建议**：统一注册表后，启动时对未知键发出明确警告（或拒绝），并在诊断页列出。

### F-10 启动即失败、没有 dry-run / 安全模式

- **证据**：`load_character` 任一字段非法 → `UserError` → 进程退出（`veil_agent.py:1169-1174`；`web_agent.py:3787-3794` 只写 `web_service_error.txt`）。
- **影响**：一个手滑的值就让 Peter 起不来，Lan 只能让别人改文件。
- **建议**：页面写入前用同一校验函数 dry-run；启动时对「可回退的覆盖层」失败可降级到基线并告警（TOML 本身仍严格失败）。

### F-11 权限其实是三层，页面只看得到一层

| 层 | 机制 | 位置 | 页面可见 |
| --- | --- | --- | --- |
| 1 代码策略 | `tool_policy()`：allow / ask / deny / workspace / lab / web / desktop_* | `tools_agent.py:92-109` | 否（只有能力记录文字） |
| 2 服务权限 | MCP 服务的 `ask/direct` | `mcp-services.json`、`mcp_services.py:661-669` | 是（MCP 页） |
| 3 持久授权 | `append-grant.json`（文件身份级、重启仍有效）、`pending-approvals.json`（24h TTL）、执行器任务级 approval fingerprint | `tools_agent.py:1711-1774`、`pending_approvals.py:12-13`、`executor_opencode.py:130-148` | 否 |

- **影响**：Lan 无法在一处回答「Peter 现在被允许做什么、谁批的、什么时候过期」。
- **建议**：权限中心把三层合并展示；第 1 层只读（P），第 2/3 层可撤销（S）。

### F-12 类目预算的执行面不完整

- **证据**：`_enforce_category_budget` 只处理 `autonomy`/`glance`（`veil_agent.py:1448-1457`）；`experience_memory`/`event_memory` 通过 `category_daily` 参数自查（`experience_memory.py:552`、`event_memory.py:816-817`）；`recall_judge`、`delegate`、`calendar_agreements`、`short_term_notes` 只有全局上限，没有类目上限。
- **影响**：判官/委派/日历判断可在全局额度内无限增长；Lan 以为「每类都有上限」。
- **建议**：预算注册表统一声明「是否有类目上限」，页面明确标注「仅受全局限制」。

### F-13 预算耗尽与冻结的用户提示不够显眼

- **证据**：类目超限时错误只回到当次请求（「自主活动已达到 ¥3/日上限，聊天不受影响」，`veil_agent.py:1457`）；全局超限提示「输入 /cost 查看」（`:1553`）；账本待核实会冻结**所有**付费请求（`:1365-1369`），但页面没有横幅，只有在聊天里触发时才知道。
- **影响**：Lan 可能在不知情时发现 Peter「不说话了」。
- **建议**：总览页固定显示预算状态与冻结状态；冻结时页面顶部红色横幅 + 修复入口。

### F-14 没有配置变更审计与回滚

- **证据**：页面写入只覆盖文件，没有记录「谁、何时、旧值→新值、是否生效」。`models.json` 有 journal，但只用于崩溃恢复，不保留历史。
- **影响**：改错无法一键回退；排查「为什么行为变了」没有线索。
- **建议**：`data/<id>/config-changes.jsonl`（append-only，含旧值/新值/来源/操作者/生效状态），页面提供「撤销上一次」。

### F-15 内置模型的价格与输入上限不可页面修改

- **证据**：`[providers.deepseek] / [providers.gpt]` 的价格、`max_input_tokens`、`thinking` 在 TOML（`character.toml:12-34`）；页面只把内置模型列出来（`model_settings.py:591-595`），表单只能编辑自建模型。
- **影响**：DeepSeek 官方调价后，必须改 tracked 文件；若默认模型被设成内置，页面无法调整价格，预算估算会偏。
- **建议**：内置模型也进注册表，价格可从页面覆盖（写 overlay），TOML 保留出厂价。

### F-16 界面偏好有一半不在备份里

- **证据**：`backup_restore.py:24-28` 明确只备份 `desktop.json` + 背景图；主题/透明度/Peter 大小/自定义 CSS 在渲染层 `localStorage`（`chat.js:2129-2137,2504-2506`）。
- **影响**：换机、清缓存或换 Windows 用户后外观丢失；Lan 会以为是程序坏了。
- **建议**：把外观统一进 `desktop.json`（主进程可写），或纳入备份；统一中心把「界面偏好」列为一等配置。

### F-17 文档/注释与实现的小偏差（会误导后续施工）

- `chat.html:92` 把 Codex 监视「检查间隔 10 秒」写死在 HTML；代码是 `POLL_SECONDS=10`（`codex_watch.py:16`），今天一致，但改代码不会改页面。
- `[memory.recall] random_chance` 名为概率，实际是开关（见 F-07）。
- `workbench_switch.py` 文档说「重启后生效」，页面也确实提示；但 `/api/workbench` 的 add/remove 立即写文件，容易让人以为立即生效。
- **建议**：这些文案在统一中心里由注册表的 `apply` 字段生成，不再手写。

### F-18 临时提额的语义与预期不符（需 Lan 拍板）

- **证据**：只对工作台单次任务询问（`veil_agent.py:4994-5021`），上限 ¥50（`:1324`），只提升当天/当月上限，重启清零（`:1343-1352`）。
- **影响**：如果 Lan 的预期是「任何预算超了都能申请」，现在只有工作台能；如果预期是「提额要留痕」，现在页面看不到。
- **建议**：先确认预期；最小改动是把「今日已提额」显示出来，持久化与否由 Lan 定。

---

## 6. 分类汇总与清理清单

### 6.1 全量分布（661 项）

| 域 | 合计 | U 用户常用 | A 高级 | S 敏感权限 | R 只读运行信息 | P 程序安全约束 | X 冗余废弃 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 预算与费用 | 62 | 6 | 18 | 3 | 17 | 18 | 0 |
| 自主与后台 | 84 | 6 | 29 | 0 | 8 | 41 | 0 |
| 记忆系统 | 91 | 3 | 47 | 11 | 7 | 17 | 6 |
| 模型与上下文 | 72 | 20 | 20 | 3 | 9 | 19 | 1 |
| 工具与权限 | 115 | 11 | 17 | 20 | 8 | 54 | 5 |
| 工作台/GitHub | 73 | 7 | 14 | 7 | 5 | 39 | 1 |
| 交互与个性化 | 61 | 26 | 7 | 4 | 8 | 16 | 0 |
| 安全/维护/诊断 | 103 | 3 | 7 | 13 | 39 | 38 | 3 |
| **合计** | **661** | **82** | **159** | **61** | **101** | **242** | **16** |

读法：
- **U 82 项**是页面可以直接改的候选；**A 159 项**是「改之前要说明影响」的；**S 61 项**必须二次确认/审批。
- **P 242 项**占 37%，说明「系统里大部分数字是程序约束，不是用户偏好」——统一中心不能把它们做成可编辑，否则等于拆掉安全边界。
- **R 101 项**适合展示（预算、状态、诊断、版本）。
- **X 16 项**见 6.3，清理需 Lan 同意。

### 6.2 六类的页面处理规则

| 分类 | 页面呈现 | 修改方式 | 例子 |
| --- | --- | --- | --- |
| U 用户常用 | 默认展开，直接改 | 即时保存 + 回显生效值 | 日/月上限、聊天模型、自主开关、瞄一眼开关 |
| A 高级 | 折叠 + 「影响说明 + 生效时机」 | 先展开说明再保存 | 自主间隔、召回冷却、判官候选数、历史轮数 |
| S 敏感权限 | 权限页 + 二次确认 + 审计 | 确认对话框/审批卡 | MCP 服务权限、持续追加授权、项目根、token |
| R 只读运行信息 | 总览卡片/表格 | 不可改 | 已花/预留/剩余、预算提额、模块状态、版本 |
| P 程序安全约束 | 只读并注明「程序保证」 | 不可改（改代码需 Lan 同意） | 路径包含、容器网络锁、单次提额 50、审批 TTL |
| X 冗余废弃 | 只在「清理清单」出现 | 后续工单删除 | `dimensions`、`ombre_model`、死常量 |

### 6.3 冗余/废弃配置（16 项，清理依据与影响）

| ID | 位置 | 清理依据 | 删除影响 | 建议 |
| --- | --- | --- | --- | --- |
| MEM-002 | `[memory] mode`（`character.toml:39`） | 只允许 `native`，其他值启动即报错（`veil_agent.py:947-950`） | 无行为变化；旧配置需同步删键 | 删键，代码改为不读 |
| MEM-005 | `[memory] dimensions`（`:42`） | 全仓零引用，维度写死 1024（`brain_retrieval.py:103`） | 无；改它本来就不生效 | 删键 |
| MEM-007 | `ombre_port`（`veil_agent.py:1062`） | 仅 `pop` 兼容，无行为 | 无 | 删兼容代码 |
| MEM-008 | `ombre_model`（`memory_agent.py:57-58,362`） | 只在无 Brain authority 的旧分支可达，生产不可达 | 无 | 删函数与调用 |
| MEM-012 | `[memory.retrieval] backend` | 生产被强制 `brain`（`memory_agent.py:185-192`） | 无 | 删键 |
| MEM-018 | `[memory.recall] random_chance` | 只作为「0=关联想」使用，不再当概率（`natural_association.py:49`） | 需保留 0 的兼容语义 | 改名为 `association_enabled` 后删旧键 |
| MOD-009 | `kind="claude_agent"`（`veil_agent.py:855-863`） | 工单 317 已下线 Claude Code | 旧配置会被当作不存在 | 删分支与迁移记录 |
| TOO-021 | `SOCIAL_PARTS`（`tool_groups.py:71-73`） | 兼容旧分组 | 旧配置分组提示丢失 | 删 |
| TOO-023 | `BUDGET`（`:81`） | 运行时不校验，仅测试检查 | 测试需同步 | 保留常量但补运行时校验，或删测试 |
| TOO-024 | `OVERSIZE_EXEMPT`（`:82-83`） | 注释写明不再加成员 | 只影响测试 | 删或冻结 |
| TOO-026 | `REGROUP_FLAG`（`:84`） | 只影响一次性提示 | 老用户少一条提示 | 删 |
| TOO-040 | `KEEP_DAYS=7`（`pending_approvals.py:12`） | 全仓未使用 | 无 | 删常量 |
| WRK-036 | `[web] github_repository` | 只拼进提示，不限制可读仓库（`github_readonly.py:90-93`） | 提示里少了仓库名 | 要么真正限制，要么删键 |
| SAF-018 | `DIAGNOSTIC_HOURS`（`web_agent.py:2935`） | 定义后未使用 | 无 | 删或接到「报告窗口」 |
| SAF-103 | `diagnostic.log` | 全仓零引用 | 无 | 删残留引用 |
| SAF-104 | `logs/` 目录 | 无写入点，仅被 gitignore | 无 | 删 ignore 或补日志实现 |

> 清理原则：**一次只删一类**，每类单独工单 + 迁移说明；删除前跑全量测试；`character.toml` 的死键删除要同步所有本机副本（正式目录与测试实例）。

### 6.4 「不是废弃、但现在是隐性/陷阱」的配置（需在页面显性化）

| 项 | 问题 | 处理 |
| --- | --- | --- |
| `[autonomy] max_tool_steps` | 只在 `run_budget_cny` 存在时生效 | 注册表声明依赖，页面禁用/提示 |
| `[glance] max_steps` | >4 启动即失败 | 注册表上限 4 |
| `[tools] max_result_bytes` | Windows/小红书被夹到 8192 | 页面标注「实际上限」 |
| `[tools] enabled` | 关不掉 Windows/桌面/screen_pop | 修装配顺序（工单 446/450） |
| `[providers.*] thinking` | 写 disable/off 静默失效 | 校验枚举 |
| `[providers.*] cny_per_call` | 支持但文档/页面没有 | 页面暴露或删支持 |
| `models.json reasoning_field` | 页面保存即丢失 | 保存时沿用旧值 |
| `[memory.recall] directory/stage_slots` 等 | 代码读取但未文档化 | 注册表补全 |
| `[memory.events] evidence_*` 等 | 同上 | 注册表补全 |
| `[profile.relationship_base]` | 只能写在 `[profile]` 内，顶层会被忽略 | 校验/提示 |
| `[email] enabled` / `[voice]` | 开但缺凭据时静默不工作 | 就绪三态 |
| `workbench.json container_image` | 页面一保存就写默认镜像 → 进容器模式 | 页面显式选择模式 |

---

## 7. 统一配置管理中心：架构建议

### 7.1 目标与现状差距

Lan 的目标是「不读代码、不找开发 Agent，也能看清并调整 VEIL 的行为」。现状的差距不是「缺一个页面」，而是缺四样东西：

1. **没有单一真源**：同一个开关有 TOML 默认 + `data/*.json` 覆盖两层，页面只显示一层。
2. **没有注册表**：每个配置项的默认值、范围、生效时机、影响面散落在各自的 `load_*` 函数里，页面只能逐个手写。
3. **没有变更审计**：改了哪个值、谁改的、从什么变成什么、是否已生效，都没有记录，无法回滚。
4. **没有「生效值」概念**：页面显示的是「文件里的值」，不是「运行时正在用的值」。

现成可复用的资产（不要另造）：
- `prompts/registry.py` + `prompts/store.py`：**配置注册表 + 默认值 + 覆盖文件 + 指纹 + 页面列表**的完整范式（99 条指令已经在用）。
- `model_settings.ModelSettings`：**跨文件事务（models.json + .env）+ journal + 崩溃恢复 + 并发指纹**的写入范式。
- `diagnostics.py`：**配置问题清单**的展示范式（但校验源要修）。
- `pending_approvals.py`：**持久审批 + TTL** 的授权范式。
- `ActivityState` 等开关文件：**立即生效的轻量覆盖**范式。
- `workbench_switch`：**页面只写本机文件、不碰 tracked 文件**的更新安全范式。

### 7.2 数据模型：三层 + 一个生效值

```
第 0 层  代码默认      config_registry.Item.default        （单一真源，随代码）
第 1 层  角色基线      characters/<id>/character.toml      （只读展示；不鼓励手改）
第 2 层  本机覆盖      data/<id>/settings.json             （页面写，gitignored，随备份）
第 3 层  运行态覆盖    data/<id>/resident-state.json 等     （开关；页面写，立即）
─────────────────────────────────────────────────────────
生效值 = merge(0,1,2,3)      → 页面必须同时显示「生效值 + 来源层」
进程值 = Hub.chat.cfg.*      → 页面显示「与生效值是否一致（待重启）」
```

**为什么不直接把 `character.toml` 改成可写**：
- 它是 git 跟踪文件，页面写它会让 `update.bat`/`update-main.cmd` 冲突或丢失（§2.5）。
- 多角色/测试实例需要不同配置根，写 tracked 文件会串环境。
- 回滚、审计、并发都比 JSON 覆盖层难。

**迁移策略**：不一次性搬 137 个键。先在注册表里「登记」（默认值/范围/生效时机/影响），页面**只读展示 TOML 生效值**；Lan 一旦在页面改了某项，就写进 `settings.json` 覆盖层，从此该项以页面为准；提供「恢复跟随角色基线」按钮删除覆盖。这样零风险、可增量。

### 7.3 代码结构（建议新增 4 个模块，不改现有调用方语义）

```python
# config_registry.py —— 唯一注册表（新模块/新角色都往这里加）
@dataclass(frozen=True)
class Item:
    key: str            # 'autonomy.interval_minutes'
    section: str        # 'autonomy' | 'budget' | 'memory' | ...
    title: str          # 给 Lan 看的中文名
    kind: str           # bool | int | decimal | text | enum | time_range | path | list[str]
    default: object     # 代码默认（唯一）
    minimum: object = None
    maximum: object = None
    choices: tuple = ()
    unit: str = ''      # '分钟' | '元/日' | '次/日' | 'tokens'
    level: str = 'U'    # U | A | S（P 类不进注册表可写集合）
    apply: str = 'restart'   # immediate | next_request | next_wake | restart
    storage: str = 'overlay' # overlay | runtime_file | env | read_only
    effects: str = ''        # 1–3 句：改大/改小会影响什么
    requires: tuple = ()     # ('memory.enabled',)
    owner: str = ''          # 谁消费它（模块名）
    writable: bool = True
    sensitive: bool = False  # S：需二次确认 + 审计

# config_store.py —— 合并、校验、写入、审计
def effective(role, key) -> (value, source)      # default|toml|overlay|runtime|process
def set_value(role, key, value, actor) -> Result # 校验→写 overlay→审计→applier
def pending_restart(role) -> list[Item]          # 已保存但未生效

# config_apply.py —— 每段一个 applier；只调用现有 setter，不复制业务逻辑
APPLIERS = {'models': ..., 'autonomy': ..., 'memory': ..., 'tools': ..., 'budget': ...}

# config_diagnostics.py —— 复用 diagnostics 的 finding 结构
# 检查：未知键 / 死键 / 未就绪（enabled 但缺凭据）/ 进程值与文件值不一致 / 覆盖层值越界 / 待重启
```

注册表示例（这 4 条足以说明形态）：

```python
Item('autonomy.interval_minutes', 'autonomy', '自主活动间隔', 'int', default=180,
     minimum=15, maximum=360, unit='分钟', level='A', apply='next_wake',
     storage='overlay', effects='改小会更频繁醒来，受日次数与日预算共同限制。',
     owner='autonomy', writable=True)
Item('autonomy.daily_budget_cny', 'budget', '自主活动日预算', 'decimal', default='2',
     minimum='0.1', maximum='50', unit='元/日', level='A', apply='immediate',
     storage='overlay', effects='只限制自主活动；聊天不受影响。', owner='Budget')
Item('memory.experience.min_chars', 'memory', '经历整理触发字数', 'int', default=3000,
     minimum=500, maximum=50000, unit='字符', level='A', apply='next_request',
     storage='overlay', effects='调小会更频繁整理，花更多记忆模型费用。', owner='ExperienceMemory')
Item('web.github_readonly', 'tools', '只读 GitHub 研究', 'bool', default=False,
     level='S', apply='restart', storage='overlay',
     effects='只读公开仓库；不提供写权限、不使用维护 token。', owner='GitHubReadTools',
     sensitive=True)
```

### 7.4 生效机制（复用现有 setter，禁止页面直接改内存）

| apply | 实现方式 | 页面文案 |
| --- | --- | --- |
| `immediate` | 写入后调用现有 setter：`hub.set_activity/ set_glance/ set_quiet_hours/ set_codex_watch`、`experience.set_enabled`、`judge.set_enabled`、`hub.use_model`、`hub.save_models` | “已生效” |
| `next_request` | 覆盖层由每次请求的读取函数读取（新增 `config_store.effective` 调用点） | “下一句生效” |
| `next_wake` | 控制器在每次调度前重读本机覆盖 | “下次醒来自动用新值” |
| `restart` | 只写文件；顶部显示「有 N 项待重启」，提供「重启 Peter」按钮（`/api/exit` + 桌面壳自动拉起，`desktop/backend.cjs`） | “重启后生效” |

**关键约束**：`character.toml` 的启动校验仍是最后防线；覆盖层写入前必须用**同一套**校验函数（把 `load_character` 里的单字段校验抽成 `validate_item(key, value)`，两处共用），避免页面写进一个让 Peter 起不来的值。

### 7.5 预算中心

- 展示：日/月上限、已花、待核对预留、剩余；按类目（聊天/自主/瞄一眼/经历整理/判官/委派/日历/短期便签/工作台）与按模型；今日临时提额（内存态，重启清零，要显著标注）。
- 编辑：日/月上限（已有）、类目预算（新增，属 A 级——**涉及花钱，按仓库规则必须先经 Lan 同意才开放**）。
- 只读：`MAX_RUN_BUDGET_REQUEST=50`、`PROXY_MAX_REQUESTS`、各处 `MAX_*` 安全上限（P 类）。
- 耗尽行为要写成可读规则：类目超限只停该类目（聊天不受影响）；全局超限停所有付费请求；账本待核实冻结全部付费；跨天自动恢复；提额只到当天。

### 7.6 权限中心

- **只读矩阵**：每个工具 → `tool_policy` 结果（allow / ask / deny / workspace / lab / web / desktop_read / desktop_control / desktop_create）；每个 MCP 服务 → ask/direct；每个执行器 → 允许的项目根、网站、容器。
- **可管理（S 级，需确认 + 审计）**：功能开关（决定工具是否注册：`windows_interaction.enabled`、`desktop_file_read.enabled`、`screen_pop.enabled`、`web.*`、`executor.enabled`、MCP `enabled`）、MCP 服务权限、`append-grant` 列表撤销、项目根/网站/软件白名单。
- **不可管理（P 级，只展示）**：路径包含、秘密文件屏蔽、容器网络锁、代理请求上限、审批 TTL（24h）、账本冻结、`--acceptance` 沙箱。
- **审批历史**：`pending-approvals.json`（7 天保留）+ 新增配置变更审计（谁批准、批了什么、有效期）。

### 7.7 诊断与「启用≠就绪」

把 `diagnostics.py` 的配置检查扩成三类，并在总览页置顶：
1. **缺失**：`.env` 键、token 文件、凭据（邮箱/QQ）。
2. **死键/未知键**：注册表没有、代码也不读的键（§3.6），标 X 并给清理建议。
3. **不一致**：页面/文件值 vs 进程值（待重启）、覆盖层越界、`enabled=true` 但依赖缺失（如 `[email]` 无凭据、`[glance]` 无模型）。

同时修掉现在的误报：`diagnostics.py:415` 应像 `model_settings._key_available` 一样读 `.env`（`dotenv_values`），而不是 `os.environ`。

### 7.8 新模块 / 新角色的注册契约

新增一个可配置模块时，必须同时提交：
1. `config_registry` 条目（默认值、范围、级别、生效时机、影响说明、消费模块）。
2. 一个 applier（或声明 `restart`）。
3. 诊断检查项（缺什么会不工作）。
4. 预算类目（若花钱）。
5. 权限声明（若会访问 Lan 的文件/软件/网络）。
页面根据注册表自动生成分组与表单，**不再为每个模块手写页面**。

新角色：只需 `characters/<id>/character.toml` + `data/<id>/`；注册表按角色读取；`workbench_switch.py` 的硬编码 `CHARACTER='peter'` 必须改成按角色/配置根（见 §5 问题清单）。

### 7.9 迁移路线（风险从低到高）

| 步骤 | 内容 | 风险 | 前置 |
| --- | --- | --- | --- |
| 1 | 注册表 + **只读**总览页：展示所有项的生效值、来源、生效时机、影响；不改任何写入 | 极低 | 无 |
| 2 | 修诊断误报 + 死键/未就绪/不一致检查 | 低 | 1 |
| 3 | 覆盖层 + 校验 + 审计日志 + 「恢复跟随基线」；先迁 20 个高频项（自主频率/预算/安静时段、瞄一眼间隔/预算、记忆整理阈值、工具步数/结果上限、历史轮数） | 中 | 1,2 |
| 4 | 预算中心（类目注册 + 花费面板 + 提额可见） | 中（涉及钱，需 Lan 拍板） | 3 |
| 5 | 权限中心（矩阵 + 撤销 + 审批历史） | 中高（涉及权限，需 Lan 拍板） | 3 |
| 6 | 双源收敛与死键清理（把 TOML 可调项标注 deprecated，统一读覆盖层；删死键） | 中（需 Lan 同意删） | 3–5 |

---

## 8. 施工建议（后续工单拆分）

> 本次审计不写代码。以下拆分按「风险从低到高、每步都能独立验收」排列；本审计单为 446，后续从 447 起（#445 P3-4 已在审计期间合并）。

### 8.1 工单序列

| 工单 | 目标 | 交付 | Lan 怎么验收 | 风险 |
| --- | --- | --- | --- | --- |
| **446 全局配置审计（本单）** | 只读审计全系统配置，产出清单与统一中心设计 | 本报告 + `config-inventory.csv` + 八份底稿 | 报告 §0 能回答「现在能改什么、改不了什么、改哪影响什么、何时生效」 | 无（不改代码/配置） |
| **447 配置注册表 + 只读总览页** | 把散落配置登记成一张表，页面只读展示「生效值 / 来源 / 生效时机 / 影响 / 状态」 | `config_registry.py`、`config_store.effective()`、设置页「配置总览」tab、诊断误报修复（F-01） | 打开设置 → 配置总览，能看到自主/预算/记忆/模型/工具每一项现在是多少、从哪来、改完何时生效；诊断页不再误报钥匙 | 极低（只读） |
| **448 覆盖层 + 审计 + 高频项可写** | 页面可改 20 个高频项，写 `settings.json`，有校验/审计/回滚/待重启提示 | `config_store.set_value()`、`config_apply`、`config-changes.jsonl`、高频项表单 | 把「自主活动间隔」从 90 改成 60，页面显示「下次醒来自动生效」；重启后仍是 60；能一键恢复跟随基线；审计里能看到这次改动 | 中（写路径） |
| **449 预算中心** | 一处看全部钱：日/月/类目/模型/预留/剩余/提额；上限可改（分级） | 预算页 + 类目注册 + `/cost` 复用 | 打开预算页看到 §3.2 的数字；把自主日预算从 ¥3 改到 ¥5 后，第二天生效；超限时页面有横幅 | 中（涉及钱，类目预算需 Lan 同意） |
| **450 权限中心** | 三层权限一张表；可撤销持久授权；审批历史 | 权限页 + 撤销 `append-grant` + 展示代码策略（只读） | 能看到 Peter 对每个工具的权限级别；能撤销「持续追加」授权；能查到谁在什么时候批了什么 | 中高（涉及权限，需 Lan 同意） |
| **451 双源收敛 + 死键清理** | 每个开关只有一个可写来源；删死键/死模块 | 迁移脚本 + 兼容期 + 清理 | 关掉自主活动后，TOML 里旧值不再让它复活；`ombre_*`/死键不再出现在任何配置里 | 中（需 Lan 同意删） |
| **452 新模块注册契约 + 文档** | 以后新模块必须登记配置/预算/权限，页面自动生成 | `docs/config-registry.md` + 测试 | 让开发 Agent 加一个假的配置项，页面自动出现、改了生效 | 低 |

### 8.2 每张工单都必须带

1. **校验共用**：页面写入前调 `load_character` 的同一套单字段校验（抽出 `validate_item`），不允许页面绕过。
2. **生效声明**：每个字段标注 `immediate / next_request / next_wake / restart`，页面按此显示文案；restart 项要有「待重启」计数与重启按钮。
3. **审计与回滚**：每次写入记 `config-changes.jsonl`（时间、来源、键、旧值、新值、结果、是否已生效），页面支持撤销上一次。
4. **诊断联动**：新增配置必须同时加一条「缺什么会不工作」的检查。
5. **测试**：单元（校验/合并/越界/并发）+ 集成（页面 → 文件 → 运行时值）+ 隔离（两个角色/验收实例互不影响）。
6. **不改正式环境**：验收用隔离实例（`--config-root`/`--data-dir`/`--acceptance`）；正式部署走维护窗口。

### 8.3 需要 Lan 拍板的事（不要替她决定）

1. **类目预算是否上页面**：自主 ¥3/日、瞄一眼 ¥1/日、经历整理 ¥1/日——开放编辑等于允许改花钱上限，按仓库规则必须先问。
2. **临时提额的语义**：只保留工作台，还是扩展到所有类目？是否要持久化（重启仍有效）？
3. **死键清理**：`[memory] dimensions`、`ombre_model`、`JIUSHI_API_KEY`、`VEIL_CLAUDE_PATH`、`ombre_entry.py`/`ombre_host.py` 是否删除（涉及删代码/配置，需明确同意）。
4. **双源收敛方向**：开关类统一进 `settings.json`（推荐），还是继续用各自的 `*-settings.json`（改动小，但仍是多文件）？
5. **内置模型价格是否允许页面覆盖**：影响预算估算的准确性。
6. **哪些高级参数默认折叠**：频率/阈值类全部展开会让页面很长；建议按「会不会花钱 / 会不会改变行为」分两组。

### 8.4 建议的页面信息架构

```
设置
├─ 总览        状态卡片（预算/待重启/配置问题/关键开关）＋ 变更历史
├─ 模型与费用   模型用途、接口、价格、日/月上限、按模型花费
├─ 自主与后台   自主频率/次数/预算/安静时段、瞄一眼、Codex、通道轮询
├─ 记忆         整理/召回/判官/联想/事件/学习上下文（分组折叠）
├─ 工具与权限   工具清单与权限矩阵、MCP、授权与审批历史、可操作软件
├─ 工作台      开关/项目/网站/时限/预算/维护仓库与 token
├─ 交互与外貌   人设/背景/说话样例/主题/透明度/Peter 大小/自定义 CSS
├─ 系统与维护   备份、诊断、日志、更新、健康检查
└─ 高级 / 实验  程序安全约束（只读）、废弃配置（清理清单）、原始文件定位
```

原则：**U 级直接出现；A 级折叠并附「影响说明」；S 级二次确认 + 审计；P 级只读并注明「程序保证」；R 级进总览；X 级只在清理清单出现。**

---

## 9. 附录

### 9.1 证据索引（本报告引用的主要源码位置）

| 主题 | 位置 |
| --- | --- |
| 角色配置加载与校验 | `veil_agent.py:832-1174` |
| 预算与账本 | `veil_agent.py:1243-1890` |
| 模型设置/事务 | `model_settings.py:109-758` |
| 页面路由（GET） | `web_agent.py:2417-2480` |
| 页面路由（POST） | `web_agent.py:3361-3455` |
| 模型保存与热切换 | `web_agent.py:1501-1555,2818-2834` |
| 诊断配置检查 | `diagnostics.py:411-440` |
| 自主活动/瞄一眼 | `autonomy.py:432-1714` |
| 经历整理 | `experience_memory.py:1-190,540-640` |
| 想起判官 | `recall_judge.py:36-175` |
| 工具权限 | `tools_agent.py:34-110,455-575` |
| MCP | `mcp_services.py:90-130,640-680` |
| 工作台执行器 | `executor_opencode.py:40-180,1130-1250` |
| 自我维护 | `maintenance.py:170-200,560-610` |
| 备份/恢复 | `backup_restore.py:20-40,330-420,620-680` |
| 更新 | `update.bat:3,58`、`update-main.cmd:47,106` |
| 指令注册表 | `prompts/registry.py`、`prompts/store.py` |

### 9.2 配置清单字段说明（`config-inventory.csv`）

```
id, 域, 模块, 配置项, 键/常量, 源码位置, 存储位置, 代码默认值, 实际生效值,
合法值/单位, 修改方式/页面入口, 优先级/覆盖, 生效时机, 持久化,
风险/权限, 分类, 建议归属页面
```

- 实际生效值：能读到的写实际值；读不到的写「无法读取（原因）」。
- 分类：U 用户常用 / A 高级 / S 敏感权限 / R 只读运行信息 / P 程序安全约束 / X 冗余废弃。
- 建议归属页面按 §8.4 的九个分区。

### 9.3 本报告不覆盖的内容

- 不覆盖 Ombre 历史数据处置（#445 已合并清理工具与 runbook，但实际清理仍待 Lan 维护窗口执行）。
- 不覆盖手机端（VEIL 小手机仓库）配置。
- 不覆盖语音技术选型（见 `VEIL-Voice-前置审计与技术选型-2026-11-25.md`）。
- 不评估第三方模型/接口的服务条款与合规。
