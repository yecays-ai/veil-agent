# H · 系统安全、维护与诊断 —— VEIL 全局配置审计（域 8 取证）

- 审计对象：yecays-ai/veil-agent 最新 main = e7e7992 的只读快照 main 快照
- 审计方式：只读源码 + 文档 + Electron 前端；未启动服务、未读写正式运行目录 正式运行目录、未执行任何备份/恢复/更新命令
- 证据格式：`文件:行号`。所有默认值、上限、提示文案均从源码原文摘录，不改写
- 分类码：U=用户常用设置｜A=高级设置｜S=敏感权限｜R=只读运行信息｜P=程序安全约束｜X=冗余/废弃

---

## 1 执行链

### 1.1 诊断概览链（页面 → /api/diagnostics/overview → 快照文件）

1. 入口：`desktop/chat.html:155-179` 的 `#diagnostics-view`，导航按钮 `desktop/chat.html:38`（`data-page="diagnostics"`）；`desktop/chat.js:2528` 切页时调用 `window.diagnosticsLoad`。
2. 前端请求：`desktop/diagnostics-page.js:352-374` 并发 GET `/api/diagnostics/overview` 与 `/api/diagnostics/links`；失败时整页显示「诊断读取失败」（`diagnostics-page.js:34-53`），不会伪装成「没有问题」。
3. 传输/校验：Electron 主进程白名单 `desktop/main.cjs:491-510`（overview/links 不允许任何 query），`main.cjs:540-541` 拒绝未允许请求；服务端 `web_agent.py:1981-1998` 校验 `Host`、`Origin`、`X-Token`（`hmac.compare_digest`），任一不符 403 并丢弃 body。
4. 服务端处理：`web_agent.py:2954-2968`。服务未就绪（`hub.chat is None`）直接 503「服务还没准备好。」（2958-2960）；异常时 503「诊断数据暂时读不到。」（2964-2967）。
5. 数据组装：`diagnostics.overview`（`diagnostics.py:1091-1115`）→ `service_info`（1058-1088，读 `.git/HEAD`、`refs/remotes/origin/main`、`GlobalMemoryStatusEx`、`_dir_size` 限 4000 文件、缓存 300 s）+ `module_states`（466-628）+ `config_findings`（353-440）+ `recent_issues`（825-832）。
6. 快照写入：`web_agent.py:2963` 调 `diagnostics.snapshot_if_stale`；`diagnostics.py:1317-1324` 仅在文件缺失或 mtime 超过 `SNAPSHOT_MAX_AGE=300`（41）时写 `data/<角色>/diagnostics-snapshot.json`（路径 212-213，原子替换 1305-1314）。
7. 缺省/非法/耗尽行为：无快照文件 → CLI 报告退回「配置校验需要服务运行」（1229-1233）；快照过期 → 只要页面再打开就会重写；写盘失败静默返回 False（1313-1314），页面仍显示实时 overview。

### 1.2 诊断 issues 时间线链

1. 入口：`web_agent.py:2970-2988`；模块过滤 `module`、条数 `limit`（`isdigit` 校验，1..200，否则 120，2978-2979）；时间窗 `_hours`：`max(1, min(720, int(raw)))`，非数字回落 72（2949-2952）。
2. 汇总：`diagnostics.recent_issues`（825-832）→ `collect_issues`（662-798）。最多 `ISSUE_LIMIT=200` 条（42、798），排序键为 `time` 或 `observed_at`（797）。
3. 数据源与阈值：各账本尾部行数固定为 tools 1200 / providers 400 / contexts 400 / autonomy 100 / memory_diag 200 / experience 80（469-474）；时间窗 `hours` 再夹到 1..720（665）、cutoff（668）。
4. 关联判定：无 request_id/run_id/call_id 的行强制 `confirmed=False`（644-645）；拒绝行按 `classify_denial` 分级（701-709）；恢复判定只认同 id 的后续成功（801-822）。
5. 耗尽：只回 `issues[:limit]`（798）；`total` 是截断后的长度（`web_agent.py:2988`），不是全量。
6. 页面：`diagnostics-page.js:196-198` 过滤掉 `severity==='info'`（denial 除外）再 `.slice(0,30)`；`counts.issues` 却是 overview 的全量计数（`diagnostics.py:1113`），所以「N 条记录」与可见条数天然不一致。

### 1.3 诊断 trace / report 链

1. trace：`web_agent.py:2990-3017`。三个 id 参数各自过 `diagnostics.valid_id`（`diagnostics.py:144-145`，正则 `[A-Za-z0-9_:.+\-]{1,120}`，38），不合法 400「参数不对。」；一个都没有 400「需要 request_id、run_id 或 call_id 之一。」（2997-3006）。
2. 链组装：`diagnostics.trace`（837-953）；请求快照列表 `snapshots` 只取同 request_id 的前 8 个（955-970），目录 `data/<角色>/request-snapshots`（958）。
3. report：`web_agent.py:3019-3039`，`scope=overview|issue`，issue id 同样过 `valid_id`（3028-3030）；`report_text`（1215-1302）在有 hub 时实时算，无 hub 时用快照 + 重读 issues（1220-1226）。
4. 报告硬上限：后台失败 12 条（1272）、未分类拒绝 3 条（1284）、其余异常 20 条（1292）；末尾强制 `_redact`（1302，正则 28-34）。
5. 页面「复制」按钮走 `/api/diagnostics/report?scope=issue&id=…`（`diagnostics-page.js:217`）；「复制诊断报告」固定 `scope=overview&hours=72`（379）。

### 1.4 运行安全摘要（RuntimeMonitor）链

1. 构造：`web_agent.py:3729-3730` `RuntimeMonitor(store, cfg.id, activity)`，路径 `data/<角色>/runtime-status.json`（`runtime_monitor.py:18`）。
2. 读取：启动时 `_load`，只接受「字段集合完全等于 6 个键、值全为 str、state/classification 在枚举内」的行（27-44）；否则丢弃用默认 `healthy/healthy/ready/'No action is needed.'`（20-21）。
3. 写入：只覆盖一行、fsync + 原子替换（46-61）；单次运行没有历史。
4. 触发：记忆工具失败（`memory_agent.py:405-408,856-858`）、工具出错（`veil_agent.py:5323-5325`）→ `observe_memory`/`observe_tool`（77-172）。
5. 安全动作：结果不确定的写（`error_code in {'unknown','timeout'}` 且工具属 `_MEMORY_MUTATIONS={'hold','trace'}`）→ `_save_unknown_memory`（70-75、105-116）→ `_pause` 调 `activity.set(False)`（63-68），即自动关掉自主活动；pause 失败则 code 变 `memory_pause_failed` 并提示人工处理（70-75）。
6. 对外：`/api/events` 返回 `runtime_status`（`web_agent.py:1908,1918`）；无 monitor 时默认「运行正常。」（217-218）。诊断 issue 只把 `attention/paused` 当异常（`diagnostics.py:753-761`）。
7. 耗尽/缺省：文件损坏 → 当无文件处理（43-44）；无法写盘 → 静默保留在内存（57-61）；没有轮询、没有模型调用、没有历史（模块 docstring 9、78-80）。

### 1.5 审批链（对话内审批 + 持久待批准）

1. 对话内审批：`web_agent.py:1799-1817`；超时 `APPROVAL_WAIT_SECONDS=600`（111）。到点返回 `'timeout'`，push「审批等待超时，操作没有执行。」（1815-1817）；停止服务时返回 `'cancel'`（1810-1812）。只有当前 `self.pending` 能回答一次（1819-1825）。
2. 持久审批：`pending_approvals.py:70-88`；`ttl_hours=TTL_HOURS=24`（13、22-25），写入 `expires_at`（83）；读取时 `_expire` 把过期项标 `expired`（58-68），已处理/过期再决策抛 UserError（99-100）→ `web_agent.py:3450-3451` 映射 409/400。
3. 页面：`chat.js:1669-1714` 每 10 s 拉 `/api/pending-approvals`（1670），卡片显示 `请求于 … · 过期 …`（1692）；决策按钮「同意一次 / 拒绝」（1695-1698）。
4. 上下文注入：下一次醒来只带 5 条 pending + 5 条未告知决策（128-131）；`mark_told` 在唤醒请求真正发出后才确认（135-145）。
5. 耗尽/非法：`MAX_PROMPT=600` 截断（14、71）；列表只保留末尾 200 条（86）、展示最近 20 条已决（112）；TTL 在建项时写死，改代码也不影响已存在条目。

### 1.6 停止 / 退出链

1. 停止当前回合：`POST /api/stop`（`web_agent.py:3413`）→ `_post_stop`（2763-2767）→ `hub.stop_turn`（1837-1849）：置 `chat.user_stop`、把待审批答复成 `cancel`、push「已叫停，等这一步说完就停。」。只结束当前回复，不退出服务（2766-2767 返回 409 `not replying` 当没在回复）。
2. 退出服务：`POST /api/exit`（3454）→ `_post_exit`（3229-3236）→ `hub.request_stop`（1443-1456，返回 `accepted|stopping|stopped`）。前端按钮 `chat.js:2097-2120` 先 `confirm('确定退出 Peter 网页服务吗？')`；桌面菜单「退出 Agent」走 `main.cjs:804-822`，`/api/exit` 失败也只关壳（817-821）。
3. 停止后的清理：`web_agent.py:3782-3785` 关闭 MCP、`hub.stop()`、`time.sleep(1.5)` 让打开的页面读到最终状态；`finally` 删 `data/web_service.json`（3810-3816）。
4. 自主活动开关：`data/<角色>/resident-state.json`（`veil_agent.py:1177-1217`，路径 `web_agent.py:3728`）；页面按钮 `chat.js:1756`；写失败回滚旧值并报「能力记录无法保存，未切换自主活动状态」（`veil_agent.py:2708-2721`）。
5. 缺省/非法：没有 resident-state.json → `enabled=False`（1189-1190）；JSON 损坏 → 同样 False（1191-1192），不报错。

