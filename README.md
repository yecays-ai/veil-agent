# VEIL

**在本机运行的 AI 伴侣系统。** 角色有自己的身份、长期记忆和可以自主度过的生活；同一套系统也能替你操作电脑。当前第一个角色是 Peter。

> 代码、记忆、人设、花费都在你自己的机器上，模型通过你自己的接口调用。仓库只保存实现与脱敏记录，不保存任何真实人设、聊天、记忆或密钥。

---

## 核心理念

- **陪伴和干活是同一个角色。** 他既是陪着你的人，也是能替你做事的工具，不拆成「表演层」和「工具层」。
- **电脑和手机是他的两个身体。** 你不在电脑前时，手机是完整入口，不是遥控器。
- **不绑定某个角色、模型或界面。** 当前先跑通 Peter 的完整生活闭环，系统本身始终支持独立 Agent。
- **先把一整串跑通，再修整。** 不追求每一格都精细。

完整方向见 [`docs/PRODUCT_PREMISE.md`](docs/PRODUCT_PREMISE.md)（五条线、两条骨架验收场景、阶段顺序）。

## 当前能力

### 陪伴与对话

- 桌面聊天：透明桌宠 + 独立聊天窗，消息可分组、引用、重新生成、中途叫停；支持连发、插话、已读不回、「晚点回」。
- 语音：浏览器语音听写、按住说话、语音消息与音色规则。
- 自己的时间：自主唤醒、可自己排闹钟、夜间安静时段、每天次数与预算上限。

### 记忆（VEIL Brain）

- 单一记忆后端 `brain.db`：记忆正文、版本、向量、事件故事、阶段、证据、原话引用、关系与来源都在同一个库。
- 事件优先：一段共同经历会沉淀成有阶段、有原话证据、可追溯的故事，而不是一条条孤立摘要。
- 检索：向量 + 关键词混合召回，带判官筛选、追问承接、冷却与轮内去重；换 Embedding 模型走显式重建。
- 你可见可管：记忆库、事件时间线、认知版本、召回追踪、健康巡检（H-1 ~ H-9）都是只读/可编辑的真实页面。

### 电脑干活

- 文件与创作：受限工作区内的读写、编辑、移动、删除，旧版本程序留存可恢复。
- 桌面操作：观察窗口、操作界面、整理图标、在你授权的桌面目录新建文件。
- 外部世界：网页搜索与读取、独立浏览器、小红书只读、GitHub 只读研究、MCP 服务接入。
- 自主活动：他自己决定做什么，结果进入经历与记忆，花费计进同一本账。

### 界面

- 三组工作区（日常 / VEIL BRAIN / 管理），四套主题（烟粉珍珠白、香槟奶油白、雾蓝银灰、深色夜幕）。
- 独立伴随小窗、可单独置顶、头像自定义、三层聊天 CSS 定制。
- 只读配置总览与全量配置清单，不用翻配置文件也能看清当前行为。

### 数据与安全

- 每个角色只访问自己的数据目录；权限由程序控制，不靠提示词。
- 硬预算：日/月上限、按请求预留与结算、花费对不上时不猜。
- 冷备份 / 校验 / 显式恢复，恢复前整体移走原数据并保留安全副本。

## 系统架构

```text
桌面壳（Electron）
  桌宠窗口 · 聊天 · 工作区页面 · 设置与审批
        │
        │  本机 HTTP / WebSocket（一次性令牌，只监听 127.0.0.1）
        ▼
本机服务（Python）
  ├─ 对话与工具循环 · 提示词装配 · 流式输出
  ├─ 记忆层：写入事务 / 检索 / 事件 / 关系 / 巡检
  ├─ 自主活动 · 日历 · 长任务 · 语音 · 外部通道
  ├─ 预算账本 · 权限审批 · 诊断与追踪
  └─ 工具：文件 / 桌面 / 浏览器 / MCP / 网页
        │
        ▼
本机数据（不进仓库）
  characters/ · persona/ · data/<角色>/brain.db
  chat.jsonl · costs.jsonl · .env
```

| 层 | 位置 | 职责 |
| --- | --- | --- |
| 桌面壳 | `desktop/` | Electron 主进程、页面前端、窗口与置顶、请求守卫 |
| 本机服务 | `web_agent.py`、`veil_agent.py` | 聊天编排、工具循环、状态与花费展示 |
| 记忆 | `brain_*.py`、`event_memory.py`、`recall_*.py` | 写入事务、检索、判官、事件与证据、健康巡检 |
| 自主与生活 | `autonomy.py`、`life_*.py`、`long_tasks.py` | 醒来自主活动、日历、长期任务、工作检查点 |
| 工具与接入 | `tools/`、`mcp_services.py`、`browser_tools.py`、`*_channel.py` | 文件、桌面、浏览器、MCP、QQ/邮件通道 |
| 规则与配置 | `characters/`、`persona/`、`instructions.py` | 角色配置、人设、可编辑提示词 |
| 数据 | `data/<角色>/` | 聊天、账本、记忆库、workspace（全部本机、全部不进仓库） |

