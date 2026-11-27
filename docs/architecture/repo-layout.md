# VEIL 仓库目录结构与开发导航

> 对应工单：工单 458（仓库目录结构重整）。
> 本次调整只改变代码目录与必要路径引用，不改变功能逻辑、权限边界、记忆系统、提示词内容与本机数据位置。

## 整理前后目录树

### 整理前（根目录约 150 项）

```text
veil-agent/
├─ .agents/  .github/  characters/  desktop/  docker/  docs/
├─ experiments/  persona/  prompts/  recall-lab/  scripts/  tests/  tools/
├─ agent_types.py  autonomy.py  brain_*.py  memory_*.py  ... (104 个 .py)
├─ setup.bat  setup-desktop.bat  start*.bat  start-desktop.vbs
├─ update.bat  update-main.cmd  update-main-collisions.ps1
├─ github-token.cmd  workbench-switch.cmd  xiaohongshu-login.cmd
├─ restore-persona.cmd/.ps1  undo-voice-rules.cmd/.ps1  打开-VEIL隔离预览.cmd
├─ life-calendar-preview.png
├─ requirements*.txt (4)  .env.example  .gitignore  .gitattributes  .coderabbit.yaml
└─ README.md  AGENTS.md  STATUS.md  HANDOFF.md
```

### 整理后（根目录约 35 项）

```text
veil-agent/
├─ veil/                      ← Python 源码唯一入口
│  ├─ __init__.py             包声明（无急切导入，避免循环依赖）
│  ├─ __main__.py             `python -m veil` 终端聊天入口
│  ├─ paths.py                PROJECT_ROOT 唯一定义
│  ├─ core/                   核心运行层（主循环、类型、环境、模型与配置注册）
│  ├─ server/                 本机 HTTP 服务与桌面启动器
│  ├─ prompts/                提示词注册表、覆盖存储与 defaults/（72 份默认文本）
│  ├─ persona/                角色人设版本、用户画像、音色规则、背景库
│  ├─ brain/                  Brain 记忆语义、事件、经历、巡检与 P4/P5 API
│  │  ├─ storage/             brain.db 底层存储、写入锁、权威适配、备份导出
│  │  └─ recall/              检索、判官、召回预算、相关度、轮换、自然联想
│  ├─ autonomy/               自主唤醒、想法、长任务、检查点、委派研究、Codex 观察
│  ├─ life/                   生活状态、记录、日历约定、转账礼物、Lab、Nowhere
│  ├─ chat/                   气泡拆分、聊天检索、附件校验、表情包、语音输出
│  ├─ tools/                  工具注册与权限、分组、别名、审批、桌面文件、工作台
│  ├─ integrations/           网页、浏览器、MCP、GitHub、小红书、QQ、邮件
│  ├─ windows/                Windows 进程、窗口发现、界面交互、观察、桌面图标、弹屏
│  └─ system/                 维护、诊断、冷备份恢复、数据修复、账本恢复、请求快照
├─ desktop/                   Electron 桌面壳与前端测试
├─ tests/                     全量离线单元测试（含 test_repo_layout.py 守卫）
├─ scripts/                   运维、数据脚本与工具子目录
│  ├─ windows/                低频 Windows 双击工具（含独立离线回滚脚本）
│  ├─ dev/                    目录迁移映射表与导入改写脚本
│  └─ workbench-verify/       工作台容器越权验证工具
├─ research/                  研究与实验代码（不参与运行）
│  ├─ experiments/            历史对比实验脚本
│  └─ recall-lab/             召回离线实验框架
├─ docker/                    工作台容器镜像定义
├─ docs/                      项目文档、工单、报告、资产（docs/assets/）
├─ characters/  persona/      角色配置与本机人设（位置不变）
├─ .agents/  .github/         项目 Skill 与 CI 工作流
├─ setup.bat  setup-desktop.bat  start.bat  start-desktop.bat  start-desktop.vbs  start-web.bat
├─ update.bat  update-main.cmd  update-main-collisions.ps1
├─ veil_agent.py  web_agent.py  web_launcher.py  setup_fetch.py  backup_restore.py   ← 根目录过渡兼容入口
├─ requirements*.txt (4)  .env.example  .gitignore  .gitattributes  .coderabbit.yaml
└─ README.md  AGENTS.md  STATUS.md  HANDOFF.md
```

## 根目录过渡兼容入口（5 个）

这 5 个文件只做 `__main__` 转发（不导出模块符号，守卫测试禁止内部代码 import），用于保障旧版 `VEIL.exe`、旧版 `update.bat` 运行中副本、旧快捷方式及已保存命令行在升级过渡期和打包失败时仍能正常启动与恢复：

| 根目录文件 | 转发目标 | 兼容场景 |
| --- | --- | --- |
| `web_agent.py` | `veil.server.web_agent` | 旧 `VEIL.exe` (`backend.cjs`) 目录校验与旧 `web_launcher.py` 启动 |
| `web_launcher.py` | `veil.server.web_launcher` | 旧 `VEIL.exe` (`backend.cjs`) 目录校验与后端启动 |
| `setup_fetch.py` | `veil.integrations.setup_fetch` | 旧 `update.bat` 副本在 `git pull` 之后执行 `setup_fetch.py` |
| `backup_restore.py` | `veil.system.backup_restore` | 旧 `update.bat` 预更新备份及已保存的备份/恢复命令 |
| `veil_agent.py` | `veil.core.veil_agent` | 旧 `start.bat` 及 `--cost` 命令行 |

