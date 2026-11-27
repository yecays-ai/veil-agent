# HANDOFF

> 更新时间：2026-11-27T14:10:00+08:00 · 基线 `main` = `2a264a7` · 当前分支 `feat/p4-p5-brain-rework`（PR #459 已按独立复审 P0–P3 集中修复并补充 12 项后端 + 15 项前端回归测试，待 CI 与人工验收；未合并、未部署正式目录）

## 当前停点

- **本单（PR #459 · P4/P5 记忆中心返工与复审修复）**：
  - 已合并 `origin/main`（含 PR #458 `veil/` 新目录结构），改动落位在 `veil/brain/brain_p4_api.py`、`veil/server/web_agent.py`、`desktop/workspace-brain.js`、`desktop/workspace-shell.css`、`desktop/workspace-shell.js`、`desktop/peter-page.js`、`desktop/main.cjs`。
  - **P0 修复**：`growth_analysis` 区分累计创建量、活跃存量、归档/删除存量与活跃净增长（`added + restored - archived - deleted`），排除失败/未完成事务并标记不完整历史；`recall_turns` 不再将缺失缓存显示为 `0`、区分召回证据缺失/未执行/未命中/命中、不再推测未入选原因、修复 `costs.jsonl` 中 `type=settle` 的 `amount` 结算统计，并明确标注 Prompt 分区估算来源。
  - **P1 修复**：`generate_growth_report`（`full` 模式）取消 20 条/600 字符静默截断，补齐修改前/后内容、Diff、事件 ID/阶段变化与仅事件更新日明细，关闭正文时保留非正文技术明细；`recall_turns` 单遍索引支持全量分页与多步去重，`recall_turn_report` 按 `request_id` 直查；健康报告合并严格校验日期范围匹配并读取 `warn_count`/`error_count` 与 `incomplete`。
  - **P2/P3 修复**：六层开关持久化落盘（`brain-layer-switches.json` / `experience-memory.json`）并对未就绪模块返回真实错误；`peter-page.js` 与 `workspace-brain.js` 共享后端 `_compute_cognition_budget` 同源预算；清理 `_legacyRenderRecallUnused`；消除 `brain_p4_api.py` 动态 SQL 拼接（改为静态白名单查询）；新增 4 组专项回归测试（共 12 项 Python + 15 项 Node 测试全通过）。
- **状态**：已推送 `feat/p4-p5-brain-rework`，等待 CI 与人工验收，不自行合并。

## 下一个窗口第一步

1. 待本单 PR 合并后，若继续 `feat/p4-p5-brain-rework`，先在该分支的隔离副本运行 `.venv\Scripts\python.exe scripts/dev/migrate_layout.py --all --check`，确认无目标冲突后再运行 `--all --move --rewrite`；工具会先改写、再移动、生成兼容入口，并在失败时恢复原文件。
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
