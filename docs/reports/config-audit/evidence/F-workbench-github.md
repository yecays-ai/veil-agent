# F 域取证：工作台、GitHub 与个人项目

- 取证对象：`main 快照`（GitHub `yecays-ai/veil-agent` main，提交 `e7e7992`；快照内 `git log -1` 显示 `1263a2e main e7e7992 (P3-3)`）。
- 只读边界：未读取 `正式运行目录`；快照内没有 `data/`（`.gitignore:8` 忽略 `data/`），因此本机 `data/peter/*.json`、`.env`、`secrets/github-token.txt` 的实际值一律标为「无法读取」。
- 本域代码入口：`executor_opencode.py`、`workbench_scope.py`、`workbench_switch.py`、`maintenance.py`、`github_readonly.py`、`github_token_setup.py`、`lab_games.py`、`lab_preview.py`、`lab_sandbox.py`、`guest_profiles.py`、`web_agent.py`（`/api/workbench`、`/api/windows-apps`、`/game/*`）、`veil_agent.py`（`[executor]` 解析与工作台调用）、`desktop/chat.js`、`desktop/chat.html`、`docker/workbench.Dockerfile`。
- 重要事实：**仓库自带的 `characters/peter/character.toml` 没有 `[executor]` 表**（全文 286 行，节标题见 `characters/peter/character.toml:9-239`）。因此出厂状态下工作台关闭、自我维护关闭，全部 `[executor]` 键都用 `veil_agent.py:1138-1151` 的代码默认值；`docs/tickets/237`、`238`、`239` 只存在于工单文档，不属于运行配置。

---

## 1. 执行链

### 1.1 工作台开关与本地设置（data/peter/workbench.json）

1. 启动加载角色：`veil_agent.load_character()` 读 `characters/<id>/character.toml`（`veil_agent.py:837-838`），再读本机设置 `workbench_local_settings(root, character_id)`（`veil_agent.py:1131-1132`；路径 `data/<id>/workbench.json`，`veil_agent.py:810-811`）。
2. 读取/校验：JSON 解析失败 → `UserError('工作台本地设置读不了，请删掉或修好 data 里的 workbench.json。')`（`veil_agent.py:820-823`）；先剔除已删除的宿主开关键 `host_mode / host_keep_old_tools / host_desktop_handover`（`veil_agent.py:799, 824-825`）；任何未知键或类型不符 → `UserError('工作台本地设置字段无效，请删掉或修好 data 里的 workbench.json。')`（`veil_agent.py:826-828`）；允许键只有 `enabled: bool, project_roots: list, container_image: str, web_hosts: list`（`veil_agent.py:798`）。
3. 合并覆盖：`if local: executor = {**(executor or {}), **local}`（`veil_agent.py:1133-1134`）——本机文件的四个键**覆盖** `character.toml [executor]` 的同名键。
4. 归一化默认：`enabled` 必须是 bool（否则 `ValueError('executor.enabled must be boolean')`，`veil_agent.py:1136-1137`），`command` 默认 `''`，`max_seconds` 默认 600，`repeat_limit` 默认 3，`run_budget_cny` 默认 `'0.5'`，`wake_budget_cny` 默认 `'0.3'`，`project_roots` 默认 `[]`，`container_image` 默认 `''`，`web_hosts` 默认 `[]`，`maintenance_repo` 默认仓库根，`worktree_root` 默认 `仓库父目录/worktrees`，`github_repo` 默认 `yecays-ai/veil-agent`，`github_token_file` 默认 `''`，`maintenance_hours` 默认 2（`veil_agent.py:1138-1151`）。正整数校验 `positive_int`（`veil_agent.py:728-731`），价格校验 `price > 0`（`veil_agent.py:754-758`）。
5. 真正使用点：
   - 工具出现条件 `workbench_ready()`：`enabled` 为真 + 有 `container_image` 时要求 `docker` 在 PATH，否则要求 `find_command(command)` 找到 OpenCode（`veil_agent.py:4984-4992`，`executor_opencode.py:907-911`）。
   - 工具 schema 注入 `workbench`（`veil_agent.py:2467-2469`；schema 见 `executor_opencode.py:46-74`）。
   - 运行 `_run_workbench`（`veil_agent.py:5023-5139`）；容器字典**只有 image 一个键** `{'image': settings['container_image']}`（`veil_agent.py:5027`）。
6. 缺省/非法/耗尽时：
   - 文件损坏/字段非法 → 启动时 `UserError`，Peter 起不来（`veil_agent.py:1169-1174` 统一包成「角色配置无法读取或字段无效」）。
   - 未装 OpenCode/无 Docker → 工具不出现（`veil_agent.py:4986-4992`）；即使被调用也只回 `The workbench is not installed on this computer.`（`veil_agent.py:5029-5030`）。
   - 当前模型没有 API Key → `The workbench has no API key for the current model.`（`veil_agent.py:5033-5034`）。
   - 超出日/月费用池 → `The workbench was blocked by the shared cost limit: ...`（`veil_agent.py:5079-5084`）。
   - 本次任务超时/超预算/重复过多 → `run()` 中止并写 `[workbench=stopped]`（`executor_opencode.py:1362-1369, 1444-1452`）。

### 1.2 运行上限、代理与密钥

1. 上限来源：`limits = {**DEFAULT_LIMITS, **(limits or {})}`（`executor_opencode.py:1106`）；`DEFAULT_LIMITS = {'max_seconds': 600, 'repeat_limit': 3, 'run_budget_cny': '0.5'}`（`executor_opencode.py:40`）。VEIL 只传三个键：`limits = {k: settings[k] for k in ('max_seconds','repeat_limit','run_budget_cny')}`（`veil_agent.py:5069`）。
2. 自主醒来：`limits['run_budget_cny'] = min(run_budget_cny, wake_budget_cny)`（`veil_agent.py:5070-5071`）。
3. Peter 申请更高预算：`budget_cny` 必须 `0 < x <= 50`（`MAX_RUN_BUDGET_REQUEST = Decimal('50')`，`veil_agent.py:1324, 4994-5003`），否则原样返回 `budget_cny must be more than 0 and at most 50 yuan.`；高于当前值时弹审批卡（文案原文）：`Peter 想把这次工作台任务的费用上限从 ¥{current} 提到 ¥{wanted}（只管这一次任务，用多少花多少）{extra}。任务：{shown}。同意吗？y/N `（`veil_agent.py:5010-5014`），批准还临时抬高当天额度 `raise_today(shortfall)`（`veil_agent.py:5018-5019`；`raise_today` 只存内存，重启失效，`veil_agent.py:1341-1352`）。
4. 预留与结算：先 `reserve_amount(..., 'workbench-estimate')`（`veil_agent.py:5079-5080`；`veil_agent.py:1585`），结束按实际但不超预留结算（`veil_agent.py:5087-5092`；`settle_estimate` `veil_agent.py:1623`）。费用池默认 daily 10 / monthly 300（`veil_agent.py:1328-1329`），运行时被 `models.json` 的 `limits.daily_cny/monthly_cny` 覆盖（`model_settings.py:29-30, 567-572`；`web_agent.py:3710-3713`）。
5. 密钥代理：`KeyProxy(provider['base_url'], key, max_requests=PROXY_MAX_REQUESTS, host='0.0.0.0' if container else 127.0.0.1)`（`executor_opencode.py:1169-1170`）；`PROXY_MAX_REQUESTS = 600`（`executor_opencode.py:41`）；代理随机令牌 `secrets.token_urlsafe(24)`（647），只允许 80/443（649），隧道(安装/联网)另计 1000 次（654），上游超时 180s（645）；模型转发次数到限返回 403 `request limit reached`（741-742），代理到限返回 429 `network approval limit reached`（709-710, 781-782）。
6. 运行日志：`<home>/runs/*.json`，保留最新 `RUN_LOG_KEEP = 30` 份、单份 `RUN_LOG_MAX_BYTES = 2 * 1024 * 1024`（`executor_opencode.py:524-525, 542-560`）；审批揭示过的密钥内容写盘前替换 `[redacted]`（531-540），单文件揭示上限 `SECRET_REVEAL_MAX = 64 * 1024`（528）。

### 1.3 容器隔离

