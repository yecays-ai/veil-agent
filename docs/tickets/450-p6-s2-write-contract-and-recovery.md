# P6-S2｜统一写入事务、防丢稿、证据链与协同恢复

- **产品格**：B（长期记忆与共同经历连续性）。
- **做完后 Lan 能多做的一件事**：经历整理或恢复中断后，Peter 的事件故事、原话证据和记忆正文不会静默丢失或互相错位。
- **体验验收**：维护窗口在隔离/备份保护下恢复目标经历后，时间线能看到 11 月 25 日白天到晚上的连续阶段，且重复执行不会新增重复阶段。

## 目标

把事件、阶段、记忆正文、单份版本、证据与摘要收口到同一事务；将经历整理失败转入可审队列；提供包含伴随状态的协同备份恢复，并完成目标历史失败批次的隔离幂等恢复预演。

## 当前状态

- `BrainAuthority.commit_event_bundle()` 已接入 `EventMemory.record()` / `revise()` 生产路径，同事务提交 memory、version、event、stage、evidence、quotes、write journal 与 canonical digest。
- 日期事实等价、事实核对待审降级、未修复草稿 `manual-review`、用户原话证据配额和 `_merge_many` 已修复。
- `brain_backup` 已扩展协同 bundle，并在 T0→T1 恢复时保留新对象和既有对象的新正文、版本、阶段、关系、quotes 与 ledger。
- `scripts/p6_s2_recover_history.py` 支持全量失败审计、默认 dry-run、锁内单事务 apply 和 `source_key` 幂等；仅在隔离副本完成目标 58 条消息恢复演练，未写正式数据。

## 验收步骤

1. 在维护窗口先创建协同 bundle，再对目标批次运行不带 `--apply` 的恢复预览，确认目标 memory/event、58 条消息和追加内容。
2. Lan 确认后才可对正式副本执行 `--apply`；在记忆详情和时间线检查 11 月 25 日阶段已连续补回，原有后续故事仍保留。
3. 再执行一次相同命令，应返回 `already_recovered`；运行 bundle 对账应为 0 重复、0 悬空引用。