## 104 个原根目录 Python 模块归属对照

| 目标目录 | 包含模块 |
| --- | --- |
| `veil/core/`（6） | `veil_agent`, `agent_types`, `runtime_env`, `model_settings`, `config_registry`, `capability_state` |
| `veil/server/`（3） | `web_agent`, `web_launcher`, `hub_screen_pop` |
| `veil/prompts/`（1 + 原 `prompts/`） | `instructions` + 原 `prompts/` 全部文件与 `defaults/` |
| `veil/persona/`（5） | `persona_versions`, `user_persona`, `voice_style`, `profile_notes`, `background` |
| `veil/brain/storage/`（13） | `veil_brain`, `brain_writer`, `brain_lock`, `brain_authority`, `brain_authority_init`, `brain_events`, `brain_read`, `brain_store`, `brain_migrate`, `brain_backup`, `brain_export`, `event_migration`, `tag_governance` |
| `veil/brain/recall/`（6） | `brain_retrieval`, `recall_budget`, `recall_judge`, `recall_relevance`, `recall_rotation`, `natural_association` |
| `veil/brain/`（11） | `memory_agent`, `memory_native`, `memory_sidecar`, `event_memory`, `experience_memory`, `short_term_notes`, `learned_context`, `recent_experiences`, `relationship_base`, `brain_health`, `brain_p4_api` |
| `veil/autonomy/`（7） | `autonomy`, `thoughts`, `long_tasks`, `work_checkpoint`, `investigation_checkpoint`, `delegate_agent`, `codex_watch` |
| `veil/life/`（8） | `life_state`, `life_records`, `calendar_agreements`, `virtual_cards`, `lab_games`, `lab_preview`, `lab_sandbox`, `nowhere_integration` |
| `veil/chat/`（5） | `bubble_split`, `chat_search`, `attachment_utils`, `stickers`, `voice_output` |
| `veil/tools/`（11） | `tools_agent`, `tool_access`, `tool_aliases`, `tool_failures`, `tool_groups`, `learned_tools`, `pending_approvals`, `desktop_files`, `executor_opencode`, `workbench_scope`, `workbench_switch` |
| `veil/integrations/`（12） | `web_tools`, `browser_tools`, `mcp_services`, `mcp_hidden_stdio`, `github_readonly`, `github_token_setup`, `xiaohongshu_tools`, `xiaohongshu_setup`, `setup_fetch`, `qq_channel`, `email_channel`, `away_bridge` |
| `veil/windows/`（8） | `windows_process`, `windows_discovery`, `windows_interaction`, `windows_observer`, `desktop_access`, `desktop_icons`, `guest_profiles`, `screen_pop` |
| `veil/system/`（7） | `maintenance`, `diagnostics`, `backup_restore`, `data_repair`, `ledger_recovery`, `request_snapshots`, `runtime_monitor` |
| `scripts/`（1） | `simplify_memory`（已退役 Ombre 的一次性清理脚本） |

其他整理项：
- `github-token.cmd`、`workbench-switch.cmd`、`xiaohongshu-login.cmd`、`restore-persona.*`、`undo-voice-rules.*`、`打开-VEIL隔离预览.cmd` → `scripts/windows/`
- `tools/workbench-verify/` → `scripts/workbench-verify/`
- `experiments/`、`recall-lab/` → `research/experiments/`、`research/recall-lab/`
- `life-calendar-preview.png` → `docs/assets/life-calendar-preview.png`

## 独立离线回滚与重新打包流程

如需将本机目录回退到历史提交，**不要使用 `update.bat` 或 `update-main.cmd`**（它们会拉取最新 `main`）。使用独立离线脚本：

```powershell
.\scripts\windows\rollback-version.cmd <目标提交或标签>
```

该脚本的行为：
1. 确认 `VEIL.exe` 已退出且 `data\session.lock` 未被占用。
2. 校验目标提交在本地存在，**绝不执行 `git fetch` 或 `git pull`**。
3. 自动创建本地恢复分支 `backup/before-rollback-<时间戳>` 保存当前 `HEAD`。
4. 将工作区切回目标提交（不触碰 `data/`、`characters/`、`persona/`、`.env`）。
5. 本地重新执行 `npm run package` 重建 `VEIL.exe` 并更新 `desktop\dist\built-from.txt`。

## 并行开发分支迁移工具

在旧平铺目录上开发的分支（如 `feat/p4-p5-brain-rework`）合并或变基后，可运行同一套映射脚本自动改写其 import 与 `mock.patch` 路径：

```powershell
.venv\Scripts\python.exe scripts/dev/migrate_layout.py --all --rewrite
.venv\Scripts\python.exe scripts/dev/migrate_layout.py --all --check
```