1. 有 `container_image` → 走 Docker：`docker run --rm --name veil-workbench-* --cap-drop ALL --security-opt no-new-privileges --pids-limit 256 --memory 2g`（`executor_opencode.py:1048-1049`；`--memory` 读 `container.get('memory','2g')`，但 VEIL 从不传 `memory`，实际固定 2g）；网络锁模式加 `NET_ADMIN/SETUID/SETGID/CHOWN`、`--user root`、`--add-host veil-relay:host-gateway`（1050-1053），只发布 `127.0.0.1:<port>`、只挂 `-v <workdir>:/work`（1054-1055）。
2. 密钥文件屏蔽：项目内 `.env/*.pem/id_rsa*...` 用空文件只读覆盖（`executor_opencode.py:964-1014, 1222-1250`）；`secret_files(workdir)` 调用未传 `max_depth`（1137），代码默认 `max_depth=None` 即**递归全部深度**（997-1014）——工单 238 写「6 层目录内」，与代码不一致。
3. 网络锁：入口脚本装 iptables 只放行回环与 veil-relay，最后 `iptables -A OUTPUT -j REJECT`，并降权到 node（`executor_opencode.py:1071-1091`）；镜像不含 `iptables/setpriv` 时直接拒绝运行：`The workbench image lacks the network-isolation tools. Rebuild veil-workbench from docker/workbench.Dockerfile before running the container workbench.`（1144-1164）。容器内看门狗 5 次心跳失败（每次 2s）杀进程组（`executor_opencode.py:1017-1038`）。
4. 自我维护强制容器：`if maintenance and not container: return ExecutorResult('VEIL maintenance requires the isolated workbench container.', '被拒绝', reason='isolation_required')`（`executor_opencode.py:1108-1114`）。
5. 镜像：`FROM node:22-slim`，`npm install -g opencode-ai@1.19.5`，venv `/opt/venv`，两个用途标签 `veil.workbench.network-lock=1`、`veil.workbench.install-env=1`（`docker/workbench.Dockerfile:1-12`）；`--pids-limit 256` 来自代码而非镜像。
6. 缺省/非法：镜像标签不是 `1/1` → 设置页显示 `还没有运行环境，双击 workbench-switch.cmd`（`workbench_switch.py:85-97`；`desktop/chat.js:2197`）；`workbench-switch.cmd` 开启时会 `docker build`（`workbench_switch.py:100-106`）。

### 1.4 权限/审批（OpenCode permission → VEIL 决策）

1. OpenCode 侧规则由 `build_config()` 内联下发（`OPENCODE_CONFIG_CONTENT`，不写项目文件，`executor_opencode.py:914-961`）：`bash {'*': 'allow' if isolated else 'ask'}`（922），`ASK_COMMANDS` 全部 `ask`（156-167, 923-928），维护模式对 `MAINTENANCE_DENY`（git commit/push/merge/reset/checkout/worktree 等）`deny`（168-169, 929-931），`edit` 对保护文件 deny（919-920），`webfetch/external_directory` 一律 `ask`（944），`read` 对 `SECRET_READ_GLOBS` `ask`（945），`continue_loop_on_deny: True`、`share: disabled`、`autoupdate: False`（946-948）。
2. VEIL 决策 `decide()`（`executor_opencode.py:369-411`）：`task/skill/todowrite` 直接放行；未知权限（含 VEIL 自己的 MCP 工具）一律 `reject`（374-378）；容器内 `/tmp` 的 `external_directory` 直接放行（379-381）；**自主醒来时一律拒绝**：`return 'reject', "needs Lan's approval; not asked during autonomous activity"`（382-383）；聊天里弹卡：`Peter 的工作台想在项目“{project}”的本次任务中{what}：{shown}。同意吗？y/N `（389）。
3. 授权复用：任务内 `TaskScope`（`executor_opencode.py:179-191`）只保存本次任务的批准/拒绝；维护任务把 sha256 指纹持久化到任务 JSON 的 `authorization.workbench`（`veil_agent.py:5123-5126`；`maintenance.py:1420-1447`），任务停止/合并/过期后指纹失效（1451）。
4. 审批超时/取消：`_ask_lan` 把 `cancel/cancelled` 记为 cancelled、`timeout/timed_out` 记为 timed_out，两者都会终止整次运行（`executor_opencode.py:247-257, 1352-1355`）。
5. 诊断落盘：每个权限事件写 `data/<role>/memory/_agent/workbench_permissions.jsonl`（`veil_agent.py:5094-5097, 5122`）。

### 1.5 GitHub 只读研究（github_readonly.py）

1. 启用：`[web] github_readonly = true` 且 `web.enabled` 才注册工具（`veil_agent.py:1106-1130`；`character.toml:204-212`；`github_readonly.py:84-89`）。
2. 工具只一个名 `github_read`（`github_readonly.py:20, 100-125`），资源枚举 18 种（25-28），参数白名单 + `additionalProperties: False`（126, 138-143）。
3. 只读机制：匿名 GET（`public_get`，`github_readonly.py:42-54`，禁止跟随重定向 36-39，User-Agent `VEIL-public-research`）；私有库强制拒绝（174-175）；只有 `search_code` 且**没有 ref/path** 且**进程环境里有 `VEIL_GITHUB_READONLY_TOKEN`** 时才走官方只读 MCP（288-290），MCP 端点 `https://api.githubcopilot.com/mcp/readonly`、头 `X-MCP-Readonly: true`、`X-MCP-Toolsets: repos`、唯一调用 `search_code`、`perPage: 5`（23, 57-77）。
4. 上限：`MAX_RESPONSE = 2_000_000`（21）、`MAX_FILE_BYTES = 1_000_000`（22）、单次输出 `max_result_bytes`（默认 8192，83、337-347）、`per_page: 10`（264）、无令牌 `search_code` 每页扫 3 个文本文件（309）、`page` 1..1000（151-153）、搜索 query 1..200 字（167）、网络超时 15s（50）、MCP 总 35s/连接 15s/读 25s/线程 40s（62-77）。
5. 令牌来源：只读 `os.environ.get(TOKEN_ENV)`（288-290），`TOKEN_ENV = 'VEIL_GITHUB_READONLY_TOKEN'`（24）。仓库里**没有** `load_dotenv`（全仓库 grep 无结果），`.env` 仅在调用点用 `dotenv_values` 单独读取（`veil_agent.py:5024,5032`；`web_agent.py:940-941`），Electron 后端只继承父进程环境（`desktop/backend.cjs:41-45`）——所以写进 `.env` 的这个键**不会被加载**；必须设成 Windows 用户/系统环境变量再启动 VEIL。`.env.example:1-15` 也没有这个键。
6. 缺省/异常：未配置 `github_readonly` → 工具不出现（`veil_agent.py:1127-1130`）；失败返回固定文案（`GitHub rate limit or access restriction; try later.` 等，`github_readonly.py:187-195`）；未启用时调用返回 `GitHub research is not available for this role.`（136-137）。

### 1.6 VEIL 自我维护（maintenance.py）

1. 初始化条件：`executor.enabled` 且 `tool_mode` 且 `cfg.id == 'peter'` 才创建 `MaintenanceManager`（`veil_agent.py:2275-2284`），参数 `repo_path=maintenance_repo or ROOT`、`worktree_root`、`github_repo`、`hours=maintenance_hours`、`token_file=github_token_file or <仓库>.parent.parent/secrets/github-token.txt`（2279-2283）。
2. 仓库校验 `_verify_repo`：必须是仓库根、必须当前分支 `main`、工作区无已跟踪改动、`origin` URL 的 owner/repo 必须等于 `github_repo`（`maintenance.py:634-650`）。
3. 开始任务 `begin`：拒绝自主发起（`maintenance.py:905-907`），先 `gh auth status` 检查（946-954，提示原文见「GitHub is not ready: ...」），同步 origin/main（651-657），生成分支 `ticket/<n>-<slug>`（85-86），worktree 落在 `worktree_root/<ticket>-<slug>`（928），必须 Lan 在卡上同意：`开始工单 {ticket}「{title}」：允许 Peter 在独立工作区开发，并只对 {github_repo} 的 {branch} 分支推送、创建或更新 PR 吗？正式目录不会改动。y/N `（930-932）。
4. worktree 位置保护：不得在仓库内、不得与 data 目录重叠（`maintenance.py:668-680`）；已有 worktree 必须是同仓库同分支且无未提交改动（683-703）。
5. 工时/截止：`hours` 只用于生成任务 `deadline = now + hours`（`maintenance.py:282-291`），之后 `_expired()` 在 deliver/merge/workbench/审批持久化前拦截（966-970, 1076-1078, 1262-1264, 1426, 1451）。改配置**不影响已创建任务**（deadline 已写入任务 JSON）。
6. 交付/合并：`deliver` 拒绝自主（`maintenance.py:1066-1067`）、`merge` 拒绝自主（1257-1258）；未验收要合并时强制问：`工单 {ticket} 尚未通过 Lan 的实际验收（当前 {acceptance}）。仍要合并 PR #{number} 吗？这会跳过用户验收。y/N `（1293-1294），同意后 `waiver=True`；同一 head 被拒后不再自动问（1290-1291）。
7. 凭证隔离：OpenCode 永远拿不到 GitHub token（`maintenance.py:1524-1525` 工具说明；`_token_env` 只给 `git`/`gh` 调用，587-598）；git/gh 环境从 `_host_env` 去掉 `GIT_DIR/GIT_WORK_TREE/GIT_SSH_COMMAND/SSH_ASKPASS...`（158-170）；`KEEP_ENV` 是验收实例与子进程的白名单（44-49, 154-156）。
8. 验收实例：从 PR commit 精确还原快照并逐文件 SHA 校验（`maintenance.py:346-478`），复制 `characters/`、`persona/` 和**真实 `.env`**（481-483），启动 `web_agent.py --acceptance --config-root ... --data-dir ... --port 0`（486-490），Electron 窗口带 `VEIL_AGENT_DATA/VEIL_DESKTOP_DATA/VEIL_DESKTOP_TEST=1`（531-536）；`--acceptance` 会禁用自主、通道、记忆/声音/桌面等（`web_agent.py:3704-3709, 3624-3625`），日/月上限压到 2/20 元（`web_agent.py:3714-3719`），执行器强制 `enabled=False`（3709）。
9. 失败行为：`gh` 未登录/令牌缺失时在创建 worktree 前给出一句可读原因（946-954）；本地 main 与远程不一致时不改写历史，交付前重新校验（`maintenance.py:1033-1050`）。
10. 人工更新：`update-main.cmd` 与维护任务解耦，只 fetch/ff-only 更新 main，先 `git stash` 备份本机改动，冲突就建 `backup/main-before-update-*` 分支并停止（`update-main.cmd:27-110`）。

