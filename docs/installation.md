# 安装与启动

> 适用于 Windows 10/11。安装只下载软件与依赖，不调用模型、不产生对话费用。

## 1. 前置

| 依赖 | 版本 | 用途 |
| --- | --- | --- |
| Python | 3.11 或 3.12 | 主程序与离线测试（其他版本不在支持范围） |
| Node.js | 24 LTS | 桌面程序构建与运行 |
| Git for Windows | 任意近期版本 | 拉取代码与一键更新 |

安装 Python 时不要用 Microsoft Store 的启动占位；装完在命令行确认 `python --version`。

项目可以放在任意可写目录（包括 E 盘）。启动器会先切到自己所在文件夹，`.venv/`、`data/`、`persona/`、`.env` 都跟着项目走；安装临时文件也放在项目内 `.local-tmp/`。

## 2. 安装

```powershell
git clone https://github.com/yecays-ai/veil-agent.git
cd veil-agent
.\setup.bat            # 按 requirements.lock.txt 装固定版本依赖到 .venv
.\setup-desktop.bat    # 安装桌面依赖并更新快捷方式（不打包 VEIL.exe）
```

- `setup.bat` 需要联网；它只更新 `.venv` 依赖和文件工具，不安装本地大模型，也不会自动下载模型。
- 项目更新后需要重新运行 `setup.bat`，聊天、人设与记忆数据会保留。
- 文件工具依赖 `@modelcontextprotocol/server-filesystem`，由 `setup.bat` 装到 `.deps/filesystem/`；安装失败时聊天、记忆仍可用，网络恢复后重跑即可。

## 3. 两份本机私有文件

仓库里只有示例，程序不会替你创建或覆盖这两份文件：

**`.env`** —— 由 `.env.example` 复制：

```ini
DEEPSEEK_API_KEY=你自己的模型密钥
EMBED_API_KEY=你的硅基流动密钥      # 记忆向量召回用
```

**`persona/peter.md`** —— 由 `persona/peter.example.md` 复制后改成你要的人设。人设缺失或为空时程序会直接停止，不会用默认设定顶替。

> 密钥只从项目 `.env` 读取，不读系统环境变量，也不写进日志。人设与密钥都不进仓库。

## 4. 启动

| 方式 | 命令 | 说明 |
| --- | --- | --- |
| 桌面版（推荐） | 双击 `start-desktop.vbs` | 无命令窗口；桌宠 + 聊天窗 |
| 终端版 | 双击 `start.bat` | 纯文字聊天，与桌面版共用同一套代码和数据 |

桌面版与终端版共用聊天锁，同一时间只能开一个。聊天、记忆、花费和命令在两种入口下完全一致。

## 5. 更新

- **日常更新**：退出 Peter，双击 `update.bat`。它会先做更新前备份，再拉新代码、补装 Python 依赖与文件工具、桌面代码有变化时重新打包 `VEIL.exe`，然后启动 Peter。
- **正式目录更新**：退出 Peter，双击 `update-main.cmd`。它会检查 GitHub 连接、暂存本地改动并把未推送的本地 `main` 保留成恢复分支、同步 `origin/main`、检查 Nowhere 运行时并执行 `npm run package` 重新打包 `VEIL.exe`；失败时保留现场不乱改 `main`。注意：`update-main.cmd` **不**运行 `pip install` 也不自动启动 Peter；若 Python 依赖有变动，请先运行 `setup.bat` 或改用 `update.bat`。
- 只运行 `git pull` 不会更新桌面程序。

更新脚本不会 stash 或丢弃你的数据；`data/`、`persona/`、`.env` 不受影响。项目换目录后需要重建 `.venv`，并保留 `data`、`.env`、`persona`。

## 6. 模型与预算

默认角色配置在 `characters/peter/character.toml`，把 `provider = "deepseek"` 改成 `"gpt"` 会同时切换模型、接口、密钥名称与价格配置（GPT 需在 `.env` 填 `OPENAI_API_KEY`）。

更常用的做法是在桌面里改：**更多 → 模型**，添加中转站接口与模型，给「Peter 本人」「记忆后台」「自主活动」「委派」「瞄一眼」分别选模型，顶栏下拉随时切换。这些选择存在本机 `data/<角色>/models.json`，密钥写进本机 `.env`，都不进仓库。

预算默认日/月双上限，可在「预算」页调整基础限额；所有角色合计。费用按配置单价在本机核算，是估算而不是供应商账单。

## 7. 常见问题

| 现象 | 处理 |
| --- | --- |
| 启动即停止，提示人设缺失 | 确认 `persona/peter.md` 存在且非空 |
| 记忆检索降级为关键词 | 检查 `.env` 的 `EMBED_API_KEY` 是否有效 |
| 桌面打不开或版本没变 | 先完全退出 Peter，再运行 `update.bat` |
| 备份/恢复 | 见 [`backup-and-restore.md`](backup-and-restore.md) |
| 数据损坏或账本对不上 | 见 [`data-repair-and-ledger.md`](data-repair-and-ledger.md) |

## 8. 离线自检

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试只用假人设、假消息、假模型或本机模拟 HTTP，不读真实密钥、不访问付费接口。CI 在 Windows/Linux × Python 3.11/3.12 跑同一套测试；这些检查不代替真机体感验收。
