# HANDOFF

> 更新：2026-11-27 +08:00 · 工单 459 / PR #460 · 复审基线 `de2cc06`，接入 `main` `2b03006`；未部署。

## 当前停点

- **本单（工单 459 · P6-S3 关系底座解耦、证据链晋升与认知治理）**：
  - `veil/brain/learned_context.py`：实现四区独立 UTF-8 预算（`facts 1800 / growth 2000 / relationship 1500 / ongoing 900 B`，合计 6200B），读校验与容量校验解耦（超限分区进入 `capacity_degraded` 且不影响读写其他分区），无损幂等迁移（`migrate_storage(dry_run=True/False)` + `rollback_migration`）。
  - 统一严格证据核验入口（`_evaluate_sources`）：支持 `chat:` 与经 `brain.db` `event_ledger(stage-evidence, chat, user)` / `quotes(speaker='user')` 追溯的 `event:`/`memory:` 间接晋升，含同源证据去重、内容支持性核验、Lan 否决防复活、`stale_ref` 失效检测与模型认知安全降级。
  - 用户主权与写入一致性：`locked` 锁定保护、删除/否决防复活（`vetoed`）、历史条目恢复（`restore_relationship_item`）、`_commit_with_version` 正文与版本支持进程中断恢复，按角色与写入及备份互斥。
  - `veil/brain/relationship_base.py` & `veil/core/veil_agent.py`：`locked` 最高优先、预算不足时省略整条并说明原因；隔离失效认知和跨区冲突、`debug['relationship_base']` 区分存储/有效/选中/实际注入。
  - `veil/tools/learned_tools.py`、`veil/prompts/defaults/`、`veil/brain/brain_p4_api.py`、`veil/server/web_agent.py`、`desktop/peter-page.js`、`desktop/workspace-brain.js`、`desktop/chat.js` 同步接入四区预算、锁定/恢复/核验与诊断展示。
  - 收口：只允许恢复历史条目，同一版本链只保留一条当前认知；重复恢复不再写正文或版本日志。GET `/api/peter-page` 使用现有 Host、Origin、Token 防护。
  - 收口前 `d5f8591` 的 GitHub 检查为 14 通过、1 跳过；收口提交查对应的新运行。开发侧联合回归 128 项、桌面 Node 56 项通过，Lan 真机体验未验收。
  - 旧本机失败对照 `2b03006` / `d5f8591` 均复现：Maintenance 在 CRLF 测试文件的暂存区检查失败（改 LF 后通过）；OpenCode 原用例缺输出文件，GitHub CI 因未安装而跳过；冷备份仅在 APPDATA 有桌面配置时增加 `desktop-data`，空 APPDATA 的两个用例均通过。本轮不改这些模块。
  - 未触碰正式运行目录 `veil-agent/` 及正式 `data/peter`。

## 下一个窗口第一步

1. 复核 PR #460 收口提交与对应 CI；不要合并或部署。S6 接入前读[证据与否决的能力边界](docs/tickets/459-p6-s3-relationship-governance.md#供-s6-使用的能力边界)。
2. 读 `docs/ACCEPTANCE.md`，按 Lan 体验结果更新验收勾选并推进 P6-S3/S5/S6/S7。
3. 三项待授权操作（B-3 正式历史恢复、B-4 换 Embedding 重建、H-1 QQ 邮箱）只在 Lan 明确同意后做，且先 dry-run。

## 环境与工具注意

- 正式目录 `.git` 为写保护，**不要在正式目录开分支、提交或改文件**；开发一律在独立 worktree / 副本里做。
- 本机 `git` 走 HTTPS 拉取时可能因 schannel 凭据不可用而失败；本单改用 `gh api` 推送分支，未改任何全局 git 配置。
- 本机 Node 24 用于桌面构建；Python 3.11/3.12 跑测试。

## 红线（不变）

- 不启停正式 Peter、不写正式 `data/peter`、不向正式 Peter 发消息。
- 正式恢复必须先做协同 bundle + dry-run，经 Lan 确认后才 `--apply`。
- 换 Embedding 模型必须用 `scripts/embedding_reindex.py`（先 dry-run 再 `--apply`），不依赖后台重试。
- 不提交人设正文、密钥、真实聊天/记忆、本机绝对路径、日志。
