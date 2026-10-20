# 工单 024：Ombre 完整版常驻（4B-3）

## 目标

Peter 的记忆服务 Ombre 改成完整版常驻启动：lan 能打开 Ombre 自带的记忆面板，浏览、置顶、归档 Peter 的记忆；记忆会按 Ombre 自己的规则自然衰减。聊天、自主活动、lab 照旧，Peter 能用的记忆工具不变（`hold` + `breath_search`）。

做完后 lan 每天能做到的：直接在面板里看到 Peter 记住了什么、哪些重要、哪些在淡去，不用再靠 `/memory` 命令。

依据：[重建计划](https://claude.ai/code/artifact/85533c78-6925-4ad3-842c-4fe446685695) 4B-3「Ombre 完整版」（lan 2026-10-20 定在自主活动之后）。

## 当前状态（main `695c58a`，读源码确认）

- `memory_agent.py:166` 用 stdio 方式启动 Ombre（锁定版本 `115d831`，`setup_memory.py:11`）。每次聊天进程启动时开一个 Ombre 子进程，结束就关。
- Ombre 在 stdio 模式下**不启动记忆面板，也不跑衰减**：`src/server.py` 只有 `streamable-http` 分支会把 `decay_engine` 交给 `RuntimeLifecycle` 并用 uvicorn 起 HTTP 服务；stdio 分支只起向量队列。
- Ombre 完整版（`transport: streamable-http`）在同一个端口提供：`/mcp`（记忆工具）、`/dashboard`（面板，首次打开设密码，或用 `OMBRE_DASHBOARD_PASSWORD`）、`/health`。端口来自 `OMBRE_PORT`（默认 18001），监听地址来自 `OMBRE_BIND_HOST`（默认 `0.0.0.0`）。`/mcp` 鉴权由 `mcp_require_auth` / `mcp_auth_mode` 决定，支持静态 Token（`auth_mode = "token"`）。
- 衰减引擎（`decay_engine.py`）按 `decay.check_interval_hours` 定时跑；会顺带补算缺失的向量（调用 SiliconFlow 向量接口，有少量费用）。
- 常驻网页服务（020）已有：无窗口后台进程、启动互斥、`data/web_service.json` 记地址、「退出服务」。

## 复用与面板费用核对

- 复用已安装的 [Ombre-Brain `115d831`](https://github.com/P0luz/Ombre-Brain/tree/115d831d979bf67efc7150f976e2cb34c333c935)：MIT 许可，仓库通过固定提交和归档哈希安装；该版本已有 Streamable HTTP、`/dashboard`、衰减生命周期和 MCP Token 鉴权。本单不复制或改写 Ombre 实现，只用其 MCP HTTP 客户端和运行入口。
- Ombre 面板的历史导入/需要模型标注的导入、脱水模型连通性测试、向量连通性测试，以及编辑记忆正文后重建向量，可能直接请求模型或向量服务；这些调用不进入 veil-agent `/cost`。普通浏览、置顶、归档不调用模型。VEIL 嵌入方式禁用模型配置和外部服务开关，保留首次设置/修改 Ombre 面板密码。
- 补缺失向量仍按本工单预计每天不到 ¥0.01；没有在本机真实记忆上启动服务或测量实际调用，具体用量待 Windows 体验确认。

## 要做的事

1. **Ombre 常驻**：网页服务启动时，若该角色的 Ombre 没在运行，就以 `streamable-http` 方式在后台启动它（同样无黑窗口）；已在运行就直接连。「退出服务」时先让聊天和记忆保存完，再关 Ombre。异常退出后再次启动能直接起来。终端 `start.bat` 也连同一个常驻 Ombre，不再自己开 stdio。
2. **同一记忆目录只允许一个 Ombre 进程**：用程序锁保证，不允许 stdio 和 HTTP 同时指向同一个 `data/<角色>/memory`。两个进程同时写一个目录可能把记忆写坏。
3. **只在本机能访问**：`OMBRE_BIND_HOST=127.0.0.1`；`/mcp` 开静态 Token 鉴权，Token 本机生成、存在 `data/<角色>/memory/_agent/` 下，不进仓库、日志或网页响应。端口写在角色配置里，Peter 和以后的 Ghost 各用各的端口、各自的记忆目录。
4. **关掉不需要的功能**：GitHub 同步、隧道（tunnel）、自动更新（`/api/do-update`）、本地 Ollama 子进程、webhook 一律关闭。Ombre 版本仍锁在 `115d831`。
5. **veil-agent 改用 HTTP 连接**：用 `mcp` 库现成的 streamable HTTP 客户端替换 stdio 连接。Peter 能调用的记忆工具、隐藏/删除逻辑、`/memory` `/forget` `/restore` `/delete`、记忆费用记账全部保持现状。
6. **记忆面板入口**：聊天页面加一个「记忆面板」按钮，在浏览器打开 `http://127.0.0.1:<端口>/dashboard`。首次打开由 Ombre 自己引导设密码。
7. **面板里会花钱的功能**：施工时查清面板里哪些操作会调用模型（例如导入历史对话、改模型配置），这些不经过 veil-agent 的预算。查清后在 PR 和汇报里写一句列出来，本单不改它们。
8. **数据安全**：第一次以新方式启动前，把 `data/<角色>/memory` 整个复制一份备份（带时间戳），不迁移、不改记忆格式。
9. **Peter 感知**：按惯例在 `capabilities.json` 记一次能力变化：他的记忆现在会随时间自然淡去，lan 能在面板里看到和整理他的记忆。只提示一次。

不改：Peter 的记忆工具种类、聊天、自主活动、lab、预算上限、人设。

## 预计费用（估算，未实测）

衰减本身不调用聊天模型；补算向量每次只算缺失的，按 SiliconFlow bge-m3 价格估计每天不到 ¥0.01。面板只浏览不花钱。若施工发现衰减或面板默认会调用聊天模型，先停下告诉 lan，写明每天大约多花多少。

## 施工规矩（Codex 必读）

lan 上一单等了三个小时才能验收。这一单按下面做：

1. **只做本单写的事。** 不开新工单、新分支、新文档；不顺手重构、不改无关文件；`STATUS.md`、`HANDOFF.md` 只改本单相关的段落，几行写完。
2. **测试只跑必要的。** 写本单直接相关的测试，提交前全量离线测试跑一次即可。不反复跑全套，不为凑覆盖率写测试，不调真实模型或付费接口。
3. **不在 lan 的电脑上做实验。** 不让 lan 帮你跑命令、贴日志、反复重启来排查。需要她电脑上的信息，一次列清楚问完。
4. **交给 lan 前自己走一遍。** 双击 `start-web.bat` → 聊一句 → 打开记忆面板 → 「退出服务」 → 再双击启动，确认都能用。入口打不开、找不到依赖的版本不能交给她。
5. **评审只处理会出错的问题。** 小兔子限流超过 15 分钟不等，CI 通过就告诉 lan，由 Claude 代审后合并。
6. **卡住就停。** 同一个问题修两次还坏，或者做了一小时还没完：停下来，用两三句话告诉 lan 卡在哪、要她决定什么。
7. **汇报一条，十行以内，中文。** 写：做了什么、她要验收的步骤、哪些没验证。不贴过程、不贴测试清单。

## 验收步骤（Windows）

1. 更新 main，在页面点「退出服务」，再双击 `start-web.bat`：没有黑窗口，能正常聊天；`data/peter/memory` 旁边多了一份备份。
2. 点「记忆面板」：浏览器打开 Ombre 面板，设好密码后能看到 Peter 已有的记忆，数量和 `/memory` 对得上。
3. 让 Peter 记一件新的事，在面板里能看到这条新记忆；在面板里置顶一条，刷新面板后置顶还在。
4. 点「退出服务」：聊天和面板都停掉；再双击启动，记忆、面板密码都还在。
5. 聊天、多条消息、lab、文件审批、自主活动、`/cost` 照常；Peter 下次聊天收到一次能力变化提示。


## 验收记录

- lan 2026-10-21 确认：Windows 真实体验验收通过。验收按本工单「验收步骤（Windows）」完成；细项未另行留存独立操作记录。
