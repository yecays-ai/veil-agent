# HANDOFF

> 更新时间：2026-11-27T11:30:00+08:00 · 基线 `main` = `d78a8b7` · 当前分支 `ticket/458-repo-layout`（已按 F1–F11 集中修复，待独立复审；未合并、未部署正式目录）

## 当前停点

- **本单（工单 458 · 仓库目录结构重整）**：
  - 104 个原根目录 Python 模块与 `prompts/` 已分批迁入 `veil/`（`core`、`server`、`prompts`、`persona`、`brain/{storage,recall}`、`autonomy`、`life`、`chat`、`tools`、`integrations`、`windows`、`system`）。
  - 根目录保留 7 个过渡兼容入口（`web_agent.py`、`web_launcher.py`、`setup_fetch.py`、`backup_restore.py`、`veil_agent.py`、`data_repair.py`、`veil_brain.py`）；所有入口改为导入真实模块后调用 `main()`，不再经 `runpy` 重复加载。
  - 低频双击脚本移入 `scripts/windows/`，工作台验证移入 `scripts/workbench-verify/`，实验目录并入 `research/`，新增独立离线回滚与重打包脚本 `scripts/windows/rollback-version.cmd`（绝不执行 `git fetch`/`git pull`）。
  - 守卫测试 `tests/test_repo_layout.py` 启用严格模式；完整对照文档见 `docs/architecture/repo-layout.md`。
  - 回滚脚本已加固：切换前检查所有未跟踪/被忽略文件（含父子目录）冲突，默认离线，依赖不足即中止，`-AllowOnlineDeps` 才联网，打包失败恢复旧 `desktop\dist`。
  - 迁移工具已修复 `prompts` 包冲突、半迁移、兼容入口生成和 CRLF 伪 diff；在含未提交修改的 P4/P5 临时副本上验证通过。
  - 隔离升级/回滚演练证据见 [`docs/reports/pr458-isolation-drill-20261127.md`](docs/reports/pr458-isolation-drill-20261127.md) 与同名 JSON；只使用合成数据。
  - 未修改功能逻辑、权限、记忆算法与提示词正文；未触碰正式运行目录及 `worktrees/p4-p5-brain-rework`。
- **状态**：F1–F11 集中修复完成，隔离演练通过，待推送 CI 与独立复审；未合并、未部署。

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
