# 当前状态

## P6-S0 · 基线同步与 P3-4 确定性清理补丁入库（`ticket/449-p6-s0-baseline-sync`）

- **目标**：同步 P3-4 维护窗口已验证的 `scripts/p3_4_cleanup.py` 确定性排序补丁、P3-4 验收报告、P6-0/P6-4 审计与整合设计报告，并对齐 `main` 最新状态（`#446` + `#448` + P3-4 + P6-4）。
- **现状**：已在独立 worktree 完成补丁合入、确定性排序单元测试与脱敏文档同步，准备提交 PR 与 CI 检查（见 [工单 449](docs/tickets/449-p6-s0-baseline-sync.md)）。
- **验证**：隔离运行 `tests/test_p3_4_cleanup.py` 及相关离线单元测试。
- **下一步**：PR 合并后，基于最新 `main` 按既定顺序 **`P6-S0 → P6-S2 → P6-S1 → P6-S4 → 依据实验结果实施 P6-S3 / P6-S5 → P6-S6 → P6-S7`** 启动 `P6-S2`。

## P6-4｜VEIL Brain 全局架构整合与施工设计（定稿完成，已批准按序推进）

- **交付**：[p6-4-brain-architecture-integration-20261126.md](docs/reports/p6-4-brain-architecture-integration-20261126.md)（架构整合定稿），完整引用以下已同步入库的脱敏专项审计依据：
  - [p6-0-memory-audit-20261126.md](docs/reports/p6-0-memory-audit-20261126.md)（开源调研与差距审计）
  - [p6-1-retrieval-judge-association-audit-20261126.md](docs/reports/p6-1-retrieval-judge-association-audit-20261126.md)（检索、判官与自然联想审计）
  - [p6-2-relationship-weight-evolution-audit-20261126.md](docs/reports/p6-2-relationship-weight-evolution-audit-20261126.md)（关系认知、记忆权重与动态演化审计）
  - [p6-3-context-budget-audit-20261126.md](docs/reports/p6-3-context-budget-audit-20261126.md)（上下文组装、注入与预算审计）
- **核定路线**：`P6-S0`（基线同步）→ `P6-S2`（单事务写入/版本契约 + 防丢稿 + 证据链 + 协同备份与故障恢复演练 + `2026-11-25` 58 条丢稿幂等恢复）→ `P6-S1`（检索/卡片/元数据确定性修复）→ `P6-S4`（21 项严格单变量原子消融，`¥20` 总预算硬上限）→ 依据实验结果实施 `P6-S3` / `P6-S5` → `P6-S6` → `P6-S7`。
- **边界**：严格分离 P6 与 M2（`input_token_ratio` 校准与 `tool_groups` 瘦身）、M3（`parse_cache_usage` 与 `tools` 缓存边界）及后续聊天体验专项调优（完整保留瞄一眼与弹幕连续性及时间感知，本阶段不削减）。

## 全局管理中心 · 首批（`#448` / `2408bb6`，已合并）

- **目标**：让用户看清模型用途的配置选择、实际生效与待重启状态，保留聊天「＋」菜单和生活记录日历。
- **首批**：只读注册契约（`config_registry.py`）、五种模型用途总览（`GET /api/config/overview`）及桌面回归测试（121 项 Python + 25 项桌面 + 隔离 Electron 假后端 smoke 通过；正式打包及用户体验待验）。
- **#446 全局配置审计（已合并 `0a688ad`）**：八域取证与 668 项配置清单（`docs/reports/config-audit/`）。

## Brain 正式底座（P3-0 ~ P3-4 已合并并完成正式部署）

- P2 正式体验由 Lan 确认通过；P3-0 至 P3-4 已合并并完成正式维护部署（[P3-4 最终验收报告](docs/reports/p3-4-final-acceptance-20261125.md)）：正式清理、Ombre 物理卸载、数据对账（mismatch 0）、vault 导出与 Brain 备份恢复演练均通过；确定性排序补丁随 Ticket 449 合入 `main`。
- P3 的保存、想起、事件续写与费用连续体验持续由 Lan 在正式实例观察；未启动正式 P4/P5 界面施工。