### 1.7 费用冻结链（账本待核实 → 全部付费请求停发）

1. 产生：恢复对账 `backup_restore.py:610-624`、费用账本修复 `data_repair.py:130-142` → `ledger_recovery.mark_unverified`（277-292）。标记文件 `data/costs-unverified.json`（15、212-217），证据 `data/costs-unverified.jsonl`（16）。
2. 保护：标记文件不可读时**不覆盖**、继续冻结（282-284）；证据追加前先读旧文件去重（244-274）。
3. 使用点：`Budget._require_verified`（`veil_agent.py:1365-1369`）在每次预留前抛 `BudgetExceeded`：「费用账本有待核实的记录，已暂停所有付费请求，未发送。请先关闭 VEIL…运行 data_repair.py ledger-status / ledger-verify」。
4. 次生行为：`recover_interrupted` 在冻结时直接 `return 0`（1692-1693），不结算任何遗留预留；`/cost` 输出前加警告（1855-1856）。
5. 解除：`data_repair.py ledger-verify`（172-188），必须输入 `VERIFIED`，标记改名 `costs-unverified.verified-<stamp>.json`（`ledger_recovery.py:348-353`）。
6. 耗尽/非法：标记存在但无明细 → 拒绝自动确认、继续暂停（181-183）。

### 1.8 冷备份链（create / pre-update）

1. 入口：命令行 `backup_restore.py:947-961`（`create|verify|restore|pre-update|usage`）；更新前自动备份 `backup_restore.py:897-905`（`pre_update_backup`），由 `update.bat:51` 调用。
2. 备份位置：`read_env_value(root, 'VEIL_BACKUP_DIR')`（31、874-894）——先进程环境、后 .env（876-880），去引号（891-892）。缺省：抛 `BackupError`「没有设置备份位置。请在 .env 里加一行 VEIL_BACKUP_DIR=X:\VEIL-backups（另一块磁盘）…」（901-904），update.bat 因此停更（`update.bat:133-138`）。
3. 校验：目标不能是符号链接/项目内（337-343）；冷锁 `data/session.lock`（213-266，`_FileLock` 用 msvcrt LK_NBLCK），服务在跑直接「VEIL 仍在运行或正在写入，请先完整关闭服务后重试。」（239）；磁盘空间 `needed + max(64MB, needed//10)`（316-329）。
4. 内容：`PAYLOAD_ROOTS=("data","characters","persona",".env")`（23），逐个必须存在且非符号链接（163-176）；桌面端只带 `desktop.json` 与 `appearance-background.{png,jpg,jpeg,webp}`（27-30、96-109）；排除 `data/session.lock`、`data/web_service.json`、`data*/*.tmp`、`memory/_agent` 运行残留与 tmp（146-160、396-400）。
5. 落盘：先写到 `destination/.veil-backup-<uuid>.tmp`，逐文件 sha256 写 manifest，`manifest.sha256` 校验，`verify_backup(stage)` 通过后才 `os.replace` 成 `VEIL-Agent-Backup-<UTC 时间戳>-<8 hex>`（345-419）；任何异常删除半成品（416-419）。
6. 缺省/耗尽：没有 `--destination` 时 create 必须给（951）；备份目录不存在会创建（349）；不加密、不上传，README.txt 明写可能含 .env 里的 API Key（407-411）。

### 1.9 恢复链（restore / --replace）

1. 入口：`restore_backup`（662-871），`--replace` 标志 957；先完整 `verify_backup`（667）——清单哈希（450-455）、格式/布局版本（462-465）、组件清单必须恰为 `[data,characters,persona,.env]` 或再加 `desktop-data`（466-469）、Windows 大小写重复、符号链接、每个文件 size+sha256（472-533）。
2. 目标校验：目标不能符号链接、不能与备份互相包含（677-683）；非 replace 时同名文件冲突直接拒绝并提示用 `--replace`（812-818）。
3. replace：对每一项 `data/characters/persona/.env` 存在成分要求输入精确 `RESTORE`（736-741），否则「未确认替换，目标目录未改动。」；随后 `safety = <target>.before-restore-<stamp>-<8hex>`（742-743），写恢复标记（758、821），整体 `os.replace` 移入安全副本再装入备份（760-770）。
4. 失败：完整性复核失败 → 回滚已装/已移项（781-802）；回滚不全时报错并保留安全副本路径（794-797）；恢复中途崩溃由 `.veil-restore-incomplete.json` 兜底（40、62-87）；`Store.__init__` 见到该标记直接拒绝启动并给出下一步命令（`veil_agent.py:435-437,449-465`）。
5. 账本对账：`_reconcile_ledger`（572-624）按 `(id,type)` 合并 reserve/dispatch/settle，冲突/损坏行进 `costs-unverified.jsonl` 并冻结付费（610-624）。
6. 桌面端：只有「live 目录没有这些文件且 VEIL.exe 未运行」才写回原位置，否则放 `restored-desktop-data-<stamp>` 并提示手动复制（627-659）。`_desktop_app_running` 用 `tasklist`，超时 10 s，判断不了就当作运行中（112-123）。
7. 持久化：安全副本、标记、恢复出来的数据都留在磁盘；程序不删除任何安全副本（852-859）。

### 1.10 brain.db 备份 / 恢复链

1. 在线备份：`brain_backup.backup`（96-110）用 SQLite backup API；结果必须 `quick_check == 'ok'` 且 `digest_ok`，否则抛错（108-109）。`snapshot` 重新计算 quick_check、canonical digest、10 张表计数（19-20、37-61）。
2. 恢复前置：备份自身校验（117-127）；`_assert_stopped` 检查 `brain-write.lock` 可加锁、且不存在 `brain.db-wal`/`-shm`（64-93），否则「请完全关闭 VEIL 后重试」。
3. 保留现场：替换前复制 `brain.db.pre-restore-<stamp>` 并导出 `…-export/`（133-140）；合并备份之后新增的 memories/events/ledger 行（148-155、198-272），任何一步失败都回滚候选文件，live 库不变（268-270）。
4. 路径：`data/<角色>/brain.db`（`brain_authority.py:23`、`brain_authority_init.py:88`），因此冷备份的 data/ 已覆盖 brain.db；`brain_backup.py` 是与冷备份并行的「单库热备份」通道。
5. 缺省/耗尽：无自动保留/清理，`.pre-restore-*` 与 `-export` 会持续堆积；没有 CLI 层的保留参数。

### 1.11 数据修复链（data_repair.py）

1. 范围：只允许项目 `data/` 下已存在的 `.jsonl`，不是符号链接（24-41）；不满足 →「只能处理项目 data 文件夹里的文件。」「请指定 data 文件夹里已存在的 .jsonl 文件。」。
2. diagnose（48-71）只读；`find_problems` 识别 `invalid-json/torn-tail/bad-utf8/not-an-object/unknown-type/missing-id/invalid-amount/invalid-period/no-newline`（`ledger_recovery.py:19-29`）。
3. repair（94-152）：先查 `.veil-restore-incomplete.json`（98-99），再拿冷锁（100）；只缺换行时只补 `\n`（107-113）；否则要求输入 `REPAIR`（116），保存 `<原名>.corrupt-<stamp>` 并校验 sha256（118-125），坏行进 `<stem>.quarantine-<stamp>.jsonl`（127-128）。
4. 费用账本特殊：先写证据 + 冻结标记，再替换文件（130-142）；修完仍有问题 → 报错并保留原件（144-146）。
5. 缺省/非法：确认串不对 → `BackupError('未确认，文件没有改动。')`；恢复标记存在 → 拒绝（98-99）。

### 1.12 失败记录链（tool_failures）

1. 工具异常：`veil_agent.py:4939-4956` 捕获所有普通异常 → `tool_failures.internal_failure`（159-175）只返回 `err-<8 hex>`、异常类名与固定文案，异常原文不发给模型。
2. 落盘：`_record_internal_failure`（`veil_agent.py:4960-4974`）写 `data/<角色>/tool-errors.jsonl`，含 `type`、`arguments`（脱敏后）、`traceback`（截 8000 字符）；写失败静默（4974）。
3. 面向模型/账本：`finalize`（122-156）拼「工具原话 + 收到参数 + 改法/没有改法」；`redact`（86-114）先结构化去密钥、再文本正则，最后过 `safe_tool_activity_detail(limit=1000)`（105-113）。
4. 缺省/耗尽：`tool-errors.jsonl` 无轮换、无保留期，只在异常时追加；`_short` 上限 160（117-119）。

### 1.13 自维护任务链（maintenance.py）

