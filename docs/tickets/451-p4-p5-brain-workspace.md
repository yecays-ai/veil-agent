# Ticket #451 · P4/P5 VEIL Brain 管理中心与全局工作区正式接入

- **分支 / Worktree**：`ticket/451-p4-p5-brain-workspace`（基于 `origin/main` `56abe3f`，隔离工作树 `VEIL/worktrees/451-p4-p5-brain-workspace`）
- **关联审计与设计**：
  - PR `#446` 全局配置审计（668 项清单与 8 域证据）
  - PR `#448` 配置总览与桌面端核心回归契约
  - Stage 2 确认原型与交互规范（`P4-P5/` 设计与补充清单）
  - 并行开发隔离约束：严禁改动 P6-S2（`#450`）独占文件（`brain_authority.py` / `brain_backup.py` / `brain_writer.py` / `event_memory.py` / `experience_memory.py` / `memory_native.py` / `veil_brain.py`）与正式运行目录 `veil-agent`。

## 核心交付

### 1. P4 / P5 只读后端引擎与 API（零付费 Token、SQLite `?mode=ro`）
- 新增 [`brain_p4_api.py`](file:///E:/Peter-Agent/VEIL/worktrees/451-p4-p5-brain-workspace/brain_p4_api.py)：
  - `overview()` -> `GET /api/brain/overview`：活跃/置顶/归档/待补向量/事件统计、7 日与 4 周新增与修订趋势、六层记忆系统运行态、近期写入与事件轨迹及最新健康摘要。
  - `library()` -> `GET /api/memory/library`：支持关键词 `q`、状态（`active`/`archived`/`locked`/`pending_vector`/`all`）、类型（`pinned`/`event_link`/`fragment`/`all`）、来源与多维排序及分页；提供正文 SHA-256。
  - `learned_versions()` -> `GET /api/peter-page/versions`：读取习得认知历史版本摘要。
- 新增 [`brain_health.py`](file:///E:/Peter-Agent/VEIL/worktrees/451-p4-p5-brain-workspace/brain_health.py)：
  - 实现严格只读（`file:...?mode=ro`）P5 `H-1` ~ `H-9` 确定性健康巡检、超时保护、`health-reports/` 脱敏快照与导出（`GET /api/brain/health`、`POST /api/brain/health/scan`、`POST /api/brain/health/settings`、`GET /api/brain/health/export`）及低开销后台定时守护线程 `start_health_scheduler(hub)`。
- 扩展 [`diagnostics.py`](file:///E:/Peter-Agent/VEIL/worktrees/451-p4-p5-brain-workspace/diagnostics.py)、[`request_snapshots.py`](file:///E:/Peter-Agent/VEIL/worktrees/451-p4-p5-brain-workspace/request_snapshots.py) 与 [`web_agent.py`](file:///E:/Peter-Agent/VEIL/worktrees/451-p4-p5-brain-workspace/web_agent.py)：
  - 保留完整 `recall` 分层明细（含 `relationship_base`）并关联 `costs.jsonl` 开销；
  - `GET /api/memory/event` 返回 `linked_memories`；
  - `POST /api/memory/edit` 支持 `expected_sha256` HTTP `409` 乐观锁防并发覆盖，`POST /api/memory/versions` 返回行级 Diff。
  - 集成 `/api/budget/summary`（账本预算）与 `/api/config/inventory`（`#446` 只读全量清单）。

### 2. 全局两级工作区与视觉系统
- [`desktop/workspace-shell.css`](file:///E:/Peter-Agent/VEIL/worktrees/451-p4-p5-brain-workspace/desktop/workspace-shell.css)、[`desktop/workspace-detail.css`](file:///E:/Peter-Agent/VEIL/worktrees/451-p4-p5-brain-workspace/desktop/workspace-detail.css)、[`desktop/workspace-mcp.css`](file:///E:/Peter-Agent/VEIL/worktrees/451-p4-p5-brain-workspace/desktop/workspace-mcp.css)：
  - 实现烟粉珍珠白（`rose`）、香槟奶油白（`gold`）、雾蓝银灰（`blue`）与夜幕（`night`）四套清透磨砂主题，桥接原有 `--veil-*` 语义变量。
  - 顶部两级 Nocturne 弧形子导航岛（指示器平滑移动）、自主活动控制卡 + 滑动开关 + 双栏时间线、`↑/↓` 滚动跳转与 MCP 折叠详情卡片。
- [`desktop/workspace-shell.js`](file:///E:/Peter-Agent/VEIL/worktrees/451-p4-p5-brain-workspace/desktop/workspace-shell.js) & [`desktop/workspace-brain.js`](file:///E:/Peter-Agent/VEIL/worktrees/451-p4-p5-brain-workspace/desktop/workspace-brain.js)：
  - 三组工作区边栏（日常 / VEIL BRAIN / 管理）及六大 Brain 二级页面（`brain-home`、`memory`、`workspace-events`、`brain-cognition`、`brain-recall`、`brain-health`）与工作台/预算/全量配置清单。

### 3. 日常聊天体验、独立小窗、头像持久化与三层 CSS
- [`desktop/main.cjs`](file:///E:/Peter-Agent/VEIL/worktrees/451-p4-p5-brain-workspace/desktop/main.cjs)、[`desktop/desktop-preload.cjs`](file:///E:/Peter-Agent/VEIL/worktrees/451-p4-p5-brain-workspace/desktop/desktop-preload.cjs)、[`desktop/chat-theme.css`](file:///E:/Peter-Agent/VEIL/worktrees/451-p4-p5-brain-workspace/desktop/chat-theme.css)、[`desktop/chat.js`](file:///E:/Peter-Agent/VEIL/worktrees/451-p4-p5-brain-workspace/desktop/chat.js)：
  - **独立小窗与伴随置顶**：每窗口记忆位置/尺寸与独立置顶开关（`view:get-pinned` / `view:set-pinned`），独立伴随小窗默认紧凑顶栏，支持随时收回主窗口与跨窗口状态同步。
  - **Peter 头像自定义与持久化**：持久化到 `userData/avatar.*`（自动迁移旧版 `localStorage['veil-avatar']`），点击顶栏/气泡头像/独立小窗按钮打开预览裁剪弹窗，多窗口实时同步。
  - **三层聊天 CSS 体系**：设计系统 -> `chat-theme.css` -> `userData/chat-custom.css`（保持原有 CSP 与外部资源拦截校验），支持设置页与独立小窗快捷样式编辑器、打开外部文件及重载。

## 复查后验证结果（2026-11-26）

- **定向 Python 单测**：`test_p4_p5_brain_routes`、`test_budget_workspace_route`、`test_model_settings`、`test_brain_authority_memory`、`test_web`（记忆页）全部通过；新增用例覆盖必需 `expected_sha256`、归档写入、巡检超时 `incomplete`/`H-T`、报告持久化失败 500、limits-only 保存保留配置、基础/有效限额分离。
- **全量 Python**：本机 `python -m unittest discover -s tests` 运行 2343 项。本机 22 项失败/错误均为环境差异，不在本次改动范围：
  - 本机 `core.autocrlf` 未设置，maintenance 用例里的 `git diff --check` 把 CRLF 当行尾空白（以 `GIT_CONFIG_KEY_0=core.autocrlf GIT_CONFIG_VALUE_0=true` 复跑 47/47 通过）；
  - 本机 `%APPDATA%\VEIL Desktop` 已有真实桌面数据，备份用例多出一项（以空 `APPDATA` 复跑 backup/closeout 13/13 通过）；
  - 本机未安装 `python-docx`（CI 由 `requirements.lock.txt` 安装）；本机 OpenCode 实机未产出测试文件（CI 环境差异）。
- **桌面 Node**：`node --test tests/*.test.cjs` **53/53 全绿**，含新增 `workspace-budget.test.cjs`（基础限额表单）与 `workspace-review-fixes.test.cjs`（编辑载荷/草稿/归档/置顶/头像/CSS 默认模板）。
- **Windows Electron 冒烟**：本机未安装 `desktop/node_modules`，未在本机重跑；由 CI `desktop.yml` 的 `npm test`、`npm run test:windows` 与打包覆盖。
## 隔离预览启动方式

双击仓库根目录 [`打开-VEIL隔离预览.cmd`](file:///E:/Peter-Agent/VEIL/worktrees/451-p4-p5-brain-workspace/%E6%89%93%E5%BC%80-VEIL%E9%9A%94%E7%A6%BB%E9%A2%84%E8%A7%88.cmd) 查看真实 Electron 桌面交互界面（使用临时虚构后端数据，不影响正式 Peter 实例）。

## CodeRabbit 复查修复（2026-11-26）

对 PR #456 的 6 条 inline major、1 条 outside-diff major 与 22 条 minor/additional 逐条核对；只修仍成立的实质问题：

### Major
- **记忆库保存丢正文**：`desktop/workspace-brain.js` 改为提交 `changes: { content }`，保留顶层 `expected_sha256`；`memory_native.panel_edit` 只接受 `changes`。
- **归档按钮不写入**：新增 `NativeMemoryTools.panel_archive()` 与 `POST /api/memory/archive`（走既有可逆 `trace archive=True` 路径），前端改用写入接口；`/api/memory/archived` 仍只做列表。
- **409 后草稿丢失**：库页面按记忆 ID 分开保存草稿与服务端正文；刷新后编辑器保留草稿，并展示服务器最新版本与「丢弃草稿」按钮；不自动用新哈希重试旧草稿。
- **限额保存把当日加额写进基础限额**：`/api/budget/summary` 增加 `base_limit_cny` / `raise_cny`，预算页表单只读写基础限额；`Budget.raise_today` 仍只在当日生效。
- **预算页保存清空接口/模型**：`ModelSettings.save()` 在 payload 缺少 `endpoints`/`models` 时沿用现有配置与角色选择；显式传空列表仍可删除。
- **巡检时限只覆盖部分阶段 / 未完成算通过**：`brain_health.run_health_scan()` 在取数、完整性、日志各阶段检查时限，改用 `COUNT(*)` 与向量元数据/有界样本；超时返回 `incomplete=true`、`timed_out=true`、`skipped_checks`、`H-T` 检查项，未执行项不标为通过；`daily_night` 只在角色夜间静默时段启动，未完成报告 10 分钟后可重试。
- **旧记忆编辑入口可能覆盖并发更新**：`/api/memory/edit` 现在必须携带 `expected_sha256`（缺失 400、当前不可读 409、不一致 409 且不写入）；`/api/memory/panel` 返回与后端一致的 `sha256`，旧编辑器保存时携带。

### Minor / Additional
- 巡检：`from`/`to` 先校验再返回（不再把请求区间标成报告区间）；快照先追加 `health-snapshots.jsonl` 再替换 `latest.json`，任一步失败让 POST 返回 500、调度器重试；损坏向量记录到 H-7 后继续；只读连接用 `Path.as_uri()` 编码（`brain_health.py`、`brain_p4_api.py`、`brain_read.py`）。
- P4 API：`learned_versions` 按 28 天窗口聚合版本而非只取最近 400 条；库列表用原始正文计算哈希并返回 `body_exact`，避免尾随空白导致永久 409。
- 记忆库：列表请求按序号丢弃过期响应；关联记忆按 ID 直接打开；事件/召回详情按序号防串台；费用按 `settle` 行合计；快照完整显示（`.diff-box` 可滚动，不再截 6000 字符）；加载失败允许重试；按钮在 `finally` 恢复；导出解析 JSON 的 `text` 字段；认知版本读取 `items`。
- 桌面：置顶桥接统一为 `getPinned`/`setPinned`/`onPinnedState` 并统一布尔值；头像先写新文件（临时文件 + rename）再删旧格式；默认自定义 CSS 注释不再触发 `validateCustomCssText`；`onPage` 先渲染再显示自定义页。
- 诊断：`diagnostics.trace()` 同时按 `request_id` 与 `id` 关联普通聊天的结算行。

### 未单开改动
- 纯 JSON 缩进、命名与文案风格建议不单改；`request_snapshots` 等已符合现有契约。

## 第二轮 CodeRabbit 复查修复（2026-11-27）

复审新增 17 条（2 条 major、1 条 heavy lift、14 条 minor），逐条核对后全部修复或按确认机制落地：

### 数据一致性
- **编辑哈希与写入原子化**：`brain_writer.update_memory()` 新增 `expected_sha256`，在 `BEGIN IMMEDIATE` 内比较后才写入；`BrainAuthority._trace` 透传，`panel_edit` 与 `/api/memory/edit` 传入；冲突抛 `WriteConflict`，不写、不留旧版本，返回 409。新增 writer 与面板级单测。
- **旧面板正文与哈希同源**：`panel_entries` 改用 `_panel_records()` 一次读取（Brain 的 `body`/`body_sha256`/`raw_markdown`），不再分别查询。
- **按 ID 判断存在性**：新增 `_visible_id()`，归档/编辑/删除/锁定/版本不再依赖 `panel_entries(limit=10000)` 列表。
- **巡检设置并发**：`save_health_settings()` 的读-合并-写放在同一把锁内。
- **巡检覆盖度诚实**：缺库扫描的 H-0 报告也会持久化；事件超过 20000 条时 H-4/H-5 标为“已核验 N/M（未全量）”，`sampled_events` 写入报告；H-2 的碎片判断改用全量 `event_id` 集合。
- **数据库异常**：总览、记忆库、手动巡检捕获 `sqlite3.Error` 返回 503；巡检设置写入 `OSError` 返回 500。

### 桌面交互
- 保存成功只清除“本次提交”的草稿，输入中的新草稿保留；归档/恢复不再清草稿；删除后仅当详情仍是被删记忆时清空详情。
- 列表与按 ID 打开改用独立请求序号，详情不再取消列表加载；事件/召回整页渲染加序号，过期响应不覆盖新视图。
- 巡检设置保存省略 `idle_only`；保存成功后同步当前开关/周期控件；`daily_night` 调度仅在 `idle_only=true` 且忙碌时跳过。
- 预算输入允许后端支持的正小数（`min=0.01`、`step=any`）。

## 第三轮 CodeRabbit 复查修复（2026-11-27）

- **编辑旧版完整性（major）**：带哈希的原子编辑现在向 BrainWriter 传 `archive_raw=True`，在同一 `BEGIN IMMEDIATE` 事务里归档完整 `raw_markdown`（含旧标题）；页面旧版本改为写入成功后再落 `memory-versions.jsonl`，冲突不留孤儿版本。
- **详情选择竞态**：`openMemoryById` 与卡片选择共用 `detailSeq`，选中其他记忆会立即使旧的按 ID 请求失效，不再覆盖新详情。
- **巡检保存乱序**：新增 `healthSaveSeq`，只应用最近一次保存的响应与控件状态，旧响应不会把表单改回旧值。

