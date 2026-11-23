# 工单 431 · P4 开源记忆管理界面复用与只读原型

## 这一格

A 陪伴核心（记忆注入与精修的配套管理界面）。不是 P0-B 影子同步、不是 P1 检索。

## 做完后 Lan 能多做的一件事

在现有 VEIL 桌面 App 里打开一个独立的「记忆中心」窗口，直接浏览 brain.db 里的真实记忆，
点开一条能看到正文、状态、来源和版本；还能跑一次本地健康检查，看到重复/断链/墓碑等真实问题。
这一轮只读，目的是让她亲手判断这套开源界面值不值得继续适配。

## 验收（Lan 在 Windows 上做）

1. 右键 Peter 打开菜单，点「记忆中心（原型）」；出现独立窗口。
2. 「记忆」页显示真实记忆（不是演示数据）。搜索框可搜正文。
3. 点第一条：右侧/弹层显示正文、状态（正常/隐藏/锁定）、来源、事件、版本；切「版本」能看旧正文。
4. 「健康检查」→「开始检查」：显示本地检查结果（当前真实数据下应有 7 个事件来源断链、1 条隐藏记忆等）。
5. 同时照常和 Peter 聊天、让他保存一条记忆，确认记忆中心不影响聊天和保存；关掉记忆中心窗口即结束。

## 当前状态（2026-11-23，待 Lan 体验验收）

- 已完成只读原型：`desktop/brain/`（服务端 + 适配层 + vendored mnemory 前端）、桌面菜单入口、独立窗口。
- 真实数据实测：brain.db 283 条记忆（244 条非删除 + 39 条墓碑），281 条版本、168 条 sidecar、244 条向量；
  窗口显示记忆总数 244，详情能读正文/状态/来源/版本。
- 健康检查是本地确定性检查，不调用模型：当前真实数据查出 5 个问题（4 个事件来源断链、1 条隐藏记忆），
  与工单 428 记录的 7 个悬空来源一致（按事件合并后为 4 条）。
- 写操作全部拒绝（HTTP 403）；前端把编辑/删除/修复按钮隐藏。brain.db 以 `readOnly` 打开，测试也断言写入 403。

## 开源调查与复用结论

| 候选 | 许可证 | 实际验证 | 能否嵌入/接 brain.db | 结论 |
|---|---|---|---|---|
| fpytloun/mnemory | Apache-2.0 | 284★；下载固定提交 `0ff5a8d`，UI 是完整可运行的 Alpine/Tailwind 管理台；自带浏览、仪表盘、健康检查、版本/历史、关系图 | 前端可独立运行；后端绑 Qdrant + LLM，不能直接接 brain.db；用本项目 server 实现只读兼容 API | **选它做界面**；只复用前端，不引入它的记忆后端 |
| zaydek/sqlite-viewer | MIT | 0★；Node 24 直接 `node server.js brain.db` 跑通，零依赖，只读行/DDL/外键图/SQL | 能直接读 brain.db，但只是通用数据库控制台（见 `sqlite-viewer.png`） | 备选；不作为记忆中心主界面 |
| mandoof1/amnis | MIT | 1★；FastAPI + ChromaDB + sentence-transformers，UI 单文件 2338 行，接口绑自家 Pydantic/Chroma | 依赖重（模型、向量库），会引入第二套记忆后端 | 不采用 |
| MANISH007700/OpenMemoryUI | **无 LICENSE** | GitHub 仓库无 licence 文件（API 404），页面是 localStorage 演示 | 法律上不能复制 | 不用 |

补充候选：没有继续展开 mem0/OpenMemory、Letta ADE 等重型方案，因为它们都需要 Docker/Postgres/Qdrant
或完整 Agent 运行时，违反「不额外启动大型服务、不引入第二套权威记忆系统」。

### 保留 / 适配 / 缺失

- 保留：记忆列表 + 搜索、详情弹层、版本历史、仪表盘（Chart.js）、健康检查的筛选/分组/严重度展示、D3 关系图代码（本轮未接）。
- 适配：把 mnemory 的 `MemoryItem`/history/links/fsck 响应映射到 brain.db；中文标签；只读护栏；入口与窗口。
- 缺失：召回诊断（候选→判官→最终注入及原因）在四个候选里都没有；语义搜索/重复语义判定需要 P1 检索；
  写入、修复、工件、会话、多用户切换均未接。

## 关键文件

- `desktop/brain/server.cjs`：只读兼容 API + 静态资源。
- `desktop/brain/ui/`：vendored mnemory 前端（Apache-2.0，见 `LICENSE-mnemory.txt`、`NOTICE.md`）。
- `desktop/brain/tools/patch-ui.py`：可重放的 UI 补丁（换上游版本后重跑）。
- `desktop/main.cjs`：`openBrainWindow()` 与 `menu:action=brain`；窗口只允许来自本地 brain 服务。
- `desktop/menu.html`：新菜单项。
- `desktop/tests/brain.test.cjs`：合成 brain.db 的端到端只读测试。

## 边界与回退

- 不写 brain.db；不碰 Peter 的聊天、记忆保存、P1 检索、P0-B 同步链。
- brain.db 路径：`<agent root>/.local-tmp/brain-p0a/brain.db`；可用 `VEIL_BRAIN_DB` 覆盖。没有该文件时弹窗提示。
- 回退：删掉「记忆中心（原型）」菜单项和 `desktop/brain/`，或 revert 本 PR；brain.db 与正式记忆零改动。

## 验证证据

- `npm test`（desktop）：38 条通过（含新增 brain 只读测试）。
- 真实 brain.db：窗口截图 `dashboard.png` / `detail.png` / `health.png`（本机验证用，含真实记忆，**未提交仓库**）。
- Electron 窗口实测：`VEIL_BRAIN_AUTOOPEN=1` 启动后日志 `[p4] brain window opened`，窗口截图由 `capturePage()` 取得。
- 入口：菜单项 + 独立 BrowserWindow；聊天窗口未改动。