1. 启用条件：`executor.get('enabled')` 且 `tool_mode` 且角色 id 为 `peter`（`veil_agent.py:2274-2284`）；`executor` 来自 `character.toml [executor]`，可被 `data/<角色>/workbench.json` 的 `enabled/project_roots/container_image/web_hosts` 覆盖（`veil_agent.py:798,1131-1134`）。
2. 开始：`begin`（905-944）要求 title+goal（908-911，截 `MAX_TITLE=160`/`MAX_GOAL=4000`），校验仓库在 main、无未提交源码改动、origin 指向配置仓库（638-647），同步 main（649-656），工单号取 `docs/tickets` 最大值+1（658-666），建 worktree 前先要求 Lan 授权（929-932）。
3. 时限：`deadline = now + hours`（282-286），`hours` 默认 2（`veil_agent.py:1151,2282`）；过期后除 begin 外的动作一律「maintenance task exceeded its time limit…」拒绝（1066-1077、1262-1263、1426-1427、1451-1452）。
4. 交付/门禁：`deliver`（1066-1132）允许本地容器测试红（1072-1075）但以 PR CI 为准；`checks`（1134-1170）读分支保护（785-800，读不到就拒绝猜 791/794）、必需检查、review（824-841）；`accept`（1172-1254）要求 worktree head == PR head，并要求 Lan 亲自 y/N 确认；`merge`（1256-1345）逐项校验 OPEN/main/MERGEABLE/head 一致/check passed/无 blocking review/验收 head/兰的合并授权，拒绝后记住 `merge_denied.head_sha` 不再重复问（1290-1291）。
5. 隔离：worktree 不得在仓库或 data 内（668-674）；提交前扫 `FORBIDDEN_PATH`（40-42、713-724）；`_clean_env` 只保留白名单（44-48、154-155），`_host_env` 丢弃所有 GIT_* 重定向（161-169）。
6. 验收实例：`AcceptanceInstance.start`（485-545）用 `--acceptance` + 独立 config/data；等端点 90 s、等 ready 90 s、轮询 0.5 s、HTTP 3 s（495-528）；失败即停并记录 blocker（1201-1207）。

### 1.14 更新链（两套更新器）

1. `update.bat`（1-155）：把自身复制到 `.local-tmp` 再运行（9-13），因为 `git pull` 会改写正在执行的 bat；检测 VEIL.exe 与 `data/session.lock`（26-32）；只允许 main（36-38）；有新提交才备份（44-53），`pre-update` 失败即停（51-52、133-138）；`git pull --ff-only`（58）；`pip install -r requirements.lock.txt`、playwright chromium、`setup_fetch.py`、Nowhere、快捷方式（65-75）；按 `git rev-list -1 HEAD -- desktop` 与 `desktop/dist/built-from.txt` 决定是否重打包（98-113）。
2. `update-main.cmd`（1-161）：`git fetch origin main`（28），未提交改动先 `git stash --include-untracked`（45-53），非 main 则切 main（55-67）；`git merge --ff-only` 成功即进入打包（70-71）；本地 main 有独有提交时建 `backup/main-before-update-<sha>` 再 `git reset --hard origin/main`（73-110）；打包前要求 VEIL.exe 未运行与 electron-packager 存在（113-123）；不做数据备份。
3. 失败行为：`update.bat` 报错后只停下并提示（139-152），代码可能已更新、依赖可能未装齐；`update-main.cmd` 失败会 `:return_to_branch` 切回原分支，stash 与 backup 分支保留（148-161）。
4. 依赖锁：`requirements.lock.txt`、`requirements.nowhere.lock.txt`、`requirements.fetch.lock.txt` 三份锁定文件被 setup/update/CI 引用（`update.bat:65`、`setup.bat:31`、`.github/workflows/tests.yml:17`）。

### 1.15 断线 / 重连链

1. 轮询：`chat.js:1811` 每 600 ms 拉 `/api/events?after=<id>`；连续失败 `pollFailures>=3` 才显示「服务：连接中断」并禁用活动开关与退出按钮（`chat.js:1808`）。
2. 重连：`chat.js:2121-2124` → `window.veilDesktop.reconnect()` → `main.cjs:221-230`（销毁 chat 窗口、清 endpoint、重新 toggleChat）+ `main.cjs:743` IPC。
3. 单次 HTTP 超时：Electron 转发 `AbortSignal.timeout(max(1000, min(120000, timeoutMs||30000)))`（`main.cjs:550`）；endpoint 直连 5 s（`main.cjs:274`）。

---

## 2 配置清单表

> 说明：「页面入口」一栏严格按现状填写。没有就是「仅改文件」或「代码写死，无入口」。备份/恢复/更新类没有任何 HTTP 路由或页面（`web_agent.py:2417-2473,3430-3456` 全部路由已核对，无 backup/update/maintenance 路由）。

### 2.1 诊断与快照（diagnostics.py / web_agent.py）

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 诊断 | 快照文件名/路径 | diagnostics.py:40,212-213 | `data/<角色>/diagnostics-snapshot.json`，JSON（indent=1） | 无文件则不写 | 固定文件名 | 仅改文件（删除即重建）；无页面 | 无覆盖 | 每次 overview 且超过 300 s | 覆盖式持久 | 只含计数/标签，不含聊天与记忆正文（1-11） | R |
| 诊断 | SNAPSHOT_MAX_AGE | diagnostics.py:41,1317-1324 | 代码常量/秒 | 300 | 正整数秒 | 代码写死，无入口 | 无 | 每次 overview 请求 | 无 | 快照最多滞后 5 分钟，CLI 报告可能读到旧模块/配置 | R |
| 诊断 | ISSUE_LIMIT | diagnostics.py:42,662,798 | 代码常量/条 | 200 | 1..200（实际返回 ≤200） | 代码写死，无入口 | 被 API limit 200 与页面 30 双重收窄 | 每次请求 | 无 | 超出静默截断 | R |
| 诊断 | TAIL_ROWS | diagnostics.py:43,87,689 | 代码常量/行 | 4000 | 行 | 代码写死，无入口 | tools.jsonl 读取用此上限 | 每次 overview/issues | 无 | 更早的失败查不到 | R |
| 诊断 | hours（时间窗） | web_agent.py:2949-2952；diagnostics.py:665,1106；diagnostics-page.js:379 | URL query / 报告参数 | 72 | 1..720 小时；非数字回落 72 | 页面无选择器；报告固定 `hours=72`；仅 URL 手改 | API 参数优先 | 请求时 | 无 | 页面看不到 72 小时以外的异常 | R |
| 诊断 | issues limit（API） | web_agent.py:2977-2979 | URL query | 120 | 1..200 条；非数字 120 | 页面不传，恒为 120 | 覆盖 ISSUE_LIMIT | 请求时 | 无 | 与页面 30 条、报告 12 条不一致 | R |
| 诊断 | overview 明细上限 | diagnostics.py:1112,1115 | 代码常量 | issues 60 条 / attention 10 条 | 条 | 代码写死，无入口 | 在 200 条之内再截 | 每次 overview | 无 | 「总数」与「可见数」不一致 | R |
| 诊断 | 账本读取行数 | diagnostics.py:469-474 | 代码常量 | tools 1200／providers 400／contexts 400／autonomy 100／memory_diag 200／experience 80 | 行 | 代码写死，无入口 | 无 | 每次 overview | 无 | 老故障不可见 | R |
| 诊断 | 自主活动逾期判定 | diagnostics.py:536 | 代码常量 | 10 分钟 | 分钟 | 代码写死，无入口 | 无 | 每次 overview | 无 | 误报/漏报窗口 | R |
| 诊断 | 数据体积扫描/缓存 | diagnostics.py:1026-1055 | 代码常量 | 4000 文件、300 秒缓存 | 文件数/秒 | 代码写死，无入口 | 无 | overview | 仅内存缓存 | 页面标注「至少；只统计到扫描上限」 | R |
| 诊断 | trace 快照条数 | diagnostics.py:955-970 | 代码常量 | 8 | 条 | 代码写死，无入口 | 无 | trace 请求 | 无 | 只看最近 8 个请求 | R |
| 诊断 | 报告段落上限 | diagnostics.py:1272,1284,1292 | 代码常量 | 后台失败 12／未分类拒绝 3／其余 20 | 条 | 代码写死，无入口 | 无 | 生成报告时 | 无 | 报告非全量 | R |
| 诊断 | 工具预算接近阈值 | diagnostics.py:394 | 代码常量 | 0.9（90%） | 0..1 | 代码写死，无入口 | 与 `tool_groups.BUDGET` 配合 | overview | 无 | 只提示不修改 | R |
| 诊断 | memory.maintenance_url | diagnostics.py:1118-1136 | `characters/<id>/character.toml` 的 `[memory]` 字符串 | 空 → state=not_ready | 必须以 `http://127.0.0.1` 或 `http://localhost` 开头 | 仅改文件 character.toml；无页面/无文档 | 无 | 下次 `/api/diagnostics/links` | 随角色配置持久 | 只允许本机 URL；未配置时提示用 `veil_brain.py inspect` | A（隐藏键） |
| 诊断 | 诊断 CLI 参数 | diagnostics.py:1327-1342 | 命令行参数 | `--data-root data`、`--role peter`、`--hours 72`、`--issue ''`、`--json false` | 路径/角色/整数小时/ID/开关 | 命令行 | 无 | 每次运行 | 无 | 只读；服务停止后仍可跑 | R |
| 诊断 | 密钥/凭据脱敏正则 | diagnostics.py:28-34,148-152 | 代码常量 | sk-/ghp_/github_pat_/Bearer + key=value + URL 凭据 | 正则 | 代码写死，无入口 | 对所有 detail/report 输出 | 输出时 | 无 | 非常规密钥可能漏网 | P |
| 诊断 | 缺钥匙检测 | diagnostics.py:411-422 | 读 `os.environ` | 无 | 环境变量名 | 代码写死，无入口 | 与实际 `.env` 读取口径冲突（见 §3 C6） | overview | 无 | 误报「钥匙没有配好」 | X |
| 诊断 | DIAGNOSTIC_HOURS | web_agent.py:2935 | 代码常量 | 72 | 小时 | 代码写死，无入口 | 全仓无引用（`_hours` 自己写 72） | 不生效 | 无 | 死常量 | X |
| 诊断 | 诊断接口鉴权 | web_agent.py:1981-1998 | 请求头 | 无 | Host=127.0.0.1:{port} 或 localhost:{port}；Origin 可空但必须匹配；X-Token 恒等 | 不可配置 | 所有 `/api/*` | 每请求 | 无 | 403 并丢弃 body；token 在 URL query 中（见 §3 C15） | P |

