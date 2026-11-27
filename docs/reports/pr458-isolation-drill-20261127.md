# PR #458 · 隔离升级/回滚与复审修复验证（脱敏）

> 生成时间：2026-11-27 · 分支：`ticket/458-repo-layout` · 未合并、未部署
> 本报告只使用临时克隆和合成数据；没有读取或修改正式 `data/peter`、真实聊天/记忆、人设正文或密钥。文中不记录本机绝对路径。

## 1. 隔离升级与回滚演练

脚本：[`scripts/dev/verify_layout_drill.py`](../../scripts/dev/verify_layout_drill.py)

命令（在仓库根目录、无正式 Peter 运行时）：

```powershell
python scripts/dev/verify_layout_drill.py --old d78a8b7 --new HEAD --out docs/reports/pr458-isolation-drill-20261127.json
```

脚本行为：

1. 把仓库克隆到系统临时目录（完整本地对象，不联网）。
2. 在旧布局 `d78a8b7` 上写入 5 个合成文件：`data/peter/costs.jsonl`、`data/peter/chat.jsonl`、`data/peter/brain.db`、`persona/peter.md`、`.env`。
3. 原地切换到新版提交，验证新布局存在、7 个入口可执行、合成数据哈希不变。
4. 运行真实 `scripts/rollback_version.ps1` 回滚到旧提交，验证数据哈希不变、旧布局恢复。
5. 切回新版，制造一个未跟踪的 `restore-persona.cmd`（旧提交跟踪该路径），验证回滚被安全中止、HEAD 不变、文件内容不变。

脱敏结果（完整 JSON 见 [`pr458-isolation-drill-20261127.json`](pr458-isolation-drill-20261127.json)）：

| 检查 | 结果 |
|---|---|
| 旧提交 | `d78a8b7bc1af4c60e99f67880bf809df3fd3e314` |
| 新版提交 | 见 JSON `new_commit` |
| 合成文件数 | 5 |
| 升级前后合成数据哈希 | 相同（`upgrade_data_unchanged=true`） |
| 新布局存在 | `veil/server/web_agent.py` 存在 |
| 7 个入口 | `python -m veil`、`data_repair.py`、`veil_brain.py`、`web_agent.py`、`web_launcher.py`、`backup_restore.py`、`veil_agent.py` 全部返回 0 |
| 回滚退出码 | 0 |
| 回滚后 HEAD | 旧提交（`rollback_ok=true`） |
| 回滚前后数据哈希 | 相同（`rollback_data_unchanged=true`） |
| 旧布局恢复 | 是（`old_layout_restored=true`） |
| 未跟踪文件冲突 | 回滚退出码 1（`conflict_guard_aborted=true`），HEAD 不变，文件内容不变 |

## 2. 回滚安全检查（临时 git 仓库）

| 场景 | 结果 |
|---|---|
| 未跟踪文件与目标提交**同路径**（`restore-persona.cmd`） | 中止，HEAD 不变，文件内容不变 |
| 未跟踪**目录**与目标提交文件同路径（`restore-persona.cmd/child`） | 中止，HEAD 不变，目录内容不变 |
| 被忽略文件（`.env`）与目标提交同路径 | 中止，HEAD 不变，文件内容不变 |
| 目标提交 `requirements.lock.txt` 有变化、`.venv` 缺失 | 默认离线中止；不会先切换版本 |
| 传入 `-AllowOnlineDeps` 但 `.venv` 缺失 | 中止并提示先运行 `setup.bat`，不切换版本 |
| 模拟打包失败、已有 `desktop/dist` | 旧 `VEIL.exe` 被恢复到原位置 |

## 3. P4/P5 未提交分支迁移演练

在 `worktrees/p4-p5-brain-rework` 的临时副本（含未提交的 `brain_p4_api.py`、`web_agent.py` 修改）上运行：

```powershell
python scripts/dev/migrate_layout.py --all --move --rewrite
```

结果：

- `REWRITTEN: 286 files`
- `MOVED: 112 files`
- 创建 7 个根兼容入口，包含 `data_repair.py`、`veil_brain.py`。
- `brain_p4_api.py` 与 `web_agent.py` 的未提交修改保留；除 import 行外，正文差异为 0。
- 文件保持原 LF 换行（迁移工具不再在 Windows 上制造 CRLF 伪 diff）。
- `prompts/` 包先于 `instructions.py` 移动，不再出现目标 `__init__.py` 冲突。

## 4. 自动化测试

- 代码修复后全量 Python 套件：`2,359` 个用例（原 2,351 + 新增 8 个入口/迁移/recall-lab 测试）。
- 本机结果：18 failures / 4 errors / 29 skipped，全部集中在既有环境依赖测试：真实 DOCX/PDF、真实 OpenCode、`test_maintenance` 的 git/gh 环境、本机 `%APPDATA%` 桌面设置；与本次目录/回滚修复无关。CI 在干净矩阵中运行全量套件。
- 定向通过：`test_repo_layout` 5/5、`test_entrypoints` 4/4、`test_migrate_layout` 3/3、`test_prompt_registry` 32/32、`test_recall_lab` 41/41、桌面后端 9/9、桌面全套 56/56、`test_ledger_recovery` 全部通过。

## 5. 边界

- 真实 `data/`、`persona/`、`characters/`、`.env`、`data/peter/brain.db` 未参与演练，也未包含在报告中。
- 演练临时目录由脚本自动删除；JSON 只保留提交号、合成数据哈希和布尔结果。
