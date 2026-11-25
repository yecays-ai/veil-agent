# HANDOFF

## 全局管理中心首批 · `ticket/448-desktop-regression`

- 基线 `0a688ad`，包含已合并的 #446。首批改动为只读注册和模型用途总览，另加桌面原功能的回归测试。Windows Electron 使用假后端的 smoke 通过，不能当作发布包或用户验收。
- 整体顺序：配置覆盖层及诊断 → 预算和权限 → 全局 UI 与 Brain P4/P5 → 隔离验收及另行授权的发布。各批次从真实运行消费者取证，不把 CSV 当实际配置；不得修改正式 Peter。原聊天「＋」和生活记录日历必须沿用原交互和执行链。
- 下一步先汇审当前分支，并核实 Windows/Ubuntu CI 与评审，再处理后续分域接入。范围见 [工单](docs/tickets/448-desktop-regression.md)。

## 基线与上线边界

- [#446 审计报告](docs/reports/config-audit/2026-11-25-global-config-audit.md) 和同目录清单/证据是设计依据；里面的规划编号不要当实际 PR 编号。#447 是 P3-4 的维护兼容修复。
- 2026-11-25 的维护验收记录确认正式 Brain 底座在 `160b0ec` 已部署，P3-4 完成清理及 Ombre 卸载；稳定排序修订尚未入 main。本轮未复验正式实例。不能用旧备份直接覆盖有新增记录的 Brain。