### 2.2 运行安全摘要（runtime_monitor.py）

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 运行摘要 | runtime-status.json | runtime_monitor.py:18,27-37 | `data/<角色>/runtime-status.json`，单行 JSON | `{'state':'healthy','classification':'healthy','code':'ready','action':'No action is needed.'}` | state∈{healthy,recovered,attention,paused}；classification∈{healthy,safe_recovery,safe_to_retry,unknown}；6 键全为 str | 程序自动写；无页面入口 | 每次 `_save` 覆盖 | 事件发生时即时 | 持久（仅一行） | 只含诊断标签，不接受原始 payload（77-80） | R |
| 运行摘要 | 不确定写动作集 | runtime_monitor.py:13 | 代码常量 | `{'hold','trace'}` | 集合 | 代码写死，无入口 | 决定 unknown/safe_to_retry | 事件时 | 无 | 漏标会把不确定写当安全 | P |
| 运行摘要 | 自动暂停自主活动 | runtime_monitor.py:63-75,105-116 | `data/<角色>/resident-state.json` | 暂停 | 布尔 | 只能看到活动开关被关（chat.js:1756） | 与用户开关共用同一文件 | 事件时即时 | 持久 | 用户无法拒绝自动暂停；pause 失败会标 `memory_pause_failed` | P |
| 运行摘要 | 组件状态口径 | runtime_monitor.py:145-184 | 内存计算 | memory/file_tools/autonomy/long_task | 枚举 | 不可配置 | 无 | 每次 `/api/events` | 无 | 只反映当前 | R |
| 运行摘要 | 无 monitor 时默认 | web_agent.py:217-218 | 内存 | `healthy / healthy / '运行正常。'` | 固定 | 代码写死，无入口 | `serve_chat` 有 monitor 时覆盖（3636-3637） | 启动时 | 无 | CLI/验收模式下永远显示正常 | P |

### 2.3 审批、停止与退出

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 审批 | APPROVAL_WAIT_SECONDS | web_agent.py:111,1799-1817 | 代码常量 | 600 秒 | 秒 | 代码写死，无入口 | 仅对话内审批卡 | 每次审批 | 无（内存） | 超时=不执行；停止=取消 | P |
| 审批 | TTL_HOURS | pending_approvals.py:13,22-25,58-68,83 | `data/<角色>/pending-approvals.json` 的 `expires_at` | 24 小时 | 小时（构造参数） | 代码写死（`web_agent` 未传参） | 独立于 600 秒 | 建项时写死 | 持久 | 过期=expired，不能再批 | S |
| 审批 | MAX_PROMPT | pending_approvals.py:14,71 | 代码常量 | 600 字符 | 字符 | 代码写死，无入口 | 无 | 建项时 | 持久 | 截断 | R |
| 审批 | 保留条数 | pending_approvals.py:86,112,128-131 | 代码常量 | 200 条 / 展示已决 20 / 上下文 5+5 | 条 | 代码写死，无入口 | 无 | 写入/读取时 | 持久 | 超出丢弃且无提示 | R |
| 审批 | KEEP_DAYS | pending_approvals.py:12 | 代码常量 | 7 天 | 天 | 代码写死，无入口 | 全仓无引用 | 不生效 | 无 | 死键 | X |
| 审批 | 决策接口 | web_agent.py:2456,3447-3451；pending_approvals.py:90-104 | HTTP JSON | 只接受 `approved` / `denied` | 枚举 | 活动页卡片（chat.js:1681-1714） | 只能处理当前 pending | 请求时 | 持久 | 已处理/过期 409「已经处理过或过期了。」 | S |
| 停止 | /api/stop | web_agent.py:3413,2763-2767,1837-1849 | HTTP POST | 无 | 无参数 | 聊天页停止按钮 | 只结束当前回合 | 即时 | 无 | 对当前审批投 `cancel` | U |
| 退出 | /api/exit | web_agent.py:3229-3236,1443-1456,3454 | HTTP POST | 无 | 无参数 | 页面 `exit-service`（chat.js:2097）+ 桌面菜单（main.cjs:804-822） | 无 | 即时（202/200） | 无 | 页面有 confirm；桌面菜单无二次确认；失败只关壳 | S |
| 退出 | 退出后清理 | web_agent.py:3782-3816 | 内存/文件 | sleep 1.5 秒 | 秒 | 代码写死，无入口 | 无 | 退出时 | 删除 `data/web_service.json` | 无 | P |
| 活动 | ActivityState（resident-state.json） | veil_agent.py:1177-1217；web_agent.py:3728 | `data/<角色>/resident-state.json`，JSON `{enabled, enabled_at}` | 无文件=false | 布尔 | 页面 `activity-toggle`（chat.js:1756，POST /api/activity） | runtime monitor 自动暂停也写此文件 | 即时 | 持久 | 写失败回滚（veil_agent.py:2708-2721） | U |
| 事件 | 实时回放上限 | web_agent.py:59-60,1244-1252 | 内存 | 5000 条（+500 slack 触发裁剪） | 条 | 代码写死，无入口 | 裁剪时保留等待中事件 | 运行中 | 不持久 | 页面重载可回放 | R |

### 2.4 冷备份与恢复（backup_restore.py）

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 备份 | VEIL_BACKUP_DIR | backup_restore.py:31,874-905；.env.example:14-15；update.bat:51 | `.env` 的 `KEY=value` 文本 | 空 → 更新前备份直接失败并停更 | 项目目录之外的绝对路径 | 仅改文件 `.env`；或命令行 `--destination`；无页面/无接口 | 进程环境 > `.env`（876-880） | 下次 update.bat / 下次命令 | 随 `.env` 持久（`.gitignore:2`） | 备份明文，含 .env 内 API Key（407-411） | U |
| 备份 | PAYLOAD_ROOTS | backup_restore.py:23,163-176 | 代码常量 | `("data","characters","persona",".env")` | 固定四项，缺一即 `BackupError`（168） | 代码写死，无入口 | 无 | 每次备份 | 备份目录 | brain.db/models.json 都在 data 内被覆盖；.env 明文 | P |
| 备份 | 桌面端数据 | backup_restore.py:27-30,96-109 | `%APPDATA%\VEIL Desktop`（90-93） | `desktop.json` + `appearance-background.{png,jpg,jpeg,webp}` | 仅这 5 个名字 | 代码写死，无入口 | 无 | 每次备份 | 备份目录 | 不含 localStorage 外观/CSS、attachments、session-data（26、备份 docs:7） | P |
| 备份 | 排除项 | backup_restore.py:146-160,396-400 | 代码常量 | `data/session.lock`、`data/web_service.json`、`data*/*.tmp`、`memory/_agent` 运行残留/tmp | 规则 | 代码写死，无入口 | 无 | 每次备份 | manifest 记录 | 运行残留不进备份 | R |
| 备份 | 冷锁 | backup_restore.py:213-266,348 | `data/session.lock` 文件锁 | 备份时创建 | 1 字节 msvcrt 锁 | 代码写死，无入口 | 服务在跑即失败 | 备份/恢复期间 | 锁文件保留 | 报「VEIL 仍在运行或正在写入，请先完整关闭服务后重试。」（239） | P |
| 备份 | 磁盘余量 | backup_restore.py:316-329 | 代码常量 | `needed + max(64MB, needed/10)` | 字节 | 代码写死，无入口 | 无 | 备份前 | 无 | 不足直接拒绝，原文含需要/现有 MB | P |
| 备份 | 格式/布局版本 | backup_restore.py:21-22,462-465 | manifest.json 字段 | 1 / 1 | 仅 1 | 代码写死，无入口 | 不匹配即拒绝恢复 | verify/restore | manifest 内 | 「此备份格式或数据布局版本不受当前程序支持」 | P |
| 备份 | 必需根项目 | backup_restore.py:37-39,440-449 | 备份目录 | manifest.json/manifest.sha256/README.txt/payload | 根目录只允许这四项 | 代码写死，无入口 | 无 | verify | 备份目录 | 多余文件=拒绝 | P |
| 备份 | 恢复确认串 | backup_restore.py:736-741 | 交互输入 | 无 | 必须精确 `RESTORE` | 命令行 | 仅 `--replace` 且目标已有组件时 | 恢复时 | 无 | 未确认不改动 | S |
| 备份 | 安全副本命名 | backup_restore.py:742-743,852-859 | 同级目录 | `<target>.before-restore-<UTC>-<8hex>` | 目录名 | 代码写死，无入口 | 多次恢复会产生多份 | 恢复时 | 永久保留（程序不删） | 提示最早一份才是原始数据 | R |
| 备份 | 未完成恢复标记 | backup_restore.py:40,62-87,758,821,850；veil_agent.py:435-437 | `<项目根>/.veil-restore-incomplete.json` | 无 | 存在即拦截 | 代码写死，无入口 | 优先于一切启动 | 恢复开始/结束 | 持久直到完成 | 拒绝启动半恢复目录（veil_agent.py:436-437） | P |
| 备份 | 目标路径校验 | backup_restore.py:536-557,677-685,723-724,773-774 | 代码 | 无 | 非符号链接、非互相包含、大小写唯一 | 代码写死，无入口 | 无 | 恢复时 | 无 | 失败回滚（781-802） | P |
| 备份 | 费用账本对账 | backup_restore.py:572-624 | `data/costs.jsonl` + `costs-unverified*` | 自动 | `(id,type)` 合并 reserve/dispatch/settle | 代码写死，无入口 | 仅 replace 且有安全副本 | 恢复时 | 写回成本账本 | 无法确认 → 冻结全部付费请求 | P |
| 备份 | 桌面设置恢复条件 | backup_restore.py:627-659,860-861 | `%APPDATA%\VEIL Desktop` 或 `restored-desktop-data-<stamp>` | 条件写回 | 仅当 live 无这些文件且 VEIL.exe 未运行 | 代码写死，无入口 | 不覆盖正在使用的配置 | 恢复后 | 写入或放到旁边 | 用户可能以为「没恢复」 | P |
| 备份 | 保留/清理策略 | backup_restore.py:923-944（仅统计） | 无 | 不存在自动删除 | — | 无 | 无 | — | 备份/安全副本/临时目录无限增长 | `usage` 只报数不删 | X（缺失） |
| 备份 | CLI 子命令 | backup_restore.py:947-961 | 命令行 | create/verify/restore/pre-update/usage | 子命令 | 命令行 | 无 | 每次运行 | 无 | 只读/冷操作 | R |
| 备份 | --skip-backup | update.bat:48,54-56,137 | 命令行参数 | 不跳过 | 开关 | 命令行 | 覆盖自动备份 | 本次更新 | 无 | 屏幕写明「没有备份」 | S |