## 当前开发阶段

| 线 | 目标 | 状态 |
| --- | --- | --- |
| A 陪伴核心 | 身份连续、共同经历、自主生活、关系连续 | **施工中**：P6 记忆与检索精修，S0/S1/S2/S4 已完成 |
| B 电脑干活 | 普通操作直接做，敏感操作向你申请授权 | 权限、审批、预算底座已具备，按需补缺口 |
| C 手机 | C1 连得上 → C2 电脑功能搬上手机 → C3 手机专属陪伴 | 未开工，技术调查已完成 |
| V 语音 | 语音消息 → 通话 → 唤醒 | 语音消息已可用，通话与唤醒未开始 |
| D 自己升级 | 角色自己发现改进、提 PR，合并由你决定 | 随时可发起，不占阶段顺序 |

工程侧当前停在「P6 实验结论待拍板」：S3/S5/S6/S7 的方向还没定，未开工。

## 快速开始

前置：Windows 10/11、Python 3.11 或 3.12、Node.js 24（桌面构建需要）。

```powershell
git clone https://github.com/yecays-ai/veil-agent.git
cd veil-agent
setup.bat            # 安装 Python 依赖到本项目 .venv
setup-desktop.bat    # 安装并构建桌面程序
start-desktop.vbs    # 启动
```

首次启动需要两个本机私有文件（仓库里只有示例）：

- `.env`：由 `.env.example` 复制，填入你自己的模型与 Embedding 密钥；
- `persona/peter.md`：由 `persona/peter.example.md` 复制，写你要的角色人设。

完整步骤、更新方式和常见问题见 **[`docs/installation.md`](docs/installation.md)**；日常使用、命令、工具与预算见 **[`docs/usage.md`](docs/usage.md)**。

## 文档导航

### 入门

| 文档 | 内容 |
| --- | --- |
| [`docs/installation.md`](docs/installation.md) | 安装、密钥与人设、启动、一键更新 |
| [`docs/usage.md`](docs/usage.md) | 日常使用、聊天命令、记忆与工具、预算口径 |
| [`desktop/README.md`](desktop/README.md) | 桌面版启动、构建、使用与验收边界 |
| [`docs/backup-and-restore.md`](docs/backup-and-restore.md) | 冷备份、校验、显式恢复 |
| [`docs/data-repair-and-ledger.md`](docs/data-repair-and-ledger.md) | 数据损坏修复与账本核对 |

### 产品与规则

| 文档 | 内容 |
| --- | --- |
| [`docs/PRODUCT_PREMISE.md`](docs/PRODUCT_PREMISE.md) | 产品纲领：五条线、两条验收场景、阶段顺序 |
| [`AGENTS.md`](AGENTS.md) | 开发 Agent 施工规则（四类工单、并行 worktree、审查与合并边界） |
| [`docs/RELIABILITY.md`](docs/RELIABILITY.md) | 长期可靠性计划的阶段与退出条件 |
| [`docs/p3-4-maintenance-runbook.md`](docs/p3-4-maintenance-runbook.md) | 维护窗口操作手册 |

### 状态与验收

| 文档 | 内容 |
| --- | --- |
| [`STATUS.md`](STATUS.md) | 当前进度、近期工单与 PR 的真实状态 |
| [`HANDOFF.md`](HANDOFF.md) | 交接：停在哪、下一个窗口第一步 |
| [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md) | **待 Lan 验收清单**（按模块，可勾选） |

### 工程记录

| 文档 | 内容 |
| --- | --- |
| [`docs/reports/`](docs/reports/) | 架构、审计、实验与验收报告 |
| [`docs/reports/p6-4-brain-architecture-integration-20261126.md`](docs/reports/p6-4-brain-architecture-integration-20261126.md) | Brain 架构整合与 P6 施工路线 |
| [`docs/reports/p6-s4-decision-table-20261126.md`](docs/reports/p6-s4-decision-table-20261126.md) | P6-S4 白话决策表（S3/S5/S6/S7 方向） |
| [`docs/reports/config-audit/2026-11-25-global-config-audit.md`](docs/reports/config-audit/2026-11-25-global-config-audit.md) | 全局配置审计（668 项清单） |
| [`docs/tickets/`](docs/tickets/) | 全部工单（编号即施工顺序，PR 号另记在 STATUS 与清单里） |

## 项目状态

- 运行版本：`main`，正式目录跟随 `main` 一键更新。
- 自动检查：GitHub Actions 跑 Windows/Linux × Python 3.11/3.12 全量离线测试、桌面 Windows 测试与打包、依赖安全检查。
- **自动检查通过不等于体验验收通过。** 哪些功能已经你实际确认、哪些还等你体验，只看 [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md)。

## 隐私与边界

- 不提交：人设正文、API Key、真实聊天、真实记忆、本机绝对路径、运行日志。
- 测试只用假人设、假数据、假模型，不调用付费接口。
- 仓库只保存实现与脱敏后的审计/实验报告；真实运行数据留在本机 `data/`。
- 角色定位与关系以本机人设文件为准，不写进代码。
