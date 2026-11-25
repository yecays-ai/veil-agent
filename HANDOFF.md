# HANDOFF

## P3-1 启动解耦 + Brain-only 读路径（2026-11-25，本 PR，待复审）

- 已完成：启动链移除 `open_ombre`；Memory 可在无 backend 下运行；本地 trace schema、删除 dream；判官/事件缓存/最近创建/库统计等读路径改 Brain；BM25-only/空候选降级；pinned/importance 落库；自主删除申请改结构化状态；runtime_monitor 接入 Brain 写入失败。
- 测试：`test_p3_1_brain_only` 新增；native/authority/chat/recall/event/brain/web 相关套件通过；隔离实例验收通过。
- 正式状态：P3-0 已合并并完成维护窗口；正式 Peter 当前仍按 P2 方式运行（Ombre 18011 仍在，仅回退）；本单未部署、未停正式进程、未改正式数据。
- 未做：未自行合并；未启动 P3-2；未做正式环境 1–2 天观察。
- 下一步第一步：等复审；合并后部署并确认无 Ombre 启动，再做正式观察。
- 注意：P3-1 合并前不要停止正式 Ombre；正式测试只用新增专用测试记忆。

## P3-0 配置持久化与前置准备（已合并）

- #441 已合并；main 配置持久化生效；维护窗口已完成更新/重启/技术验收，正式库 digest ok、0 tombstone、页面/事件 API 正常。
- P2 正式体验已由 Lan 确认通过；P3-0 误删记忆已定点恢复，报告在仓库外。

## P2 正式接管（2026-11-25）

- 已执行：正式 Peter 停止 → 最终同步 → 一致性备份 + 恢复验证 → 事件完整数据迁移 → P2-C 标签治理 → 最终对账 → 启用 Brain 唯一读写权威 → 启动/重启验证 → Ombre 回退导出验证。
- 正式运行状态：Brain 唯一写入权威、shadow 关闭、retrieval=brain；Ombre 18011 仍运行，仅回退。
- 回退：按 `docs/tickets/p2/P2-formal-cutover-report.md` 第 6 节“当前 Brain 同 ID 导出 → Ombre 读取”，不要用旧备份直接覆盖当前 Brain。