### 2.5 brain.db 备份 / 导出（brain_backup.py / brain_export.py）

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Brain | brain.db 路径 | brain_authority.py:23；brain_authority_init.py:88 | `data/<角色>/brain.db` | 新建角色时创建 | SQLite | 无（由角色/数据目录决定） | 冷备份的 data/ 已覆盖 | 启动时 | 持久 | 缺失/损坏时拒绝建空库（brain_authority_init.py:38-51,97-98） | P |
| Brain | 在线备份校验 | brain_backup.py:37-61,96-110 | 内存/结果 JSON | quick_check+digest+10 表计数 | 必须 ok/匹配 | 命令行（`python brain_backup.py`） | 无 | 备份时 | 输出文件 | 不合格直接抛错 | P |
| Brain | 停服/WAL 检查 | brain_backup.py:64-93 | `brain-write.lock`、`brain.db-wal`、`-shm` | 存在即拒绝 | — | 代码写死，无入口 | 优先于恢复 | 恢复前 | 无 | 「请完全关闭 VEIL 后重试」 | P |
| Brain | 恢复安全副本/导出 | brain_backup.py:133-140 | 同级 `brain.db.pre-restore-<stamp>`、`…-export/` | 自动生成 | 时间戳 | 代码写死，无入口 | 不覆盖旧库 | 恢复时 | 永久保留 | 无自动清理 | R |
| Brain | 合并备份后新增 | brain_backup.py:148-155,198-272 | 临时库 | 自动 | 只补主键不存在的新行 | 代码写死，无入口 | 不覆盖旧 ID | 恢复时 | 写回 live | 失败不改 live | P |
| Brain | 事件导出开关 | brain_export.py:31,93 | 函数参数 | `include_events=True` | 布尔 | 代码/CLI（`--db/--out`） | 无 | 导出时 | 输出目录 | 只写副本，从不改 brain.db（1-5） | R |

### 2.6 数据修复与费用冻结（data_repair.py / ledger_recovery.py）

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 修复 | 可处理文件范围 | data_repair.py:24-41 | 代码 | 仅 `data/*.jsonl` | `.jsonl` 且存在、非符号链接 | 命令行 | 无 | 每次运行 | 无 | 越界即拒绝 | P |
| 修复 | 确认串 REPAIR | data_repair.py:116 | 交互输入 | 无 | 精确 `REPAIR` | 命令行 | 无 | 修复时 | 无 | 未确认不改文件 | S |
| 修复 | 确认串 VERIFIED | data_repair.py:184 | 交互输入 | 无 | 精确 `VERIFIED` | 命令行 | 解除冻结唯一途径 | 核对账本后 | 无 | 不对就继续冻结 | S |
| 修复 | 原件/隔离文件命名 | data_repair.py:119-128 | 同目录 | `<原名>.corrupt-<stamp>`、`<stem>.quarantine-<stamp>.jsonl` | 时间戳 | 代码写死，无入口 | 无 | 修复时 | 永久保留 | 先校验 sha256 再改 | R |
| 修复 | 未完成恢复时拒绝 | data_repair.py:98-99 | 项目根标记 | 拒绝 | — | 代码写死，无入口 | 优先于修复 | 每次运行 | 无 | 「先按提示重新运行恢复」 | P |
| 冻结 | 标记文件名 | ledger_recovery.py:15-16,212-217 | `data/costs-unverified.json` / `.jsonl` | 无=正常 | 文件存在=冻结 | 仅 `data_repair.py ledger-verify` 解除 | 无 | 下次付费请求 | 持久 | 不可读标记不覆盖、继续冻结（282-284） | S |
| 冻结 | 费用事件类型 | ledger_recovery.py:17,92-102 | 代码常量 | `reserve/dispatch/settle` | 枚举 | 代码写死，无入口 | 无 | 校验时 | 无 | 非法行进证据文件 | P |
| 冻结 | 冻结后的请求行为 | veil_agent.py:1365-1369,1692-1693,1855-1856 | 代码 | 全部付费请求拒绝 | — | 无 | 预算预留前 | 每次预留 | 无 | 文案指明 ledger-status/ledger-verify | P |
| 冻结 | 冻结历史归档 | ledger_recovery.py:348-353 | `data/costs-unverified.verified-<stamp>.json` | 自动 | 时间戳 | 代码写死，无入口 | 无 | 解除时 | 永久保留 | 证据文件也保留 | R |

### 2.7 失败记录（tool_failures.py）

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 失败记录 | tool-errors.jsonl | veil_agent.py:4960-4974 | `data/<角色>/tool-errors.jsonl` | 仅异常时写 | JSONL，`traceback` 截 8000 字符 | 仅改文件；无页面 | 无 | 工具内部异常时 | 持久，无轮换 | 含已脱敏参数与本地路径 | P |
| 失败记录 | 密钥键名表 | tool_failures.py:45-57 | 代码常量 | token/secret/passw/api_key/... | 子串匹配 | 代码写死，无入口 | 结构化优先于文本 | 每次 redact | 无 | 非标准键名可能漏 | P |
| 失败记录 | 截断上限 | tool_failures.py:117-119,111 | 代码常量 | 160 / 1000 字符 | 字符 | 代码写死，无入口 | 无 | 生成文案时 | 无 | 截断 | R |
| 失败记录 | 固定文案模板 | tool_failures.py:129-156,169-172 | 代码 | 中英双语固定句 | 文案 | 代码写死，无入口 | 无 | 失败时 | 写入账本 | 异常原文不外泄 | P |