### 1.7 Lab / 小游戏 / Python 沙盒

1. Lab 目录来自 `[tools] lab = "lab"`（`character.toml:119`），必须是工作区内单级普通文件夹、不能等于成长日记名（`tools_agent.py:429-453`）；写入上限 `[tools] lab_max_write_bytes = 200000`（`character.toml:120`；校验 `tools_agent.py:430-432`）。
2. 预览：`preview_lab_page` 只在 Windows 可用（`lab_preview.py:333, 358-362`），每轮聊天/醒来最多 `MAX_ITERATIONS = 2` 次（24, 369-373），超时 `PREVIEW_TIMEOUT_SECONDS = 30`（25, 334, 387-390）；页面/资源上限 `MAX_PAGE_BYTES = 5MB`、`MAX_RESOURCE_REQUESTS = 128`、`MAX_TOTAL_RESOURCE_BYTES = 20MB`、截图 `MAX_SCREENSHOT_BYTES = 5MB`、诊断 `24 条 × 500 字`（18-23）；导航/稳定/截图超时 10s/1.5s/2.5s（26-28）；视口 1280×800（29）；CSP `_csp()`（123-130）。
3. `/game/<capability>/<file>`：同源校验 Host/Origin/Sec-Fetch-Site（`web_agent.py:2000-2020`），资源 `MAX_GAME_RESOURCE = 5MB`（`lab_games.py:20, 75-80`），入口文件自动注入 `veil-score.js`（81-88）；能力表最多 64 条、FIFO 淘汰（56-58），链接失效文案 `The game link has expired; ask Peter to open it again.`（63-64）。
4. 评分：POST `/game/<id>/score` 要求 JSON、请求体 1..2048 字节（`web_agent.py:2494-2500`；`MAX_SCORE_BODY = 2048` 在 `lab_games.py:18`，两处重复常量），字段必须**恰好** `{game_id, round_id, score, duration_ms, ending, ended_at}`（`lab_games.py:100-114`），`round_id` 正则 `^[A-Za-z0-9_-]{8,64}$`（21, 103），分数 ±10^9（105-107），时长 0..86_400_000ms（108），ending ≤160 字且无控制字符（110-112），成绩文件 `scores.jsonl` 上限 `MAX_SCORE_FILE = 2MB`（19, 129-142），同一 round 去重（134-139），Windows 再校验最终路径（157-161）。
5. Python 沙盒：只有 `[tools] python_sandbox = true` 才启用（`character.toml:129`；`tools_agent.py:364`；`lab_sandbox.py:144-147`），关闭时返回 `Lab Python execution is disabled by configuration.`（200-201）；必须 Docker（231-242，宿主执行被明令禁止 238-240）；固定 `python:3.12-slim`、`--network none`、`--cpus 1.0`、`--memory 256m`、`--pids-limit 64`、`--cap-drop ALL`、`--read-only`、`/tmp 16m noexec`（30-33, 382-402）；默认超时 15s、最大 30s（20-21, 245-249）；脚本 ≤2MB、输出 50KB/1000 行、生成文件 ≤20 个/每个 5MB/合计 20MB/工作区磁盘 50MB（22-28）。

### 1.8 Windows 应用卡片与客浏览器档案

1. 卡片后端：GET/POST `/api/windows-apps`（`web_agent.py:2446-2447, 3443-3444`），需要 `X-Token`（3077, 2872-2876）；“添加”要求该应用**当前已打开**，否则 `没找到这个软件的窗口，请先把它打开再添加。`（3082-3085）。
2. 存储：Lan 从卡片批准的应用写 `data/<role>/windows-approved-apps.json`（`tools_agent.py:275`；`windows_interaction.py:1064-1094`），动作固定 `activate/minimize/restore/invoke/select/toggle/expand/collapse`（1066）；静态白名单在 `characters/<id>/windows-apps.toml`（`character.toml:226`；`windows_interaction.py:419-432`）。
3. 客档案：`guest_profiles.py` 用临时目录 `veil-chrome-guest`，5s 轮询、主人进程死后再等 24h 才删、旧前缀 7 天、最多重试 1440 次（约 2h）、7 天兜底清扫（`guest_profiles.py:19-27, 102-150`）；由 `windows_interaction.py:1001-1021` 调用。

---

