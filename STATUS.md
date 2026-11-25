# 当前状态

## P3-1 启动解耦 + Brain-only 读路径（PR #442，CI 全绿，待复审/合并）

- **目标**：正式 Peter 启动不再依赖 Ombre 进程；Brain 独立完成记忆读写、召回、页面/事件/经验整理；修复 P2 已知缺陷；保留一致性备份/离线导出用于回退。
- **已完成**：启动链移除 `open_ombre`；Memory 可在无 MCP backend 下运行；NativeMemoryTools 使用本地 trace schema，删除无实际作用的 dream 工具；判官/事件账本/故事缓存/最近创建/库统计/事件邻接/召回元数据改读 Brain；检索失败只降级 BM25 或返回空候选并写诊断；pinned/importance 落库并同步 raw front matter；自主删除申请改用 `delete_requests.status`；Brain 写入失败上报 runtime_monitor；无 Brain authority 时明确提示并跳过记忆，不启动 Ombre。
- **首次启动**：全新角色没有数据目录时自动创建空 brain.db（完整 schema、事件表、digest）；已有角色数据但 brain.db 缺失或损坏时明确报错并停止，**不会创建空库掩盖数据丢失**；首次初始化加跨进程写锁，避免双实例同时建库。
- **复审**：CodeRabbit 5 条实质意见已修复（元数据更新不重算向量、召回读 DB、BM25 权重、隐藏删除申请不泄露、启动门控）；PR 评论已记录。
- **测试**：新增 `tests/test_p3_1_brain_only.py`（12 项，含全新角色初始化/已有角色缺库拒建/损坏库不替换）；相关 memory/recall/event/brain/web/chat 套件通过；隔离实例验收通过；Windows/Linux 四组 CI 全绿，scanner/audit 通过。
- **未做**：未合并 PR（等复审/用户决定）；未部署正式环境；未停 Ombre；未改正式数据；未启动 P3-2。

## P3-0 配置持久化与前置准备（已合并，2026-11-25）

- #441 已合并，main 含 Brain 权威过渡配置；`update-main` 后配置仍为 `write_authority=true`、`shadow=false`、`retrieval.backend=brain`。
- 维护窗口已完成：正式目录更新到 #441 合并提交、桌面重新打包、Peter 重启、页面/事件 API 与 brain.db 完整性验收通过；本机未跟踪文件已恢复，未删除正式记忆。
- P2 使用证据与 P3-0 验收记录在仓库外审计目录；后续测试只用新增专用测试记忆。

## P2 正式接管（2026-11-25，已完成）

- 正式配置：Brain 唯一写入权威、shadow 关闭、自动召回 backend=brain；Ombre 仍在 18011，仅临时回退，未卸载。
- 数据：P2 切换与标签治理完成；正文/ID/版本/事件/来源/关联不变；0 pending、digest ok。
- 回退：当前 Brain 已有同 ID 导出；独立 Ombre 读取验证通过。详见 `docs/tickets/p2/P2-formal-cutover-report.md`。

## Lan 验收记录

- 2026-11-25：Lan 确认 P2 正式体验 + P3-0 技术验收通过；P3-1 gate 已开。
- 待 P3-1 合并部署后：Lan 在正式 Peter 上做 1–2 天观察（保存/想起/事件续写/页面/费用/无 pending），并确认启动无 Ombre/18011。

## 下一步

1. 等待 P3-1 PR #442 复审与合并；不自行合并。
2. 合并后按维护窗口流程部署，确认启动无 Ombre、无 18011、记忆读写/页面/事件正常；观察 1–2 天。
3. P3-2 按工单顺序；P3-4 不可逆清理需 Lan 维护窗口授权。