### 2.8 自维护（maintenance.py + executor 配置）

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 自维护 | executor.enabled | veil_agent.py:1136-1138；`data/<角色>/workbench.json` | 角色 TOML 布尔 / 本机 JSON 布尔 | false | 布尔 | 仅改文件；无页面 | workbench.json 覆盖 character.toml（1132-1134） | 重启加载角色 | 持久 | 未开启时 `maintenance` 工具不注入（2274-2284） | S |
| 自维护 | github_repo | maintenance.py:30；veil_agent.py:1149,2281 | `[executor].github_repo` 字符串 | `yecays-ai/veil-agent` | owner/repo | 仅改文件 | 覆盖默认值 | 重启 | 角色配置持久 | 指向错误仓库会在别处开 PR | S |
| 自维护 | maintenance_hours | veil_agent.py:1151,2282；maintenance.py:282-286,966-968 | `[executor].maintenance_hours` | 2 小时 | 正整数（`positive_int`） | 仅改文件；无页面 | 无 | 新任务创建时写入 deadline | 任务 JSON | 过期后交付/合并/验收全拒 | A |
| 自维护 | maintenance_repo | veil_agent.py:1147 | `[executor].maintenance_repo` | 项目根 `ROOT` | 绝对路径 | 仅改文件 | 无 | 重启 | 角色配置 | 必须是 main、无未提交源码（638-647） | A |
| 自维护 | worktree_root | veil_agent.py:1148；maintenance.py:668-674 | `[executor].worktree_root` | `<项目父目录>/worktrees` | 绝对路径 | 仅改文件 | 无 | 重启 | 角色配置 | 不得在仓库/data 内 | A |
| 自维护 | github_token_file | veil_agent.py:1150,2283；github_token_setup.py:9-23；maintenance.py:172-187 | 默认 `<项目上两级>/secrets/github-token.txt` | 同左 | 文件路径；内容匹配 `github_pat_` 或 `ghp_` 等 | 仅改文件；`github-token.cmd` 是输入器 | 覆盖默认 | 重启 | 文件持久 | token 不进提示词/日志/容器 | S |
| 自维护 | MAX_TITLE/GOAL/NOTE/HISTORY | maintenance.py:36-39,911,1241,311 | 代码常量 | 160 / 4000 / 1000 / 60 | 字符/条 | 代码写死，无入口 | 无 | 写入时 | 任务 JSON | 截断 | R |
| 自维护 | Runner 默认超时 | maintenance.py:117,130-151 | 代码常量 | 180 秒 | 秒 | 代码写死，无入口 | 各调用可覆盖 | 每次外部命令 | 无 | 超时杀进程树（144-147） | P |
| 自维护 | 单项命令超时 | maintenance.py:631,651,747,948 | 代码常量 | gh 120 / fetch 180 / push 300 / auth 30 | 秒 | 代码写死，无入口 | 调用点覆盖默认 | 命令执行 | 无 | 网络慢会失败 | R |
| 自维护 | 验收实例等待 | maintenance.py:495-528 | 代码常量 | 端点 90 s、ready 90 s、轮询 0.5 s、HTTP 3 s | 秒 | 代码写死，无入口 | 无 | 启动验收实例 | 无 | 失败即停并记 blocker | R |
| 自维护 | 必需检查来源 | maintenance.py:785-800,843-863 | GitHub 分支保护 API | 无 | 检查名列表 | 不可配置（跟仓库设置） | 404 视为无必需检查 | 每次 checks/merge | 任务 JSON | 读不到时拒绝猜（791/794） | P |
| 自维护 | CI 失败“两次” | maintenance.py:1150 | 文案 | 2 次 | — | 代码写死，无入口 | 无计数/无强制 | checks 输出 | 无 | 只是 next_step 提示 | X |
| 自维护 | 合并门 | maintenance.py:1271-1295 | 任务 JSON | 全绿+验收+兰授权 | 枚举/布尔 | 模型调用 `maintenance action=merge`；无页面 | 拒绝后记住 head（1290-1291） | merge 时 | 任务 JSON | 自主活动一律拒绝（1257-1258） | S |
| 自维护 | FORBIDDEN_PATH | maintenance.py:40-42,713-737 | 代码常量 | `.env/persona/data/logs/memory/*.log/*.pem/*.key/...` | 正则 | 代码写死，无入口 | 先扫未跟踪再扫已跟踪 | 每次 commit | 无 | 命中即拒绝提交 | P |
| 自维护 | KEEP_ENV / _host_env | maintenance.py:44-48,154-169 | 代码常量 | 环境白名单；GIT_* 全丢弃 | 键名集合 | 代码写死，无入口 | 验收实例/主机 git | 启动子进程 | 无 | 隔离凭据与 git 重定向 | P |
| 自维护 | 任务状态机 | maintenance.py:31-35,96,102-105 | 代码常量 | 15 个状态 | 枚举 | 代码写死，无入口 | 非法即整任务判无效 | 每次读写 | 任务 JSON | 防手改状态 | P |
| 自维护 | 授权提问只认 y | maintenance.py:1504-1511 | 代码 | 仅 `y`（casefold） | 字符串 | 模型调用 | 每道门独立 | 每次 | 任务 JSON | 其余一律拒绝 | P |
| 自维护 | 自主活动不得自维护 | maintenance.py:905-907,1066-1068,1174-1175,1257-1258,1346-1348 | 代码 | 一律拒绝 | — | 不可配置 | 无 | 每次调用 | 无 | 防自主体改代码/合并 | P |
| 自维护 | maintenance 工具 | maintenance.py:1514-1540；veil_agent.py:2470-2473 | 工具 schema，9 个 action | begin/status/resume/deliver/checks/accept/merge/stop/cleanup | 枚举 | 只能由模型调用；无页面、无按钮 | 活动任务被 pin（veil_agent.py:2492） | 每轮请求 | 无 | 需 Lan 逐门 y/N | S |

### 2.9 更新与 CI

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 更新 | update.bat 流程 | update.bat:1-155 | 批处理 | 只 main、运行中拒绝、有新提交才备份 | — | 双击；命令行 `--skip-backup` | 备份依赖 `.env` | 双击时 | 不改 data | 失败停更并说明（133-152） | A |
| 更新 | update-main.cmd 流程 | update-main.cmd:1-161 | 批处理 | stash 本地改动、ff-only、失败建 backup 分支 | — | 双击 | 与 update.bat 独立 | 双击时 | stash/backup 分支 | 不做数据备份 | A |
| 更新 | Python 版本 | setup.bat:20-31,50 | 批处理断言 | 3.11 ≤ x < 3.13 | 版本区间 | 装环境时 | 无 | 安装/更新 | 无 | 不满足直接失败 | A |
| 更新 | 依赖锁 | requirements.lock.txt / requirements.nowhere.lock.txt / requirements.fetch.lock.txt；update.bat:65；setup.bat:31；tests.yml:17 | 文本锁文件 | 锁定版本 | pip 需求格式 | 仅改文件（需 lan 同意） | CI 与本地一致 | 安装/更新 | 仓库 | 变更需走 PR | P |
| 更新 | 桌面版本 | desktop/package.json:4 | JSON | 0.1.0 | semver | 仅改文件 | 无 | 打包 | 仓库 | — | R |
| CI | Tests 矩阵 | .github/workflows/tests.yml:6-32 | YAML | windows+ubuntu × 3.11/3.12 | 平台/版本 | 仅改文件 | PR/push 触发 | 每次推送 | 仓库 | 额外跑 Lab/browser 真实渲染（19-30） | P |
| CI | 桌面流水线 | .github/workflows/desktop.yml:3-32 | YAML | 仅 `desktop/**` 变更触发 | 路径过滤 | 仅改文件 | 无 | PR/push | 仓库 | artifact 保留 14 天（32） | R |
| CI | 依赖安全 | .github/workflows/dependency-security.yml:3-6,44-52 | YAML | 每周一 07:17 UTC + PR + 手动 | cron | 仅改文件 | 无 | 定时 | 仓库 | pip-audit 固定 commit；任一失败即 fail（31-36） | P |

### 2.10 日志与记录保留

| 模块 | 配置项（键/名称） | 源码位置 | 存储位置/格式 | 代码默认值 | 合法值/范围/单位 | 当前修改方式/页面入口 | 优先级/覆盖关系 | 生效时机 | 持久化 | 风险/权限保护 | 分类 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 记录 | context-log.jsonl 保留 | background.py:18,304-318 | `data/<角色>/context-log.jsonl` | 500 行（超过 600 才裁剪） | 行 | 代码写死，无入口 | 无 | 每次追加 | 持久（自动裁旧） | 只留最近 | R |
| 记录 | 请求快照保留 | request_snapshots.py:20-24,222-236 | `data/<角色>/request-snapshots/` | 40 个请求；块 12000／总 300000／工具 120000 字符 | 请求数/字符 | 代码写死，无入口 | 按 mtime 删旧 | 每次模型调用 | 持久 | 自动删旧；页面只是快照 | R |
| 记录 | 页面写入错误日志 | web_agent.py:523-534 | `data/<角色>/peter-page-errors.jsonl` | 仅失败时写 | JSONL | 代码写死，无入口 | 无 | 保存 Peter 页失败时 | 持久，无轮换 | 只记异常类名/消息 | R |
| 记录 | 诊断报告不落盘 | diagnostics.py:1215-1302 | 内存文本 | — | — | 页面复制 | 无 | 生成时 | 不持久 | 不含聊天/记忆正文 | R |
| 记录 | diagnostic.log | 全仓检索 0 命中（grep -rn diagnostic.log） | — | — | — | — | — | — | — | 见 §7 | X |
| 记录 | logs/ 目录 | .gitignore:13-14 忽略 `logs/`、`*.log`；代码中无写入点 | — | — | — | — | — | — | — | 见 §7 | X |

---

## 3 重复 / 冲突 / 隐性覆盖

C1 两套更新器并存，安全策略不同。`update.bat` 在有新提交时强制先 `pre-update` 冷备份（`update.bat:44-53`），失败就停（133-138）；`update-main.cmd` 只 `git stash`，完全不备份数据（`update-main.cmd:35-53,106`）。诊断页的「更新代码」提示只写 `update-main.cmd`（`diagnostics.py:1134-1135`），而备份文档只描述 `update.bat`（`docs/backup-and-restore.md:20-27`）。用户按页面提示更新时没有更新前备份。

C2 备份没有任何保留/轮换策略。`backup_restore.py` 全文没有删除旧备份/安全副本的代码；`usage_report` 只统计不删除（923-944）。`brain_backup.py` 的 `.pre-restore-*` 与 `-export/` 同样只增不减（133-140）。冷备份每次新建 `VEIL-Agent-Backup-<时间戳>-<随机>`（346），长期会堆满备份盘。

C3 诊断上限在不同层各说各话。`ISSUE_LIMIT=200`（`diagnostics.py:42,798`）→ API `limit` 上限 200（`web_agent.py:2978-2979`）→ overview 只给 60 条、attention 10 条（1112、1115）→ 页面再 `.slice(0,30)`（`diagnostics-page.js:198`）→ 报告只写 12/3/20 条（1271-1293）。`counts.issues` 却是全量 200 以内计数（1113），于是「N 条记录」与可见条数永远对不上。

C4 页面把时间窗写死 72 小时。报告按钮 `scope=overview&hours=72`（`diagnostics-page.js:379`），而 API 支持 1..720 小时（`web_agent.py:2950-2952`），`diagnostics.hours` 也夹在同一范围（665、1106）。页面上无法查看 3 天前的异常，只能手改 URL 或跑 CLI。