## 2. 配置清单表

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 执行器 | `enabled` | veil_agent.py:1136-1138；workbench_switch.py:14 | `data/peter/workbench.json` 或 `character.toml [executor]`，bool | `False`（workbench_switch.py:26；veil_agent.py:1138） | 必须是 bool，否则 `executor.enabled must be boolean`（1136-1137） | 设置→工作台「开/关」（chat.html:226；chat.js:2212-2213）；或 `workbench-switch.cmd` 菜单 1/2（workbench_switch.py:133-144） | workbench.json **覆盖** toml（veil_agent.py:1133-1134） | 重启 Peter（chat.js:2197、workbench_switch.py:2） | data/ 被 .gitignore:8 忽略，更新不丢 | 开关本身不设权限；打开即可让模型跑命令 | U |
| 执行器 | `command` | veil_agent.py:1139；executor_opencode.py:907-911 | `character.toml [executor]` 字符串路径 | `''` → PATH 上的 `opencode` | 绝对路径必须存在，否则视为未安装 | 只能改文件 | 无（workbench.json 白名单不含此键，veil_agent.py:798） | 重启 | 仓库跟踪文件，更新可能冲突 | 指向任意可执行文件＝任意代码执行 | A/S |
| 执行器 | `max_seconds` | veil_agent.py:1140；executor_opencode.py:1363-1364 | `character.toml` 整数 | `600`（executor_opencode.py:40） | 正整数，秒；无上限 | 仅改文件 | 只被 run 参数覆盖（无） | 重启；只影响新任务 | 仓库文件 | 超时=停止并 abort（1363-1377） | A |
| 执行器 | `repeat_limit` | veil_agent.py:1141；executor_opencode.py:1368-1369 | `character.toml` 整数 | `3` | 正整数，次；语义为「同一调用连续重复次数」 | 仅改文件 | 无 | 重启 | 仓库文件 | 防死循环护栏 | A |
| 执行器 | `run_budget_cny` | veil_agent.py:1142；veil_agent.py:5069-5080；executor_opencode.py:1168,1365-1367 | `character.toml` 字符串小数 | `'0.5'` 元 | 正有限小数（price()，veil_agent.py:754-758）；无上限，受日/月池约束 | 仅改文件；单次任务可由 Peter 申请 `budget_cny`（≤50）经 Lan 审批 | 自主时被 `wake_budget_cny` 取 min（5070-5071）；申请值覆盖本次（5020）；预留占用 models.json 日/月池（5079-5080） | 重启；每次任务读取 | 仓库文件 | 硬费用上限＋审批（5013-5017） | U/A |
| 执行器 | `wake_budget_cny` | veil_agent.py:1143, 5071 | `character.toml` 字符串小数 | `'0.3'` 元 | 正有限小数 | 仅改文件 | 自主醒来时与 run_budget 取 min，前者永远只能收紧 | 重启 | 仓库文件 | 降低自主花费 | A |
| 执行器 | `project_roots` | veil_agent.py:792-795, 1144；workbench_scope.py:50-102 | toml 字符串数组 / workbench.json 数组（绝对路径） | `[]`＝只用工作台 workspace | 非空字符串列表；每条应是已存在目录（运行期不存在则跳过，workbench_scope.py:59-60） | 设置→工作台「添加项目文件夹/移除」（chat.html:228；chat.js:2214-2217；API web_agent.py:3117-3125）；或脚本菜单 3/4 | workbench.json 覆盖 toml | 重启 | data/ 文件 | 扩大可改项目范围＝扩大影响面；页面拒绝 data/characters/persona（workbench_switch.py:52-55） | A |
| 执行器 | `container_image` | veil_agent.py:1145；veil_agent.py:5027；workbench_switch.py:13,29,87-97 | toml 字符串 / workbench.json 字符串 | toml 缺省 `''`＝宿主模式；**页面/脚本缺省 `veil-workbench`**（workbench_switch.py:13,26） | 任意镜像名；默认镜像需带两个标签 `1/1` 才认为可用 | 页面**不可改**（web_agent.py:3110-3135 只接受 enabled/add/remove/add_host/remove_host）；脚本只能沿用已有值（workbench_switch.py:136-140） | 覆盖 toml；且**保存任何页面改动都会把它写成 `veil-workbench`**（load_state→save_state，workbench_switch.py:26,40） | 重启 | data/ 文件 | 自定义镜像不校验网络锁标签（workbench_switch.py:87-90），但仍会跑 iptables 探测（executor_opencode.py:1144-1164） | A/S |
| 执行器 | `web_hosts` | veil_agent.py:802-807, 1146；executor_opencode.py:1173-1175；veil_agent.py:5127 | toml 数组 / workbench.json 数组 | `[]`＝容器无额外站点 | 主机名小写，≤50 条，正则 `[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?`（veil_agent.py:804-807）；页面额外要求含 `.`（workbench_switch.py:68） | 设置→工作台「添加网站/移除」（chat.html:229-231；chat.js:2190-2196, 2211） | workbench.json 覆盖 toml；**只在容器模式且非维护任务时生效**（veil_agent.py:5127），宿主模式被静默忽略 | 重启 | data/ 文件 | 只放行容器内 Playwright 与代理 egress；VEIL 自带浏览器 MCP 不受它限制（见 §3） | U/A |
| 执行器 | `maintenance_repo` | veil_agent.py:1147；veil_agent.py:2279 | toml 字符串路径 | 仓库根 `ROOT` | 必须是 Git 仓库根（maintenance.py:634-650） | 仅改文件 | 无 | 重启 | 仓库文件 | 指向别的仓库＝对别的仓库提交/推送 | S |
| 执行器 | `worktree_root` | veil_agent.py:1148；maintenance.py:576,668-680 | toml 字符串路径 | `<仓库父目录>/worktrees` | 不得在仓库内、不得与 data 重叠 | 仅改文件 | 无 | 重启；任务创建时使用 | 仓库文件 | 写盘位置；越界会被拒绝（668-680） | A |
| 执行器 | `github_repo` | veil_agent.py:1149；maintenance.py:30,647-649 | toml 字符串 `owner/repo` | `yecays-ai/veil-agent` | 必须与 `origin` 一致，否则拒绝开始（647-649） | 仅改文件 | 与 `[web] github_repository` **互不相干**（后者只做展示） | 重启 | 仓库文件 | 决定 PR/合并目标仓库 | S |
| 执行器 | `github_token_file` | veil_agent.py:1150, 2283；maintenance.py:176-188 | toml 字符串路径 | `''` → `<仓库根>.parent.parent/secrets/github-token.txt` | 文件存在且有 `github_pat_*`/`gh[pousr]_*` 行（TOKEN_PATTERN，maintenance.py:172） | 仅改文件；用 `github-token.cmd` 写入默认路径（github_token_setup.py:13-22） | 空值自动落到默认路径 | 每次 git/gh 调用现读（maintenance.py:598-616） | 仓库外明文文件 | 无 ACL/权限校验、无加密；token 只给宿主 git/gh（587-598），日志脱敏（63-66） | S |
| 执行器 | `maintenance_hours` | veil_agent.py:1151；maintenance.py:282-291,966-970 | toml 整数 | `2` | 正整数，小时 | 仅改文件 | 无 | 只在新任务 begin 时写入 deadline；改后旧任务不变 | 任务 JSON（data/<role>/maintenance/tasks） | 超时后 deliver/merge/workbench/审批全部拒绝（1076,1262,1426,1451） | A |
| 执行器 | `DEFAULT_LIMITS` | executor_opencode.py:40,1106 | 代码常量 | `max_seconds 600 / repeat_limit 3 / run_budget_cny '0.5'` | 同上 | 代码写死，无入口 | 被调用方 limits 覆盖 | 每次 run | 无 | 仅为兜底 | P |
| 执行器 | `PROXY_MAX_REQUESTS` | executor_opencode.py:41,1169 | 代码常量 | `600` 次/次运行 | 整数 | 代码写死，无入口；工单 238 写「步数×3+5」与此不符 | — | 每次 run | 无 | 令牌泄漏后最多 600 次模型调用 | P |
| 执行器 | `KeyProxy.max_requests` 默认 | executor_opencode.py:645,741-742 | 代码默认值 | `400`（仅未显式传入时；run 实际传 600） | 整数 | 代码写死 | 被 PROXY_MAX_REQUESTS 覆盖 | 每次 run | 无 | 429/403 限流 | P |
| 执行器 | 代理隧道上限 `proxy_limit` | executor_opencode.py:652-654,709-710 | 代码常量 | `1000` 次 | 整数 | 代码写死 | 安装/联网命令共用 | 每次 run | 无 | 阻止无限联网 | P |
| 执行器 | 代理允许端口 | executor_opencode.py:649,686-687 | 代码常量 | `{80, 443}` | 端口集合 | 代码写死 | — | 每次 run | 无 | 只放行 HTTP/HTTPS | P |
| 执行器 | 代理上游超时 | executor_opencode.py:645 | 代码默认值 | `180` 秒 | 秒 | 代码写死 | — | 每次 run | 无 | — | P |
| 执行器 | 结果裁剪 `RESULT_CHARS` | executor_opencode.py:42,1451,1470 | 代码常量 | `4000` 字符 | 字符 | 代码写死 | — | 每次返回 | 无 | 防上下文爆炸 | P |
| 执行器 | 运行日志保留 `RUN_LOG_KEEP` | executor_opencode.py:524,558 | 代码常量 | `30` 份 | 整数 | 代码写死 | — | 每次运行结束 | `data/<role>/workbench-home/runs/*.json`（543-551） | 含每一步工具输入输出；密钥内容只对已揭示文件脱敏 | R |
| 执行器 | 单份运行日志上限 `RUN_LOG_MAX_BYTES` | executor_opencode.py:525,552-557 | 代码常量 | `2 * 1024 * 1024` 字节 | 字节 | 代码写死 | 超限只留诊断摘要 | 每次运行结束 | 同上 | 诊断信息丢失 | P |
| 执行器 | 密钥揭示上限 `SECRET_REVEAL_MAX` | executor_opencode.py:528,1215 | 代码常量 | `64 * 1024` 字节 | 字节 | 代码写死 | — | 审批同意后 | 写入临时 mask 文件，运行后删（1421-1425） | 控制单次泄露面 | P |
| 执行器 | 工具参数上限 | executor_opencode.py:62-72 | 代码 schema | `task ≤4000 字`、`project ≤200 字`、`budget_cny > 0` | 同上 | 代码写死 | — | 每次调用 | 无 | 输入体积约束 | P |
| 容器 | 内存 `--memory` | executor_opencode.py:1049 | 代码常量（container 字典无 memory 键，veil_agent.py:5027） | `2g` | Docker 内存字符串 | 代码写死，无入口 | 无法覆盖 | 每次运行 | 无 | 资源上限 | P |
| 容器 | 进程数 `--pids-limit` | executor_opencode.py:1049 | 代码常量 | `256` | 整数 | 代码写死 | — | 每次运行 | 无 | 防 fork 炸弹 | P |
| 容器 | 网络锁 / 降权 | executor_opencode.py:1050-1053,1071-1091 | 代码逻辑 | 容器运行强制开 | — | 代码写死 | — | 每次运行 | 无 | 只允许回环+veil-relay；镜像缺工具直接拒跑（1144-1164） | P |
| 容器 | 镜像与版本 | docker/workbench.Dockerfile:1-12 | 文件 | `node:22-slim` + `opencode-ai@1.19.5` | 改 Dockerfile 后重建 | 仅改文件＋`docker build`（workbench_switch.py:100-106） | 自定义镜像不自动重建 | 手动构建 | 镜像在 Docker 本地 | 标签用于判定可用 | A |
| 页面 | `workbench.json` 键集合 | veil_agent.py:798-799；workbench_switch.py:14 | JSON 对象 | 只允许四键；旧宿主键被剔除 | 未知键 → 启动报错（826-828） | 页面写四键；脚本写四键（workbench_switch.py:40） | 高于 toml 同名键 | 重启 | data/ | 坏文件会让 Peter 起不来；页面 GET 不报同样错误（workbench_switch.py:27-33） | U |
| 页面 | 客机允许站点上限 | workbench_switch.py:16,72-73 | 代码常量 | `50` | 条 | 代码写死 | 与 veil_agent.py:804 的 50 一致 | 每次添加 | 无 | 限制攻击面 | P |
| 页面 | 图片可用性 `image_ready` | workbench_switch.py:85-97,116-119 | 运行时检测 | 标签 `1/1` | `docker image inspect` 结果 | 只读展示（chat.js:2197） | — | 每次 GET/POST | 无 | 只读 | R |
| 页面 | API 体积 `MAX_BODY` | web_agent.py:48,3391 | 代码常量 | `200000` 字节 | 字节 | 代码写死 | 所有 POST 共用 | 每请求 | 无 | 防护 | P |
| 页面 | API 鉴权 | web_agent.py:1981-1998 | `data/web_service.json` 里的随机 token | 每进程随机 | Host=127.0.0.1/localhost、Origin 同源、`X-Token` 常量时间比较 | 桌面端自动带 token | — | 每请求 | web_service.json | 本机任意同用户进程可读 token | P |
| GitHub 只读 | `github_readonly` | veil_agent.py:1106-1130；github_readonly.py:84-89 | `character.toml [web]`，bool | 缺省 `False`（veil_agent.py:1114；角色显式 true，character.toml:211） | bool，否则 `Invalid public GitHub research settings`（1122-1123） | 仅改文件 | 与 `[web] enabled` 同时为真才注册 | 重启 | 仓库文件 | 只读；写操作无入口 | U |
| GitHub 只读 | `github_repository` | veil_agent.py:1121-1126；github_readonly.py:90-93 | toml 字符串 `owner/repo` | `''`（角色 `yecays-ai/veil-agent`，character.toml:212） | 正则 `[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}` | 仅改文件 | 仅作为工具描述里的 “Your source repository is …”，**不限制可读仓库** | 重启 | 仓库文件 | 无 | X（半死键：只改文案） |
| GitHub 只读 | `VEIL_GITHUB_READONLY_TOKEN` | github_readonly.py:24,288-290 | **进程环境变量**（不是 .env） | 未设置＝无官方 MCP 搜索 | GitHub fine-grained token（文档：Public repositories only、无写权限，docs/tickets/154:12） | 只能设 Windows 环境变量后重启；仓库无页面、无脚本 | 仅当 resource=search_code 且无 ref/path | 下一次调用即读 environ，但进程环境启动时固定 | OS 环境 | 明文在环境里；不读 GITHUB_TOKEN/Git 凭据（ticket 154:12） | S |
| GitHub 只读 | `MAX_RESPONSE` | github_readonly.py:21,51-53 | 代码常量 | `2000000` 字节 | 字节 | 代码写死，无入口 | — | 每次请求 | 无 | 防大响应 | P |
| GitHub 只读 | `MAX_FILE_BYTES` | github_readonly.py:22,255-260,329-334 | 代码常量 | `1000000` 字节 | 字节；超限报 `Read a regular UTF-8 file up to 1 MB` | 代码写死 | — | 每次文件读 | 无 | 只读有界文本 | P |
| GitHub 只读 | 结果字节 `max_result_bytes` | github_readonly.py:83,337-347 | `[tools] max_result_bytes`（角色 8192，character.toml:128） | `8192` | 整数；小于头部时抛 `increase max_result_bytes`（341-342） | 仅改文件（与文件工具共用） | 所有工具共用 | 重启 | 仓库文件 | 控制上下文 | A |
| GitHub 只读 | MCP 端点/头 | github_readonly.py:23,63-70 | 代码常量 | `https://api.githubcopilot.com/mcp/readonly`，`X-MCP-Readonly: true`，`X-MCP-Toolsets: repos`，`perPage: 5` | 固定 | 代码写死 | — | 每次 search_code | 无 | 只允许 `search_code` 一个方法 | P |
| GitHub 只读 | 分页/搜索上限 | github_readonly.py:151-153,212,221,264,309 | 代码常量 | `page 1..1000`、`per_page 10`、无令牌源码扫描 3 文件/页、搜索 ≤1000 命中 | — | 代码写死 | — | 每次调用 | 无 | 防爬取 | P |
| 自我维护 | 任务截止 `deadline` | maintenance.py:282-291,966-970 | 任务 JSON 字段 | `now + hours` | ISO 时间 | 无页面；改 `maintenance_hours` 只影响新任务 | — | 创建时 | `data/<role>/maintenance/tasks/*.json` | 过期拦截交付/合并/工作台 | R |
| 自我维护 | `MAX_TITLE/MAX_GOAL/MAX_NOTE/MAX_HISTORY` | maintenance.py:36-39 | 代码常量 | `160 / 4000 / 1000 / 60` | 字符/条 | 代码写死 | — | 每次调用 | — | 输入上限 | P |
| 自我维护 | `FORBIDDEN_PATH` | maintenance.py:40-42,713-725 | 代码正则 | .env/persona/*.md/data/logs/memory/*.log/*.pem/*.key/id_rsa/cookies/node_modules/.venv | 命中即拒绝提交 | 代码写死 | — | 每次 commit | 无 | 阻止敏感文件入库（例外 `.example.md/.env.example`） | P |
| 自我维护 | `KEEP_ENV` | maintenance.py:44-49,154-156 | 代码白名单 | PATH/PATHEXT/SYSTEMROOT/... /USERPROFILE | 集合 | 代码写死 | 验收实例只用这些环境变量 | 每次启动验收实例 | 无 | 不带密钥/代理进子进程 | P |
| 自我维护 | `_host_env` 屏蔽变量 | maintenance.py:158-170 | 代码集合 | 去掉 `GIT_DIR/GIT_WORK_TREE/GIT_SSH_COMMAND/SSH_ASKPASS/GIT_CONFIG_*` | 集合 | 代码写死 | git/gh 调用专用 | 每次调用 | 无 | 防 Git 重定向劫持 | P |
| 自我维护 | Runner 超时 | maintenance.py:117,598,625-631,747 | 代码默认值 | 默认 `180s`；gh `120s`；push `300s` | 秒 | 代码写死 | — | 每次命令 | 无 | 防挂死 | P |
| 自我维护 | 令牌正则 `TOKEN_PATTERN` | maintenance.py:172,176-188 | 代码正则 | `(github_pat_|gh[pousr]_)[A-Za-z0-9_]{20,}`，文件 `github-token.txt` | 只取第一条匹配行 | 代码写死 | — | 每次 git/gh | 明文文件 | 拒绝非 GitHub token 形态（github_token_setup.py:18-20） | P |
| 自我维护 | 验收实例费用上限 | web_agent.py:3714-3719 | 代码常量 | 日 `2` / 月 `20` 元 | 元 | 代码写死 | 与 models.json 限额取 min（3717-3719, 1526-1528） | 验收实例启动 | 无 | 限制验收花费 | P |
| 自我维护 | 验收实例配置复制 | maintenance.py:470-483 | 代码逻辑 | 复制 `characters/`、`persona/`、真实 `.env` | — | 代码写死 | — | 每次验收 | 临时目录，stop 时删 .env（556-559） | 明文密钥落在验收 config 目录，崩溃时可能残留 | S/P |
| 凭证 | `github-token.cmd` / `github_token_setup.py` | github_token_setup.py:13-23；github-token.cmd:1-11 | 交互式 getpass | 目标 `<仓库根>.parent.parent/secrets/github-token.txt` | 隐藏输入；非法则不保存 | 双击 cmd；无页面 | — | 写入后重启 Peter（23） | 明文 + 换行 | 不设文件 ACL；提示原文 `That does not look like a GitHub token (it starts with github_pat_ or ghp_). Nothing was saved.` | S |
| 凭证 | 维护 token 注入 | maintenance.py:587-598,598-616 | 进程环境 | 无文件则退回本机 gh 登录态 | `GH_TOKEN`/`GITHUB_TOKEN` + `credential.helper=!gh auth git-credential` | 自动 | 有文件优先 | 每次 git/gh | 无 | 不打印、日志脱敏（63-66） | P |
| 项目范围 | 项目发现上限 | workbench_scope.py:50,92 | 代码默认值 | `limit=5`、`max_depth=3` | 整数 | 代码写死 | — | 每次找项目 | 无 | 只搜 roots | P |
| 项目范围 | 项目标识 `MARKERS/SKIP_DIRS` | workbench_scope.py:21-28 | 代码常量 | `.git/pyproject.toml/package.json/...`；跳过 node_modules/.venv/secrets/.ssh 等 | 集合 | 代码写死 | — | 每次扫描 | 无 | 不把密钥目录当项目 | P |
| 项目范围 | 快照复制上限 | workbench_scope.py:29-30,179-180 | 代码常量 | `200MB` / `20000` 文件 | 字节/个 | 代码写死 | 超限即判定无法保留退路 | 每次快照 | 快照存 `data/<role>/snapshots/<id>` | 超限时聊天问 Lan、自主不做（veil_agent.py:5190-5196） | P |
| 项目范围 | 快照保留 `KEEP_SNAPSHOTS` | workbench_scope.py:193-208 | 代码常量 | `20` 份 | 份 | 代码写死 | — | 每次快照后 prune | 磁盘 | 删除最旧快照及其 git ref | P |
| 项目范围 | 受保护目录/数据重叠 | veil_agent.py:5176-5187 | 代码逻辑 | role 下 `memory/snapshots/workbench-home/maintenance/media/resident-state.json/long-task.json` | — | 代码写死 | — | 每次选目标 | 无 | 拒绝让工作台进入含 VEIL 数据的项目 | P |
| 项目范围 | 工作台根目录 | character.toml:122；veil_agent.py:5037-5038 | `[tools] folder` | `data/peter/workspace`（角色显式） | 仓库内相对路径 | 仅改文件 | 旧 `data/<role>/workbench` 一次性并入 `from-workbench/`（workbench_scope.py:263-300） | 重启 | 仓库文件 | 工作台默认活动范围 | A |
| Lab | `lab` | character.toml:119；tools_agent.py:433-448 | toml 字符串 | `'lab'`（工作区内单级目录） | 单级普通目录名，不能等于成长日记 | 仅改文件 | 无 | 重启 | 仓库文件＋工作区目录 | 决定“免确认写入”范围 | A |
| Lab | `lab_max_write_bytes` | character.toml:120；tools_agent.py:430-432,1567-1569 | toml 整数 | `200000` 字节 | 正整数；非法 → `实验室单次写入上限必须是正整数。` | 仅改文件 | 无 | 重启 | 仓库文件 | 单次写上限 | A |
| Lab | `python_sandbox` | character.toml:129；tools_agent.py:364；lab_sandbox.py:144-147,200-201 | toml bool | 代码 `False`（角色显式 true） | bool | 仅改文件 | 无 | 重启 | 仓库文件 | 开启后仍强制 Docker、无网络 | S |
| Lab 沙盒 | 镜像/资源 | lab_sandbox.py:30-33,382-422 | 代码常量 | `python:3.12-slim`；`256m`；`1.0` CPU；`64` pids；`--network none`；`--read-only` | — | 代码写死，无入口 | — | 每次运行 | 无 | 隔离与资源上限 | P |
| Lab 沙盒 | 超时/输出 | lab_sandbox.py:20-28,245-249 | 代码常量 | 默认 `15s`、最大 `30s`；输出 `50KB/1000 行`；脚本 `2MB`；生成文件 `20 个/5MB/20MB`；工作区 `50MB` | 秒/字节/个 | 代码写死（工具参数 `timeout` 可在 1..30 内改） | — | 每次运行 | 临时目录 | 防资源耗尽 | P |
| Lab 预览 | 预览预算 | lab_preview.py:18-30,370-373 | 代码常量 | `5MB/页`、`128 请求`、`20MB`、截图 `5MB`、`2 次/轮`、`30s`、视口 `1280×800` | — | 代码写死，无入口 | — | 每次预览 | 无 | 只读临时浏览器，外部请求阻断 | P |
| Lab 预览 | CSP | lab_preview.py:123-130 | 代码常量 | `default-src 'none'; ... sandbox allow-scripts allow-same-origin` | — | 代码写死 | — | 每个响应 | 无 | 禁弹窗/下载/表单/导航 | P |
| Lab 游戏 | 分数请求体上限 | lab_games.py:18；web_agent.py:2494-2500 | 代码常量（两处） | 两处都是 `2048` 字节 | 字节 | 代码写死 | 服务端只认 web_agent 的 2048 | 每次提交 | 无 | 防大 body | P |
| Lab 游戏 | 成绩文件上限 | lab_games.py:19,129-142 | 代码常量 | `2MB`；满了报 `Score file is full.` | 字节 | 代码写死 | — | 每次写入 | `<game>/scores.jsonl` | O_APPEND、nlink/路径校验、fsync、失败回滚 | R |
| Lab 游戏 | 游戏资源上限/能力数 | lab_games.py:20,56-58,75-80 | 代码常量 | 单资源 `5MB`；能力表 `64` 条 FIFO | — | 代码写死 | — | 每次请求 | 内存 | 链接失效、目录逃逸拒绝 | P |
| Lab 游戏 | `/game/` 同源校验 | web_agent.py:2000-2020,2482-2507 | 代码逻辑 | Host=127.0.0.1:port、POST Origin 必须同源、Sec-Fetch-Site 同源 | — | 代码写死 | — | 每请求 | 无 | 防跨站提交成绩 | P |
| 客档案 | 清理阈值 | guest_profiles.py:19-27,102-150 | 代码常量 | 轮询 `5s`；主人死 `24h`；旧前缀 `7d`；重试 `1440`（约 2h）；兜底 `7d` | 秒/次 | 代码写死，无入口 | — | 后台线程 | 临时目录 | 只删带 owner 标记的自家目录 | R/P |
| Windows 应用 | 批准文件/动作 | tools_agent.py:275；windows_interaction.py:1064-1138 | `data/<role>/windows-approved-apps.json` | 无 | 应用 id 正则 `_APP_ID`；动作固定 8 种 | 设置页「可操作的软件」卡片（chat.html:234-239；chat.js:2219-2230）；先打开应用再添加 | 与 `windows-apps.toml` 静态白名单合并（1068-1081） | 即时（内存更新）＋重载后仍生效 | data/ JSON | 只给点击/切换类动作，不输入文字（1154-1155） | U/S |
| 更新 | `update-main.cmd` | update-main.cmd:27-110,112-146 | 脚本 | ff-only 更新 main；本机改动先 stash；冲突转 `backup/main-before-update-*` | — | 双击脚本；无页面 | 与维护任务无关 | 手动 | data/、.env、secrets 不在仓库，保留 | 不碰 data，不改本地分支 | U/R |

---

## 3. 重复 / 冲突 / 隐性覆盖

1. **保存页面任何一项都会把 `container_image` 写成 `veil-workbench`（宿主模式被悄悄切到容器模式）。** 页面 GET 走 `status()` → `load_state()`，文件不存在或没有该键时返回默认镜像 `veil-workbench`（`workbench_switch.py:23-26`）；POST `_save_workbench` 先 `load_state` 再 `save_state`，而 `save_state` 固定写全四个键（`web_agent.py:3110-3135`；`workbench_switch.py:14,37-43`）。所以 `character.toml` 里 `container_image = ''`（或没有 `[executor]`）时，Lan 只是在页面加一个项目文件夹或点一下「开」，磁盘上就出现 `"container_image": "veil-workbench"`；重启后 `workbench_ready()` 改为要求 Docker（`veil_agent.py:4988-4990`），运行改走容器。页面没有任何地方显示或编辑这个镜像名。
2. **两份 `workbench.json` 校验器行为不同。** 启动路径要求**键名与类型严格匹配**，任何未知键都会让 Peter 起不来（`veil_agent.py:826-828`）；页面路径忽略未知键、把 `enabled` 强制 `is True`、`project_roots/web_hosts` 强制转字符串（`workbench_switch.py:27-34`），并在下一次保存时把未知键**静默丢掉**（`save_state` 只保留 `KEYS`）。结果：一个拼错的键（如 `web_host`）会让 Peter 无法启动，但设置页仍能正常打开；此时打开页面并保存一次就“修好”了，用户看不到任何解释。
3. **页面 `add_folder` 允许加入永远会被运行期拒绝的目录。** 页面只拒绝与 `data/characters/persona` 有包含关系的路径（`workbench_switch.py:52-55`）；运行期 `_workbench_target` 还会拒绝任何包含 VEIL 数据的项目（`veil_agent.py:5176-5187`）。把整个仓库根加进去会“保存成功”，但任务永远回 `... it contains VEIL's own data (memory, budget ledger). Pick another project.`。
4. **`web_hosts` 在宿主模式与维护任务中被静默忽略。** 只有 `container and not maintenance` 才把它传给执行器（`veil_agent.py:5127`），且执行器内部 `web_browser = bool(container and web_hosts)`（`executor_opencode.py:1173-1175`）。宿主模式下页面照样展示/保存这个列表，实际不生效。
5. **`web_hosts` 管不住 VEIL 自带的浏览器 MCP。** 只要 `[web] enabled` 且 `browser_interaction = true`（`browser_tools.py:178-183`）且不是维护任务，`browser_gateway` 就会传给工作台（`veil_agent.py:5128-5129`），与 `web_hosts` 是否为空无关；`web_hosts` 只影响容器内 Playwright 和代理 egress。两项设置的边界没有任何页面说明。
6. **`web_hosts` 校验口径不一致。** 配置侧允许单段主机名（正则允许 `localhost`，`veil_agent.py:804-807`），页面侧额外要求含 `.`（`workbench_switch.py:68`），错误文案 `请填网站地址，例如 github.com。`；上限都是 50，但两处常量分别硬编码（`veil_agent.py:804`；`workbench_switch.py:16`）。
7. **`[web] github_repository` 是“只改文案”的键。** 它只拼进工具描述 `Your source repository is {home}.`（`github_readonly.py:90-93`），不参与任何仓库白名单：任何公开 owner/repo 都能读（仅要求 `private is False` 且 `visibility == 'public'`，`github_readonly.py:174-175`）。很容易被误解为“只允许研究这个仓库”。
8. **`VEIL_GITHUB_READONLY_TOKEN` 写在 `.env` 里不会生效。** 代码只读 `os.environ`（`github_readonly.py:288-290`），仓库没有任何 `load_dotenv`（全仓库 grep 无命中），`.env` 只在具体调用点用 `dotenv_values` 读取（`veil_agent.py:5024,5032`；`web_agent.py:940-941`）。而工单 154 写「由用户在本机 `.env` 配置独立 `VEIL_GITHUB_READONLY_TOKEN`」（`docs/tickets/154-github-readonly.md:12`）。实际只能用 Windows 环境变量；`.env.example:1-15` 也没有示例键。
9. **`maintenance_hours` 名字不像“时长上限”，实际是任务存活截止。** 它只在 `begin` 时写成 `deadline`（`maintenance.py:282-291`），之后 deliver/merge/workbench/审批都受它拦截（1076-1078, 1262-1264, 1426, 1451）。改配置不会延长已建任务（deadline 已写进任务 JSON），也没有页面看这个时间。
10. **`workben_switch.CHARACTER = 'peter'` 写死。** 设置页/脚本永远读写 `data/peter/workbench.json`（`workbench_switch.py:12,19-20`），即使 `web_agent.py --character ghost` 也如此；而 `veil_agent.workbench_local_settings` 是按真实角色 id 读的（`veil_agent.py:810-817`）。多角色时会“页面改了另一个角色的文件”。
11. **步骤上限只在工单里，代码里没有。** 工单 237 写「每次 30 步」（`docs/tickets/237...:11`），但 `DEFAULT_LIMITS`/VEIL 传入的 limits 只有 `max_seconds/repeat_limit/run_budget_cny`（`executor_opencode.py:40`；`veil_agent.py:5069`），执行循环没有任何步数上限，只有空闲 3 轮退出（`executor_opencode.py:1390-1392`）。
12. **密钥文件屏蔽深度与工单描述不符。** 工单 238 写「6 层目录内」（`docs/tickets/238...:11`），代码 `secret_files(workdir)` 未传 `max_depth`（`executor_opencode.py:1137`），默认 `None`＝全深度（997-1014）。
13. **容器内存键是死键。** `container_command` 支持 `container.get('memory','2g')`（`executor_opencode.py:1049`），但 VEIL 构造的容器字典只有 `image`（`veil_agent.py:5027`），也没有任何配置键能传 `memory`。
14. **`github_readonly` 的 MCP 调用条件很窄。** 只有 `resource == 'search_code'` 且**没有** `ref`/`path` 时才用官方 MCP（`github_readonly.py:288`）；带 `ref` 或 `path` 会退回匿名逐文件扫描（291-325）。页面/说明没有任何提示，用户会以为“配了 token 就全走官方索引”。
15. **`max_result_bytes` 是两个工具域共用的隐藏耦合。** GitHub 只读与文件工具都读 `[tools] max_result_bytes`（`github_readonly.py:83`；角色值 8192，`character.toml:128`）；把它调太小会让 GitHub 直接报 `Result budget is too small for source and continuation; increase max_result_bytes.`（`github_readonly.py:341-342`）。
16. **`workbench.json` 非对象 JSON 会让接口 500 而不是明确报错。** `load_state` 在 `isinstance(data, dict)` 检查之前就调用 `data.get(...)`（`workbench_switch.py:27-31`），若文件是数组会抛 `AttributeError`；`_get_workbench` 只捕获 `OSError, ValueError`（`web_agent.py:3096-3099`）。启动路径则会给出明确 `UserError`（`veil_agent.py:826-828`）。
17. **自我维护与工作台共用 `enabled` 开关。** `MaintenanceManager` 只在 `executor.enabled` 为真时创建（`veil_agent.py:2275-2284`），关掉工作台就等于关掉自我维护与 `maintenance` 工具；页面上的「开/关」没有任何地方说明这个连带影响。
18. **验收实例复制真实 `.env`。** `AcceptanceInstance._prepare` 把仓库根 `.env` 复制到验收 config（`maintenance.py:481-483`），只在正常 `stop()` 时删除（556-559）；崩溃/断电会把含全部 API Key 的 `.env` 留在 `data/<role>/maintenance/acceptance-<ticket>/config/.env`。同时验收实例被强制 `executor.enabled=False`（`web_agent.py:3709`），所以验收里不能测工作台本身。
19. **`github_repo` 与 `[web] github_repository` 同名不同义。** 前者用于 `gh --repo` 与 origin 校验（`maintenance.py:647-649, 1311-1312`），后者只是研究提示（`github_readonly.py:90-93`）；两者不一致时没有校验。

---

## 4. 页面与入口现状

| 能力 | 现有入口 | 位置 | 限制 |
|---|---|---|---|
| 工作台开/关 | 设置页「工作台」分段按钮 | `desktop/chat.html:225-226`；`desktop/chat.js:2180-2182,2212-2213`；API `GET/POST /api/workbench` `web_agent.py:2444-2445,3441-3442,3093-3136` | 保存后提示 `重启后生效`（chat.js:2197）；实际需重启后端 |
| 项目文件夹 | 「添加项目文件夹」调 Electron 目录选择；每项「移除」 | `chat.html:228`；`chat.js:2184-2188,2214-2217`；`desktop/main.cjs:664-668` | 只能选已存在目录；不能填路径文本 |
| 工作台站点 | 「工作台里能上的网站」列表＋表单 | `chat.html:229-231`；`chat.js:2190-2196,2211` | 仅容器＋非维护任务生效；不能编辑 `container_image` |
| 运行环境状态 | 卡片底部提示 | `chat.js:2197` | `还没有运行环境，双击 workbench-switch.cmd`；无「构建镜像」按钮 |
| 工作台开关/文件夹（无桌面时） | `workbench-switch.cmd` → `workbench_switch.py` 菜单 | `workbench-switch.cmd`；`workbench_switch.py:129-160` | 菜单文案：`1 开启 2 关闭 3 加一个项目文件夹 4 清空项目文件夹 0 退出（改完要重开 Peter）` |
| 预算/步数/命令路径/容器镜像/维护仓库/工时 | **无页面** | — | 只能改 `characters/peter/character.toml [executor]`；`[executor]` 出厂不存在，需自己加表 |
| GitHub 只读研究 | **无页面**；只有 `[web] github_readonly/github_repository` 文件键 | `character.toml:204-212` | 令牌只能设 OS 环境变量；无测试按钮 |
| VEIL 自我维护 | **无页面**；只有 Peter 的 `maintenance` 工具（群组 `build`） | `maintenance.py:1514-1540`；`tool_groups.py:40` | 任务状态只能通过 Peter 问或读 `data/peter/maintenance/tasks/*.json` |
| GitHub 令牌 | `github-token.cmd` 双击（getpass） | `github-token.cmd:1-11`；`github_token_setup.py:12-24` | 不校验文件权限；写死默认目标路径 |
| 可操作的软件 | 设置页「可操作的软件」卡片（列表/添加/移除） | `chat.html:234-239`；`chat.js:2219-2230`；`web_agent.py:3075-3091,2855-2876` | 必须先手动打开应用；只能加点击/切换类动作 |
| Lab 目录/写入上限/沙盒总开关 | **无页面** | `character.toml:116-130` | 只能改文件，重启生效 |
| Lab 预览/小游戏 | 无设置页；Peter 用 `preview_lab_page`/`open_lab_game` 工具，Lan 只拿到临时链接 | `lab_preview.py:338-352`；`lab_games.py:38-58` | 预览次数/资源上限全是代码常量 |
| `/game/` 路由 | Web 服务自动提供 | `web_agent.py:2423-2424,3363-3365` | 能力链接 64 条；无配置项 |
| 维护/工作台运行日志 | **无页面**；磁盘文件 | `executor_opencode.py:542-560` | 需手动开目录看；30 份滚动 |
| 更新正式版 | `update-main.cmd` 双击 | `update-main.cmd:1-160` | 与维护任务无联动，需 Lan 手动执行 |

**结论：** 页面只覆盖「开关 / 项目文件夹 / 站点 / 可操作软件」四项；预算、超时、重复上限、命令路径、容器镜像、维护仓库、工时、GitHub 令牌与所有 Lab 参数都“只能改文件”，而 `DEFAULT_LIMITS/PROXY_MAX_REQUESTS/RESULT_CHARS/RUN_LOG_*/LAB_MAX_*/CSP` 等是“代码写死，无入口”。

---

## 5. 生效时机与持久化

| 配置 | 生效时机 | 依据 | 重启后 | 更新/备份恢复后 |
|---|---|---|---|---|
| `[executor]` 全部键 | 重启后端（加载角色时） | `veil_agent.py:1131-1151` | 保留 | 仓库文件，`update-main.cmd` 会同步；本机手改可能产生 stash/冲突（`update-main.cmd:45-53`） |
| `workbench.json` 四键 | 重启后端；页面提示「重启后生效」 | `veil_agent.py:1132-1134`；`chat.js:2197` | 保留 | `data/` 被 gitignore（`.gitignore:8`），更新不覆盖；备份包含 data 时随备份恢复 |
| 工作台单次预算申请 | 下一个工具调用（审批后立即改本次 limits） | `veil_agent.py:5004-5020` | 不保留（仅本次运行） | — |
| 当天额度抬高 | 立即（内存） | `veil_agent.py:1341-1352` | 重启失效 | 不持久化 |
| `github_token_file` | 每次 `git`/`gh` 调用现读 | `maintenance.py:587-616` | 立即 | 文件在仓库外，更新不影响 |
| `VEIL_GITHUB_READONLY_TOKEN` | 下一次 `search_code` 调用（同一进程 environ 固定） | `github_readonly.py:288-290` | 需重开进程才读到新环境变量 | 不在备份范围 |
| `maintenance_hours` | 下一个新建维护任务 | `maintenance.py:282-291` | 保留 | 已有任务 deadline 写在任务 JSON，不随配置变化 |
| `maintenance_repo/worktree_root/github_repo` | 重启 | `veil_agent.py:2278-2284` | 保留 | 仓库文件 |
| 自我维护任务状态 | 每步实时写盘 | `maintenance.py:190-280` | 保留 | 在 `data/`，随数据备份 |
| web 工作台权限批准（普通任务） | 本次任务内 | `executor_opencode.py:179-191` | 不保留 | — |
| web 工作台权限批准（维护任务） | 本次任务内，指纹持久化 | `veil_agent.py:5123-5126`；`maintenance.py:1420-1447` | 任务未结束则保留 | 任务 JSON 在 data/ |
| Lab/`[tools]` | 重启 | `tools_agent.py:357-365,429-448` | 保留 | 仓库文件 |
| Windows 应用批准 | 即时 | `web_agent.py:3075-3091`；`windows_interaction.py:1082-1094` | 保留 | `data/` |
| 容器镜像 | 下次运行（镜像已构建） | `veil_agent.py:4988-4990` | 保留（Docker 本地） | 不在 VEIL 备份范围，换机需重建 |

---

## 6. 风险与建议分类

- **P（程序安全约束，必须底层强制，不给用户改）**：`ASK_COMMANDS/MAINTENANCE_DENY/SECRET_READ_GLOBS`（`executor_opencode.py:156-175`）、网络锁与 iptables（1048-1098）、`TaskScope` 审批复用（179-191）、自主一律拒绝（382-383）、`FORBIDDEN_PATH`（`maintenance.py:40-42`）、`_host_env` 屏蔽（158-170）、验收实例资源/费用上限（`web_agent.py:3714-3719`）、Lab 沙盒资源上限（`lab_sandbox.py:20-33`）、`/game/` 同源校验与字段白名单（`web_agent.py:2000-2020`；`lab_games.py:100-114`）、`MAX_BODY`（`web_agent.py:48`）、游戏资源/分数上限（`lab_games.py:18-21`）、CSP（`lab_preview.py:123-130`）。
- **S（敏感权限，需授权/审批）**：`command`（任意可执行路径）、`container_image`（自定义镜像绕过标签检查）、`maintenance_repo/github_repo/github_token_file`（对哪个仓库推送、用哪个凭证）、`github-token.cmd` 写入的明文 token（无 ACL 校验）、验收实例复制真实 `.env`（`maintenance.py:481-483`）、`windows-approved-apps.json`（给桌面操控权限）、`python_sandbox`。
- **U（用户常用，建议进管理中心页面）**：工作台开关、项目文件夹、站点列表（含“仅容器生效”的说明）、自主工作台单次预算、`max_seconds/repeat_limit/run_budget_cny/wake_budget_cny`、GitHub 只读开关、Windows 应用列表、Lab 目录与写入上限。
- **A（高级设置，需写清影响）**：`container_image/maintenance_hours/maintenance_repo/worktree_root`、`max_result_bytes`、`lab` 改名、快速路径 `command`、自定义镜像构建按钮。
- **R（只读运行信息）**：`image_ready`、运行日志列表/最近一次停止原因、维护任务状态与 deadline、`workbench.json` 实际解析结果、GitHub 只读是否启用，以及（建议新增）当前 `VEIL_GITHUB_READONLY_TOKEN` 是否检测到。
- **X（冗余/废弃，给清理依据）**：
  - `[web] github_repository` 只改提示文案（`github_readonly.py:90-93`）→ 要么变成真白名单，要么删。
  - `workbench_switch.IMAGE` 与 `[executor] container_image` 两套默认（`workbench_switch.py:13`；`veil_agent.py:1145`）。
  - 容器 `memory` 死键（`executor_opencode.py:1049`；`veil_agent.py:5027`）。
  - `REMOVED_WORKBENCH_KEYS` 三个旧宿主开关（`veil_agent.py:799`）仍保留剔除逻辑。
  - 工单 237「30 步」与 238「6 层」「步数×3+5」三处描述已无对应代码（`docs/tickets/237...:11`；238:11-13）。
  - `MAX_SCORE_BODY` 与 `web_agent.py:2498` 的 2048 重复常量（`lab_games.py:18`；`web_agent.py:2498`）。
  - `github_readonly.MAX_FILE_BYTES` 与文案 “up to 1 MB” 多处重复但一致，可集中为常量。

---

## 7. 无法确认项（明确原因，不猜）

1. **本机 `data/peter/workbench.json` 的实际内容与是否存在**：只读范围是源码快照，快照内无 `data/`（`.gitignore:8`），禁止读正式运行目录。
2. **正式运行目录 `characters/peter/character.toml` 是否有本机 `[executor]`**：快照 main 没有该表；正式目录按规则未读，无法排除本机手改。
3. **`VEIL_GITHUB_READONLY_TOKEN` 当前是否设置**：只影响运行进程的环境；审计进程的环境不等于 Peter 后端的启动环境，且正式目录未读。代码层面只能确认“`.env` 不会被加载”。
4. **`本机 secrets 目录的 github-token.txt` 是否存在、权限/ACL、内容是否有效**：在正式运行目录之外且含凭证，未读；代码里也没有任何权限检查可确认。
5. **`gh` 是否已登录、登录账号是否有该仓库权限**：需要执行 `gh auth status` 或读 `.config/gh`，均未在只读源码范围内；代码只会在运行时检查并返回 `GitHub is not ready: ...`（`maintenance.py:946-954`）。
6. **Docker Desktop 是否运行、`veil-workbench` 镜像是否存在**：页面通过 `docker image inspect` 判断（`workbench_switch.py:85-97`），本审计未执行该命令；设置页会显示“还没有运行环境”或正常，取决于本机。
7. **`models.json` 当前的日/月限额**（决定工作台预留能拿到多少）：`data` 不在快照内；只确认代码默认 10/300（`model_settings.py:29-30`）。
8. **工作台实际项目列表、快照目录与运行日志内容**：均在 `data/peter/snapshots`、`data/peter/workbench-home/runs`，正式目录未读。
9. **维护任务的当前状态（tasks/*.json）与现有 worktree**：在 `data/peter/maintenance` 与仓库父目录 `worktrees`，未读。
10. **Lab 目录里现有游戏/脚本、`scores.jsonl` 内容**：属于运行时数据，未读。
11. **`desktop/node_modules/electron` 是否存在**（决定验收实例能否起窗口）：仓库未跟踪 `desktop/node_modules/`（`.gitignore`），快照里没有，无法确认本机状态；代码在不存在时静默跳过窗口（`maintenance.py:530-537`）。
12. **`workbench_switch.py` 在本机是否曾构建过镜像、是否有自定义镜像名**：取决于本机 Docker，未执行。

---

### 附：本次取证使用的关键取证命令（可复现）

- `grep -rn '\[executor\]' --include=*.py --include=*.md --include=*.toml .`（确认仅测试与工单文档出现该表）
- `grep -n '' workbench_switch.py`（逐行核对页面默认值与保存行为）
- `grep -rn 'load_dotenv' --include=*.py .`（无结果，确认 `.env` 不注入 `os.environ`）
- `grep -rn 'VEIL_GITHUB_READONLY_TOKEN' .`（仅 `github_readonly.py:24` 与工单 154）
