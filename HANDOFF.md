# HANDOFF

## 当前工单 319（PR #318，2026-11-09 10:55 +08）

- 分支 `ticket/318-model-config-concurrency`，PR #318（草稿）：https://github.com/yecays-ai/veil-agent/pull/318。独立 worktree：`.agents/worktrees/ticket-318-model-config-concurrency`；正式运行目录未动。
- 工单 318（普通聊天调查续接，PR #317）已合并为 `19b8d5b`；本分支已同步该 `main`，解决 `STATUS.md`、`HANDOFF.md` 冲突，`veil_agent.py` 等代码自动合并。本单实际编号为 319，PR 编号仍是 318。
- 问题确认：`ModelSettings.__init__` 原先按文件锁 → `_ENV_LOCK` 获取；`Hub.save_models` / `use_model` 先持有 `models.env_lock` 和 `models.lock`，再进入 `save()` / `use()` 取文件锁，存在循环等待。
- 修复：统一 `_ENV_LOCK` → `self.lock` → `models.json` 文件锁；初始化、保存、选模和重新加载同序。新增 `ModelSettings.reload()`，重读磁盘最新配置、刷新内存快照和旧快照标识。
- 恢复路径：旧实例保存/选模发现“另一个进程修改”时自动 `reload()` 并刷新模型视图；桌面初始事件快照在空闲时重载模型设置。刷新页面即可继续编辑保存，不需要重启 Peter。重载后聊天模型变化会通过 `switch_model` 同步到下一次聊天请求。
- 测试：`test_model_settings` 41 项通过（含旧实例重载、并发初始化/保存/选模、重载后 provider 同步）；`test_web` 67 项通过。未重跑整套测试。
- 下一个窗口第一步：推送合并提交，等 PR #318 最新 CI 全绿并确认分支保护后合并；合并后请 Lan 按 `STATUS.md` 做 Windows 双实例验收。
- 未验证：真实 Windows 双实例桌面操作。数据安全：模型、API Key、费用上限不丢失；迁移备份和原子写保持不变。