C5 快照只在 overview 被打开时写，且 5 分钟节流。`web_agent.py:2963` 是唯一写入点；CLI（`diagnostics.py:1327-1342`）只读快照不写（1336）。服务停掉后 `report_text` 会标注「模块与配置来自服务运行时的最后一次快照：…」（1237-1238），但快照可能已过期很久，用户容易把旧状态当现状。

C6 钥匙检测口径与实际读取不一致。诊断用 `os.environ.get(env_name)`（`diagnostics.py:415`），而模型请求实际从 `.env` 读：`dotenv_values(ROOT/'.env')`（`veil_agent.py:5032`），设置页也用 `_env_values()`（`model_settings.py:162-164,584-595`）。钥匙只写在 `.env` 时，诊断会误报「模型接口的钥匙没有配好」并提示「在 .env 里补上后重启服务」（417-421），而它本来就在 `.env` 里；email 模块也用 `dotenv_values`（`email_channel.py:103-104`）。本仓库没有把 `.env` 注入 `os.environ` 的代码（全仓 grep 仅上述读取点）。

C7 `memory.maintenance_url` 是影子键。只有 `diagnostics.py:1123` 读它；`characters/peter/character.toml` 的 `[memory]` 没有这个键（`character.toml:36-115`），文档/页面也没有入口，所以「记忆维护（VEIL Brain）」永远显示 `not_ready`（1126-1129）。要让它出现只能手改角色文件。

C8 `data/<角色>/workbench.json` 的允许键与注释不一致。`WORKBENCH_LOCAL_KEYS` 含 `web_hosts`（`veil_agent.py:798`），但紧跟的 docstring 只写「Only enabled, project_roots and container_image may be set here」（814-816）。`web_hosts` 实际也会覆盖角色 TOML（1134）。

C9 审批有两个「过期」概念。对话内审批卡 600 秒超时（`web_agent.py:111,1808`），持久待批准 24 小时过期（`pending_approvals.py:13,83`）。页面卡片只显示后者 `expires_at`（`chat.js:1692`），用户会以为对话里的审批也有 24 小时。

C10 `KEEP_DAYS=7` 是死键。`pending_approvals.py:12` 定义后全仓无引用（grep 仅命中定义行），既不清理也不影响 TTL 24 小时。

C11 CI「两次」只是文案。`maintenance.py:1150` 写 "stop after two attempts."，但代码中没有失败计数、没有阻断第三次 `deliver`；`checks` 只把 stage 设为 `ci_failed`（1149-1150）。

C12 运行安全摘要没有历史。只保留最后一行（`runtime_monitor.py:9-10,46-61`），页面/报告只能看当前状态；`docs/RELIABILITY.md` 也自认「状态页只保留最近一次安全摘要，不扩展历史监控后台」。

C13 备份是明文，且冷备份同时包含 .env 与 brain.db。`PAYLOAD_ROOTS` 含 `.env`（`backup_restore.py:23`），`data/` 含 `brain.db`（`brain_authority.py:23`）；备份 README 自己写「may contain API keys in .env」「does not encrypt or upload it」（407-411）。而 `update-main.cmd` 连这份明文备份都不做（C1）。

C14 桌面外观不在备份内。冷备份只带 `desktop.json` 与背景图（`backup_restore.py:27-30`），但主题/透明度/自定义 CSS 存在 localStorage（`chat.js:2125-2138,2505-2507`），`attachments`、`session-data` 也被明确排除（26、`docs/backup-and-restore.md:7`）。恢复后窗口位置和背景回来了，外观设置没有。

C15 token 通过 URL query 传递。启动地址是 `http://127.0.0.1:{port}/?t={token}`（`web_agent.py:509` 附近、`maintenance.py:509` 同样拼法），Electron 从 URL 取 token 再放 `X-Token`（`main.cjs:545-548`）。本地单用户场景可接受，但 token 会进入浏览器历史/进程参数；`/api/*` 的 `_guard` 本身只认 `X-Token`（1981-1998），两者是两套凭据传递方式。

C16 诊断页「桌面连接」永远未知。`module_states` 固定追加 `desktop: unknown`（`diagnostics.py:627`），而桌面连接状态其实由 Electron 知道，页面无法读到——属于「只展示不生效」的占位项。

C17 `_hours` 的 `isdigit()` 让负数/小数静默回落 72。`web_agent.py:2951-2952` 对 `-1`、`72.5` 都当非数字，不报 400，用户无法察觉参数被忽略。

C18 `update.bat` 有一行永不执行的检查。65-69 行连续两次 `if errorlevel 1 goto failed`，第二次永远为假（前一次未跳转则 errorlevel 已为 0），属于死代码，不影响行为但说明改动时未清理。

C19 恢复后桌面设置「看起来没生效」。只有 live 目录不存在这些文件且 VEIL.exe 未运行才写回（`backup_restore.py:627-659`）；否则放到 `restored-desktop-data-*`，用户容易忽略提示（652-658）。

C20 `Store.__init__` 的恢复标记检查在项目根，而冷备份恢复目标是任意外部目录。恢复到 `D:\VEIL-recovery` 时标记写在 `D:\VEIL-recovery\.veil-restore-incomplete.json`（`backup_restore.py:48,758`），而 `Store` 检查的是 `self.root.parent`（`veil_agent.py:435`）。只有当 `--data-dir` 指向 `D:\VEIL-recovery\data` 时两者才对齐；用其他数据目录启动恢复副本时，标记可能拦不住。

---

## 4 页面与入口现状

| 能力 | 现有页面/接口（证据） | 能改什么 | 只能改文件 | 代码写死 |
|---|---|---|---|---|
| 诊断概览 | 诊断页 `/api/diagnostics/overview`（chat.html:155-179、diagnostics-page.js:357-374） | 只读：刷新、复制报告 | — | 所有上限/时间窗（§2.1） |
| 诊断异常时间线 | `/api/diagnostics/issues`（web_agent.py:2970-2988） | 只读；页面 30 条 | hours/module/limit 只能手改 URL | 200/120/60/30 上限 |
| 单轮追踪 | `/api/diagnostics/trace` + `openSnapshot`（diagnostics-page.js:321-346） | 只读 | — | 快照 8 条 |
| 诊断报告 | `/api/diagnostics/report`（web_agent.py:3019-3039） | 只读复制 | — | 12/3/20 段落、72 小时 |
| 维护入口链接 | `/api/diagnostics/links`（diagnostics.py:1118-1136） | 只读；记忆维护链接需 character.toml | `[memory].maintenance_url` | update 提示写死 `update-main.cmd` |
| 请求快照 | `/api/request-snapshots`、`/api/request-snapshot`（web_agent.py:2452-2454,2888-2931） | 只读 | — | 40 个请求保留 |
| 待批准 | `/api/pending-approvals`（GET/POST）+ 活动页卡片（chat.js:1669-1714） | 同意/拒绝 | — | 24 小时 TTL、200 条 |
| 对话内审批 | 聊天页审批卡（chat.js:1794-1801） | 同意/拒绝（仅当前一张） | — | 600 秒超时 |
| 停止回合 | `/api/stop`（聊天页停止按钮） | 无参数 | — | 只停当前回合 |
| 退出服务 | 页面 `exit-service`（chat.js:2097）、桌面菜单「退出 Agent」（main.cjs:804-822） | 触发退出 | — | 清理流程写死 |
| 自主活动开关 | `POST /api/activity` + `activity-toggle`（chat.js:1756） | 开/关 | — | 运行摘要会自动关（不可配置） |
| 运行安全摘要 | `/api/events` 的 `runtime_status`（web_agent.py:1908-1918） | 只读 | — | 只有最后一行 |
| 备份 | 无页面、无接口（路由表 web_agent.py:2417-2473,3430-3456 已核对） | — | `.env` 的 VEIL_BACKUP_DIR；命令行 `--destination` | 内容/排除/校验全写死 |
| 更新前备份 | 无 | — | `update.bat` 自动调用；`--skip-backup` | 只在有新提交时 |
| 恢复 | 无页面、无接口 | — | 命令行 `restore --backup --target [--replace]` | 确认串/回滚/标记 |
| brain.db 备份/恢复 | 无页面、无接口 | — | 命令行 `brain_backup.py` | 校验/合并/安全副本 |
| 数据修复 / 费用冻结解除 | 无页面、无接口 | — | 命令行 `data_repair.py diagnose/repair/ledger-status/ledger-verify` | 确认串、隔离命名 |
| 自维护任务 | 无页面、无接口；只有模型工具 `maintenance`（maintenance.py:1514-1540、veil_agent.py:2470-2473） | 模型按 action 调用，逐门 y/N | `[executor]` 配置 | 时限/门禁/白名单全写死 |
| 更新 | 无页面；双击 `update.bat` / `update-main.cmd` | — | 脚本本身 | 流程写死 |
| 日志级别/日志位置 | 无 | — | — | 无集中日志配置（见 §7） |

补充：诊断页只在主聊天窗口。导航按钮没有「拖出成独立窗口」标记（`chat.html:38` vs 30-37、39），Electron 的独立窗口白名单也不含 `diagnostics`（`main.cjs:531-539`）。

---

## 5 生效时机与持久化

