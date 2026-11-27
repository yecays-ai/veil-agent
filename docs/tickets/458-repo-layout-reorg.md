# 工单 458：VEIL 仓库目录结构重整

- 属于哪一格：工程底座（服务 A/B/C/V/D 全部线的可读性、模块定位与长期维护）
- 做完 lan 能多做的一件事：打开 GitHub 仓库或本地目录时一眼看到清晰的功能模块分区，且升级/回滚过程更安全、本机数据与配置完全无需手动迁移。
- 她怎么验收：查看整理前后目录树与对照表；按 `docs/ACCEPTANCE.md` 在隔离预览或更新后的桌面版确认启动、聊天、记忆读取、自主活动、工具权限正常，且原有数据原位保留。

## 目标

将根目录平铺的 104 个 Python 模块及辅助脚本按功能领域归入统一 `veil/` 源码包、`scripts/`、`research/`，根目录只保留入口、配置、依赖、高频启动/安装/更新脚本、过渡兼容入口与核心文档；同步完成全部路径适配、独立离线回滚脚本、守卫测试与隔离环境全链路验证。

## 当前状态

- 分支：`ticket/458-repo-layout`，基于 `main` (`d78a8b7`) 在独立 worktree `worktrees/458-repo-layout` 施工；正式运行目录与 `worktrees/p4-p5-brain-rework` 全程未触碰。
- 完整架构说明、前后目录树、104 模块归属对照、5 个兼容入口与离线回滚流程见 [`docs/architecture/repo-layout.md`](../architecture/repo-layout.md)。
- 提供 `scripts/dev/migrate_layout.py` + `scripts/dev/module_map.json` 供并行分支（如 `feat/p4-p5-brain-rework`）自动改写 import。
- 守卫测试 `tests/test_repo_layout.py` 强制禁止根目录新增平铺 `.py`、禁止遗留旧顶层 import、禁止离线回滚脚本包含 `git fetch`/`git pull`。

## 验收步骤

1. 打开 GitHub 仓库首页与 `docs/architecture/repo-layout.md`，确认根目录简洁、模块分类与对照清晰。
2. 合并后在隔离副本或更新后的桌面版启动 Peter，确认聊天、记忆页/事件页读取、工具权限和自主活动行为与改前一致，原有 `data/`、`characters/`、`persona/`、`.env` 无需任何手动迁移。
3. 如需回退验证，可在隔离副本运行 `scripts/windows/rollback-version.cmd <旧提交>`，确认离线切回并重打 `VEIL.exe` 且不触发 `git pull`。
