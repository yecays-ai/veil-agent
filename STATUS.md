# 当前状态

## P2 收尾（本 PR / #439，未切换正式 Peter）

- **代码修复**
  - Brain authority 检索：embedder 对象不再被提前压成函数；writer 用 callable、retrieval 用 `.embed()`，
    主动召回不再因此静默回退 Ombre。
  - 运行时配置隔离：`Character.root` + `runtime_env`；`Memory.embedder`/`RecallJudge`/Ombre 子进程只读
    本实例 config-root 的 `.env`，隔离实例不会偷读正式 `.env`；默认启动行为不变。
  - 迁移：events.jsonl 追加更新行按 event_id 折叠；digest 覆盖 event_stages 并在写入/导账后刷新。
  - 失败降级：Brain 读取改用只读连接，只读/缺表不再让聊天崩溃；pending 删除账本读取失败容错。
  - 三态反馈：索引 pending 提示直达事件层与聊天；恢复 embedder 后自动重试 pending。
  - 页面/旧机制：归档页从 Brain 读；合并归档来源、锁定保护、sidecar payload 修复（本轮实机发现并修复）。
- **隔离验收**：完整矩阵见 `docs/tickets/p2/P2-acceptance-matrix.md`。
  - 数据：287 记忆/72 事件/73 阶段/284 版本/171 sidecar/248 向量逐字段 0 mismatch；
    215 条历史非事件记忆逐条核实（140 覆盖/39 墓碑/36 独立/7 悬空）。
  - 桌面：实际 Electron 17/17；备份恢复 16/16；失败 6/6 + 聊天 5/5；连续性 8/8；
    旧机制 10/10 + 经验整理 3/3；切换/回退 19/19。
  - 质量：50 条 A/B（top-8）Brain 29 vs Ombre 16，覆盖率 36.0% vs 22.8%，延迟 187 vs 517 ms；
    8 条无答案最高余弦 0.535、聊天 injected=[]。
- **测试**：`test_brain_*` 78、`test_recall_*` 234、`test_memory_panel` 8、`test_web`+`test_veil_brain` 83 通过。

## 阻塞 / 未做

1. 正式切换未执行：需 Lan 授权；本 PR 不自行合并、不切换。
2. 正式库标签清理未执行：需 Lan 授权维护窗口。
3. 正式 `.env` 的 `EMBED_API_KEY` 当前 401（外部凭据）；隔离验收用本机备份 key，正式切换前必须更新。
4. 记忆页无独立隐藏/取消隐藏按钮：页面改版归 #431；隐藏/恢复在数据层与 Peter 工具层已验证。
5. 黄金集 4/50 条 Ombre-only 残留；总命中/覆盖率/延迟均优于旧系统，正式使用前需 Lan 体验复核。
6. Lan 真实使用体验验收未做（P2-B 第 9 条）。

## 下一步

1. Lan 复审 PR #439、检查隔离证据矩阵。
2. 更新正式 `EMBED_API_KEY`，授权后按 `docs/tickets/p2/P2-B-runbook.md` 做正式切换窗口。
3. 正式体验通过后才进入 P3；不要先进入 P3。