| 配置/行为 | 生效时机 | 重启后 | 更新后 | 备份/恢复后 |
|---|---|---|---|---|
| diagnostics 常量（SNAPSHOT_MAX_AGE、ISSUE_LIMIT、TAIL_ROWS…） | 改代码后重启 Python 进程 | 保留（代码） | 被 `git pull` 覆盖，需重新改 | 不涉及 |
| 诊断页上限/72 小时 | 刷新页面；常量需重启 | 保留 | 随代码 | 不涉及 |
| 诊断快照文件 | 下次打开诊断页（≤300 s 节流）；服务停后只读旧快照 | 保留 | 保留（在 data/） | 随 data/ 恢复 |
| memory.maintenance_url | 下次 `/api/diagnostics/links` | 从 character.toml 读 | 若改的是角色文件且被备份覆盖则回退 | 随 characters/ 恢复 |
| APPROVAL_WAIT_SECONDS | 下次审批 | 保留（代码） | 随代码 | 不涉及 |
| pending-approvals 的 TTL | 新建条目时写入 expires_at；改代码不影响旧条目 | 保留 | 保留（在 data/） | 随 data/ 恢复（含已过期状态） |
| ActivityState（resident-state.json） | 即时（API/自动暂停） | 保留 | 保留（在 data/） | 随 data/ 恢复；恢复后自动暂停状态也回来 |
| runtime-status.json | 事件发生时即时 | 保留（单行） | 保留 | 随 data/ 恢复（可能带回旧的 attention 状态） |
| costs-unverified 标记 | 下次付费预留即拒 | 保留 | 保留（在 data/） | 恢复时 `carry_unverified` 强制带入（ledger_recovery.py:323-345）；不会被恢复解除 |
| VEIL_BACKUP_DIR | 下次 update.bat / 下次备份命令 | 保留（.env） | `git pull` 不动 .env（gitignore） | `.env` 被恢复时回退到备份里的值 |
| 备份内容/排除/版本 | 代码发布 | 保留 | 随代码；旧备份仍可 verify 因版本号未变 | 恢复后数据回到备份时点 |
| 备份/安全副本 | 每次 create/restore | 保留 | 保留 | 不自动清理 |
| `[executor]`（maintenance_hours 等） | 重启加载角色（load_character） | 保留 | 需确认备份/角色文件不被覆盖 | 随 characters/ 恢复 |
| workbench.json | 重启 | 保留 | 保留（在 data/） | 随 data/ 恢复 |
| executor.enabled 关闭 | 重启后 `maintenance` 工具不再注入 | 保留 | 保留 | 随 data/ 或 characters/ 恢复 |
| 维护任务 deadline | 创建任务时写死 | 保留 | 保留（在 data/<角色>/maintenance/） | 随 data/ 恢复（可能已过期，之后动作全拒） |
| GitHub token | 重启（MaintenanceManager 构造时读路径） | 保留（secrets 目录） | 保留（不在仓库） | 不在冷备份内（secrets 目录在项目外） |
| update.bat / update-main.cmd | 双击立即 | — | 自身可能被更新 | 不涉及 |
| 依赖锁 | 下次 setup/update/CI | — | 随代码 | 不涉及 |
| context-log 保留 500 | 每次追加 | 保留 | 保留 | 随 data/ |
| request-snapshots 保留 40 | 每次模型调用 | 保留 | 保留 | 随 data/ |
| tool-errors.jsonl | 每次内部异常 | 保留 | 保留 | 随 data/（无轮换） |
| 事件回放 5000 | 运行中 | 清空（内存） | 清空 | 清空 |

---

## 6 风险与建议分类

| 风险 | 证据 | 建议 | 分类 | 优先级 |
|---|---|---|---|---|
| 备份/安全副本无限增长，最终撑爆磁盘 | backup_restore.py:923-944（只统计）、brain_backup.py:133-140 | 管理中心提供「保留 N 份/保留 M 天」并默认 dry-run；先做只读统计页 | U/A | 高 |
| 两套更新器，页面指向的 update-main.cmd 不做数据备份 | diagnostics.py:1134-1135、update.bat:44-53、update-main.cmd:35-53 | 统一为一个入口；或让 update-main.cmd 也调用 pre-update | A/S | 高 |
| 冷备份明文包含 .env 与 brain.db，且无加密/访问提示之外的保护 | backup_restore.py:23,407-411 | 管理中心显示「明文备份」警示；提供可选加密/目标目录权限检查 | S | 高 |
| 诊断「钥匙没有配好」误报 | diagnostics.py:415 vs veil_agent.py:5032、model_settings.py:162-164 | 改为与 _env_values() 同源；并区分「.env 有、进程环境无」 | X | 中 |
| 诊断上限多层不一致，页面看不到 3 天前异常 | diagnostics.py:42,1112、web_agent.py:2978、diagnostics-page.js:198,379 | 页面提供 hours/limit 选择器；统一「总数 vs 可见数」文案 | U/A | 中 |
| 审批 600 秒与 24 小时两个「过期」混淆 | web_agent.py:111、pending_approvals.py:13,83、chat.js:1692 | 页面分别标注「本次对话审批 10 分钟」「持久请求 24 小时」 | U | 中 |
| 自动暂停自主活动缺少可见解释入口 | runtime_monitor.py:63-75、veil_agent.py:2708-2721 | 活动页显示「因不确定写自动暂停」与恢复条件 | R/P | 中 |
| 运行安全摘要无历史，故障后无法回看 | runtime_monitor.py:9-10,46-61 | 管理中心只读展示最近一次 + 可选保留 N 条（需 lan 同意） | R | 中 |
| CI「两次」无强制，可能无限重试 | maintenance.py:1150 | 要么加计数，要么删掉这句话 | X | 中 |
| 自维护无页面，Lan 只能靠模型转述 | maintenance.py:1514-1540、veil_agent.py:2470-2473 | 管理中心加只读任务卡（stage/deadline/门禁），写操作仍走 y/N | A/S | 中 |
| 恢复后桌面设置放到旁边目录，用户以为失败 | backup_restore.py:627-659 | 恢复结果页明确列「已写回/放在哪里」 | U | 中 |
| token 走 URL query | main.cjs:545、maintenance.py:509 | 保持现状可接受；文档写明不要分享带 ?t= 的地址 | S | 低 |
| localStorage 外观不在备份 | chat.js:2125-2138,2505-2507、backup_restore.py:26-30 | 纳入 desktop.json 或备份说明中明确「不保留」 | X/A | 低 |
| KEEP_DAYS 死键、DIAGNOSTIC_HOURS 死常量 | pending_approvals.py:12、web_agent.py:2935 | 删除或接线 | X | 低 |
| workbench.json 注释与允许键不一致 | veil_agent.py:798 vs 814-816 | 改注释 | X | 低 |
| 恢复标记检查依赖 data 目录位置 | veil_agent.py:435、backup_restore.py:48 | 文档写明恢复目标必须用 <target>/data 作为 --data-dir | P | 低 |
| 诊断页无法在独立窗口打开 | chat.html:38、main.cjs:531-539 | 若要独立窗口需加白名单 | A | 低 |
| update.bat 死代码行 | update.bat:68-69 | 清理 | X | 低 |

---

## 7 无法确认项

1. diagnostic.log 的位置/保留期：全仓 `grep -rn 'diagnostic.log'` 0 命中（.py/.js/.cjs/.md/.json/.cmd/.bat），代码中没有写入或读取该文件名。无法确认它是旧版本遗留、本机运行目录里的文件，还是计划中的名称；本次不猜。
2. logs/ 目录的实际内容与位置：`.gitignore:13` 忽略 `logs/`，但代码中没有向 `logs/` 写入的路径；只有 `data/<角色>/memory/_agent/server.log` 被注释提及（`memory_agent.py:438`），该文件由记忆 sidecar 生成，不在本快照的 Python 代码里。无法确认 logs 目录是否存在及保留策略。
3. 本机实际生效值：`.env` 的 `VEIL_BACKUP_DIR`、`data/peter/workbench.json` 是否存在及其 `enabled`、`[executor]` 是否在正式 `character.toml` 中配置、`maintenance_hours` 实际值。快照不含 `data/`（`.gitignore:11`）与本机 `.env`（`.gitignore:2`），且禁止读正式运行目录，无法读取。
4. 备份目录当前份数/占用：需要运行 `backup_restore.py usage`（只读），本次未在正式目录执行；无法给出实际数值。
5. 正式服务当前 `runtime-status.json` / `costs-unverified.json` 状态：禁止读正式运行目录，无法读取。
6. Lan 本机实际用哪套更新器（`update.bat` 还是 `update-main.cmd`）：仓库两者都在，AGENTS.md 提到 `update-main.cmd`，`docs/backup-and-restore.md` 描述 `update.bat`；无法从代码判断她的日常入口。
7. 更新失败后的代码回退：`update-main.cmd` 有 `backup/main-before-update-<sha>` 分支与 stash（73-110、148-161），`update.bat` 失败只停不改数据（139-152）。是否存在仓库外的手工回退脚本无法确认（仓库内没有）。
8. 诊断页在 detached 视图的实际可用性：代码层面白名单不含 `diagnostics`（main.cjs:531-539），但无法确认是否存在其他入口（如开发者工具直接开 URL）；按代码应为主窗口专用。
9. brain.db 是否包含未 checkpoint 的 WAL：取决于运行时，仓库无法判断；`brain_backup.py:90-93` 会在检测到时拒绝。
10. pre_update_backup 在非交互环境的行为：`confirm` 默认是 `input`（`backup_restore.py:662`），但 `update.bat` 调 `pre-update` 时不需要交互（不触发 replace），所以无影响；`restore --replace` 在无 stdin 时的异常文本未在测试中确认。
11. diagnostics_snapshot 是否会被其他进程写：只有 `web_agent.py:2963` 一个写入点（grep 已核对），无法确认是否有外部脚本也在写。
