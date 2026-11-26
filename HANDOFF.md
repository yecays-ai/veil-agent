# HANDOFF

## Ticket #451 · P4/P5 VEIL Brain 管理中心与全局工作区（`ticket/451-p4-p5-brain-workspace`）

- **基线**：基于 `origin/main`（`56abe3f`，含 `#446`、`#448`、`#449`）。未修改任何 `P6-S2`（`#450`）占用文件，也未接触正式 `VEIL/veil-agent` 实例与数据。
- **本单交付**：
  - `brain_p4_api.py`（`GET /api/brain/overview`、`GET /api/memory/library`、`GET /api/peter-page/versions`）、`brain_health.py`（严格 `?mode=ro` 的 P5 `H-1..H-9` 只读健康检查 + 脱敏导出 + 周期巡检调度）及 `web_agent.py`（含 `expected_sha256` HTTP `409` 编辑锁、事件关联记忆、`/api/budget/summary` 与 `/api/config/inventory`）。
  - 统一桌面工作区：`desktop/workspace-shell.css`（四套主题与 Nocturne 二级导航岛）、`desktop/chat-theme.css`、`desktop/workspace-shell.js`、`desktop/workspace-brain.js`、`desktop/workspace-budget.js`、`desktop/workspace-inventory.js`。
  - 独立窗口与个性化升级：`desktop/main.cjs` / `desktop/desktop-preload.cjs` / `desktop/chat.js`（`userData/avatar.*` 头像持久化与裁剪、三层 `userData/chat-custom.css`、`view:set-pinned` 窗口置顶、MCP 开关卡片与折叠详情）。
  - 工单与文档：`docs/tickets/451-p4-p5-brain-workspace.md` 与根目录 `打开-VEIL隔离预览.cmd`。
- **预览方法**：关闭现有预览窗口后，直接运行隔离工作树根目录 `打开-VEIL隔离预览.cmd` 看真实 Electron 视觉效果（仅虚构后端数据）。
- **待验收项**：等待 Lan 在隔离预览确认视觉风格、宽窄/独立伴随小窗体验及 P4/P5 六工作区功能；确认后方可发起 PR 合并。

## P6-S0 · `ticket/449-p6-s0-baseline-sync`（施工/验收交接）

- **基线**：基于 `origin/main`（`2408bb6`，含 `#446` 全局配置审计与 `#448` 全局管理中心首批只读配置总览及桌面回归）。
- **本单交付**：
  - 将 P3-4 维护窗口已验证的确定性排序补丁同步入 `scripts/p3_4_cleanup.py`，并在 `tests/test_p3_4_cleanup.py` 补充排序确定性单元测试；
  - 同步脱敏后的 `docs/reports/p3-4-final-acceptance-20261125.md`、`docs/reports/p6-0-memory-audit-20261126.md`、`docs/reports/p6-1-retrieval-judge-association-audit-20261126.md`、`docs/reports/p6-2-relationship-weight-evolution-audit-20261126.md`、`docs/reports/p6-3-context-budget-audit-20261126.md`、`docs/reports/p6-4-brain-architecture-integration-20261126.md` 与 `docs/tickets/449-p6-s0-baseline-sync.md`；
  - 统一 `STATUS.md` 与 `HANDOFF.md`（对齐 `#446`/`#448`、P3-4 完成态与 P6-4 批准施工路线）。
- **仓库基线**：P6-4 所引用的全部 P6-0～P6-3 审计依据均已脱敏入库，后续开发窗口可直接从 `docs/reports/` 读取完整证据链。

## 下一阶段已批准路线（`P6-4` 定稿）

1. **`P6-S2`（下一窗口第一步）**：基于最新 `main` 开立独立 worktree，优先确立统一单事务写入与单份 `versions` 契约（`BrainAuthority` + `BrainEvents`），落地 `ExperienceMemory` 防丢稿与日期归一化、`_capture_evidence` 时间排序+分通道配额保留 `chat` 原话、`brain_backup` 协同快照与故障恢复演练，并完成历史失败批次审计与 `2026-11-25` 58 条丢稿消息的幂等恢复（同时可在 `recall-lab` 零代码冲突并行制作只读 `eval-v2` 独立标注集）。
2. **`P6-S1`**：在 `S2` 契约之上修复 CRLF `card_text`/`_with_metadata`、同轮单次 Embed 复用、`breath_search` 日期/权重、移除 `reuse_recall` 死代码与 `drift` 死分支、判官独立超时，产出 `Baseline-1`。
3. **`P6-S4`**：在 `recall-lab` 隔离回放运行 21 项严格单变量原子消融（总预算硬上限 `¥20.00`，按 `Tier 0 (¥0) → Tier 1 (≤¥6) → Tier 2 (≤¥10)` 价值排序执行，含 `AB-E1` 巩固收益验证）。
4. **依实验结果实施 `P6-S3 / P6-S5 → P6-S6 → P6-S7`**；严格保留 `input_token_ratio`/`tool_groups` 归 M2、`parse_cache_usage` 归 M3、瞄一眼/弹幕连续性不削减。

## 正式运行与数据安全红线

- 正式运行目录固定只跟 `main`；不得在正式运行目录开发或向正式 Peter 发消息/写记忆/启停进程。
- 回退严禁直接用旧备份覆盖有新增记录的 `brain.db`；必须使用 `brain_backup.py` 安全恢复与协同对账流程。
