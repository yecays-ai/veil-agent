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

## 隔离验证结果

- Python 单元测试：`Ran 67 tests in 5.384s — OK`（`test_p4_p5_brain_routes`、`test_budget_workspace_route`、`test_workspace_inventory`、`test_diagnostics`、`test_request_snapshots`、`test_brain_read`、`test_config_overview_route`、`test_config_registry`）。
- 桌面 Node 契约与回归测试：`37/37` 全绿（`ui.test.cjs`、`workspace-mcp.test.cjs`、`workspace-inventory.test.cjs`、`workspace-brain.test.cjs`、`backend.test.cjs`）。
- Windows Electron 真实窗口 E2E 冒烟测试：`desktop/tests/smoke.cjs` 退出码 `0`（完整覆盖原有回归项与 P4/P5 六大工作区、主题切换、独立小窗置顶及健康报告折叠交互）。

## 隔离预览启动方式

双击仓库根目录 [`打开-VEIL隔离预览.cmd`](file:///E:/Peter-Agent/VEIL/worktrees/451-p4-p5-brain-workspace/%E6%89%93%E5%BC%80-VEIL%E9%9A%94%E7%A6%BB%E9%A2%84%E8%A7%88.cmd) 查看真实 Electron 桌面交互界面（使用临时虚构后端数据，不影响正式 Peter 实例）。
