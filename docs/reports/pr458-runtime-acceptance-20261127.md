# PR #458 · 运行时隔离验收补充（脱敏）

> 时间：2026-11-27 · 分支：\`ticket/458-repo-layout\` · 未合并、未部署
> 仅使用临时克隆、合成数据和本地模拟模型；没有读取或修改正式 \`data/peter\`、真实聊天/记忆、人设正文或密钥。命令中的临时目录以 \`<TEMP>\` 代替，不记录本机绝对路径。

## 0. 结论

- **原地升级、旧/新桌面后端兼容、Brain/API/聊天、权限审批、完整离线回滚、失败恢复**均已在隔离副本中实际执行。
- 发现并修复一个实际缺陷：回滚切换到目标提交后，若依赖同步或重新打包失败，原实现会留下“目标代码 + 旧桌面”的半可用状态；修复后会自动恢复原代码和原桌面。
- **未验证**：打包版 \`VEIL.exe\` 的图形界面实际显示/鼠标交互（本机会话为非交互式，EXE 启动后立即退出 0，未出现窗口）；旧/新桌面兼容性通过各自 \`app.asar\` 中的真实 \`backend.cjs\` 启动后端并请求 \`/api/health\` 验证。
- **未验证**：Nowhere 安装（升级时主动中止该可选组件，只保留警告）；自主唤醒的“经历”独立记录（本次写入了 \`autonomy.jsonl\` 与 \`data/costs.jsonl\`，没有另建独立经历文件）。

## 1. 原地升级（旧版 update.bat）

临时副本：旧树 \`d78a8b7\` 的归档 + 新版目录补丁，使用旧版 \`update.bat\` 的 \`.local-tmp\update-running.bat\` 副本。

| 步骤 | 命令（脱敏） | 退出码 | 关键输出 |
|---|---|---|---|
| 第一次升级 | \`cmd /c .local-tmp\update-running.bat --running-copy <TEMP>\` | **1** | 代码已更新；\`playwright install chromium\` 因只读缓存 \`EPERM __dirlock\` 中止。**数据未修改，桌面未改。** |
| 第二次升级（可写 Playwright 缓存） | 同上 | **0** | \`Official fetch MCP server is already installed\`；\`npm run package\` 输出 \`Wrote new app to: dist\VEIL-win32-x64\`；\`Desktop app rebuilt\`。 |
| 可选组件 | 同上 | — | Nowhere 安装被主动中止并只打印警告；不影响其余升级。 |

升级前后：旧树数据目录、\`persona/\`、\`characters/\`、\`.env\` 路径不变；升级前备份 manifest 共 11 个文件，验收后 \`.env\`、\`persona/*\`、\`characters/peter/*\` 均与备份哈希一致；仅合成 drill 配置/Brain DB 被后续探测按预期改写。

## 2. 桌面兼容（旧/新 app.asar 后端）

| 对象 | 命令（脱敏） | 结果 |
|---|---|---|
| 旧版 \`VEIL.exe\` 的 \`app.asar\` 后端 | \`node old_backend_probe.cjs\` | \`validRoot=true\`；\`connect\` 经根 \`web_launcher.py\` 启动迁移后后端；\`/api/health\` 200，\`ready=true\`，\`stopped=false\` |
| 新版打包程序的 \`app.asar\` 后端 | \`node new_backend_probe.cjs\` | \`validRoot=true\`；\`launchArgs=["-m","veil.server.web_launcher","--no-browser"]\`；\`/api/health\` 200，\`ready=true\` |
| 直接启动打包 \`VEIL.exe\` | \`VEIL.exe\` | 本机非交互会话中进程立即退出 0、无窗口；**GUI 未验证**，不作为通过证据 |

## 3. Brain、记忆/事件 API 与聊天落盘

旧树代码（\`veil_brain\`、\`brain_writer\`、\`brain_events\`）创建合成 \`data/drill/brain.db\`：1 条 memory、1 个 event、1 个 stage，\`check_db\` 为 integrity ok / digest_ok true。

升级后启动真实 \`Hub\` + \`start_server\`，通过 HTTP API：

| 请求 | 结果 |
|---|---|
| \`GET /api/brain/overview\` | 200；\`active=1\`，\`events_total=1\` |
| \`GET /api/memory/library\` | 200；\`total=1\`，\`items=1\` |
| \`GET /api/memory/events\` | 200；\`count=1\`，标题 \`Fictional drill event\` |
| \`POST /api/send\` | 200；请求 \`drill-request-1\` 被接受 |
| \`GET /api/events?after=0\` | assistant 事件文本 \`FICTIONAL DRILL REPLY\` |
| 落盘 | \`data/drill/chat.jsonl\` 存在，3 行（含 user/assistant）；\`data/costs.jsonl\` 写入 |

模型为脚本化本地 fake（不访问付费接口）。

## 4. 权限与自主唤醒

| 场景 | 实际结果 |
|---|---|
| 允许工具 \`read_text_file\` | \`成功\`，真实读取合成文件 |
| 工作区内新文件 \`write_file\` | \`成功\`，文件创建 |
| 需要审批的 \`append_file\`（growth journal）拒绝 | \`被拒绝\`，文件内容保持 \`OLD\` |
| 同一操作批准 | \`成功\`，内容变为 \`OLD\nYES\` |
| 隔离自主唤醒 \`AutonomyController.run()\` | \`data/peter/autonomy.jsonl\` 新增记录；\`data/costs.jsonl\` 写入；本次 \`cost_cny=0.000360\`；记录结果 \`搁置\`（真实执行，未伪造成功） |

自主唤醒的“经历”独立文件未生成，标记未验证。

## 5. 完整离线回滚

命令（**不带** \`--skip-package\` / \`-SkipPythonDeps\`，代理指向不可用端口并设置 \`PIP_NO_INDEX=1\`）：

\`\`\`powershell
powershell -File scripts/rollback_version.ps1 -RepositoryPath <TEMP> -TargetRef <old-tree-commit>
\`\`\`

结果：

- 退出码 **0**；
- HEAD 回到旧树提交；
- 旧桌面重新打包成功（\`Wrote new app to: dist\VEIL-win32-x64\`）；
- 新桌面构建保留在 \`.local-tmp\rollback-dist-*\`；
- 旧代码 \`veil_brain.check_db\` 仍能读取同一 \`data/peter/brain.db\`（integrity ok，memories=1，events=1）；
- \`data/\`、\`persona/\`、\`characters/\`、\`.env\` 路径不变。

## 6. 失败恢复

| 故障 | 退出码 | 恢复结果 |
|---|---|---|
| 升级时 Playwright 缓存只读 | 1 | 代码已更新；数据/人设未改；补可写缓存后重跑成功；没有强行继续打包 |
| 重新打包 EPERM | 1 | 旧 \`VEIL.exe\` 恢复到原位置；代码恢复到回滚前提交；数据哈希不变 |
| 依赖同步缺包（\`definitely-missing-package==0.0.1\`，\`-AllowOnlineDeps\` + 无索引） | 1 | pip 失败；代码恢复到回滚前提交；旧桌面和数据保持；没有目标代码+旧桌面混用 |
| 回滚前存在未提交 tracked 修改 | 1 | 在切换前中止；HEAD、文件和数据不变 |
| 回滚前未跟踪文件与目标路径冲突 | 1 | 在切换前中止；文件内容、HEAD 不变（静态+运行时演练均验证） |

## 7. 未验证清单

1. 打包 \`VEIL.exe\` 的图形界面实际打开、点击、聊天窗口交互（本机非交互会话中 EXE 无窗口直接退出 0）。
2. Nowhere 安装/旅行服务（升级时主动中止该可选组件）。
3. 自主唤醒的独立“经历”记录（只有 autonomy 记录与费用账本）。
4. 正式 Peter 的真实数据、真实模型、真实聊天（按规则禁止）。
