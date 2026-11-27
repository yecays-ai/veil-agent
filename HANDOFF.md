# HANDOFF

> 更新时间：2026-11-27T07:20:00+08:00 · `main` = `95dc26a` · 正式目录已同版本部署

## 当前停点

- **本单（纯文档维护）**：统一了待验收归集（`docs/ACCEPTANCE.md`）、状态对齐（`STATUS.md`）、首页重构（`README.md` + `docs/installation.md`、`docs/usage.md`、`docs/README.md`）、施工规则同步（`AGENTS.md`、八个 Skill、PR 模板）。
- 只改 Markdown，未改代码、测试、CI、审查平台配置；未启停正式 Peter；未写正式数据。
- **状态：已提交 PR，停下等 Lan 确认，不自行合并。**
- 进度与近期工单/部署事实统一看 [`STATUS.md`](STATUS.md)，待验收项统一看 [`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md)；工单号 ≠ PR 号，引用时必须写全（例如「工单 451 · PR #456」）。

## 下一个窗口第一步

1. 读 `docs/ACCEPTANCE.md`，等 Lan 勾选或口头确认；把通过项在清单里打勾、写日期，并回填对应工单。
2. Lan 拍板 P6-S3/S5/S6/S7 方向后，从 `docs/reports/p6-s4-decision-table-20261126.md` 取第一张工单范围。
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
