# HANDOFF

## 当前工单 318（2026-11-09 10:35 +08）

- 分支 `ticket/318-model-config-concurrency`，从最新 `main`（`3af0092`，工单 317/PR #316 已合并）建立；PR #318（草稿）：https://github.com/yecays-ai/veil-agent/pull/318。独立 worktree：`.agents/worktrees/ticket-318-model-config-concurrency`；未修改正式运行目录。
- 问题确认：`ModelSettings.__init__` 原先按文件锁 → `_ENV_LOCK` 获取；`Hub.save_models` / `use_model` 先持有 `models.env_lock` 和 `models.lock`，再进入 `save()` / `use()` 取文件锁，存在循环等待。
- 修复：统一 `_ENV_LOCK` → `self.lock` → `models.json` 文件锁；初始化、保存、选模和重新加载同序。新增 `ModelSettings.reload()`，重读磁盘最新配置、刷新内存快照和旧快照标识。
- 恢复路径：旧实例保存/选模发现“另一个进程修改”时自动 `reload()` 并刷新模型视图；桌面初始事件快照在空闲时重载模型设置。刷新页面即可继续编辑保存，不需要重启 Peter。
- 测试：`test_model_settings` 40 项通过（含旧实例重载、并发初始化/保存/选模）；`test_web` 67 项通过。未重跑整套测试。
- 下一个窗口第一步：等 PR #318 最新 CI，请 Lan 按 `STATUS.md` 的 Windows 双实例步骤验收。**不得自行合并**。
- 未验证：真实 Windows 双实例桌面操作。数据安全：模型、API Key、费用上限不丢失；迁移备份和原子写保持不变。
