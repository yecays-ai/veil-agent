# VEIL Agent：本机文字聊天

Windows 桌面版 0.1：透明 Peter 可拖动、调整大小，点击收放原 Agent 的真实聊天。打包版完整解压后双击 `VEIL.exe`，首次选择已有 Agent 文件夹；源码版运行 `setup-desktop.bat` 后双击无命令窗口的 `start-desktop.vbs`（原 `start-desktop.bat` 仍兼容）。安装、数据边界和体验步骤见 [桌面版说明](desktop/README.md)。Memory Brain、Lab 桌面入口尚未接通；真实 Windows 体验待验收。

双击启动文字聊天，关掉再开接上最近对话。工单 005 增加后台补记和长期记忆；工单 006 让 Peter 在自己的测试文件夹里读文件，并在你同意后新建文件。

## 当前能力与验收状态

- **已合入、可按本文操作：** Windows 终端与网页聊天、角色隔离的本机聊天/记忆/预算、文件工具、完整冷备份与恢复；047 的桌面壳、048 的实验 017 HTTP 兼容、050 的固定依赖安装也已合入主线。
- **自动检查已通过，但不是 Windows 体验验收：** 047 的虚构 Electron 窗口检查，050 的 Windows/Linux × Python 3.11/3.12 全套测试和依赖安全扫描，048 的同一四平台全套测试。它们不读取真实人设、聊天或记忆，也不证明你的电脑上已能正常聊天。
- **仍待 Lan 在 Windows 实际体验：** 桌面壳、冷备份/恢复与多项记忆/长期任务改动。具体操作步骤以 [STATUS.md](STATUS.md) 的“Windows 验收”为准；未验收不等于功能不存在，也不能写成已体验通过。
- **记忆边界：** Brain 是唯一记忆后端；向量调用使用共享预算估算，pinned/importance 由程序写入；不再启动 Ombre。历史 Ombre 文档只作归档，不是运行回退路径。

## 放在 E 盘

整个项目可放在任意可写目录，包括 E 盘。启动器会先切到自己所在的文件夹：`.venv/`（依赖）、`data/`（聊天和花费）、`persona/`（人设）、`.env`（密钥）都跟着项目走。安装临时文件也放在项目里的 `.local-tmp/`，不使用 pip 下载缓存。项目迁移到另一目录后请重新建立 `.venv`，保留 `data`、`.env` 和 `persona`。

## 首次安装（Windows）

> 记忆由 VEIL Brain 唯一接管，不依赖 Ombre 进程。全新角色首次启动会自动创建空 `data/<角色>/brain.db`；如果角色已有数据但 `brain.db` 缺失或损坏，启动会明确停止并提示从备份恢复，不会创建空库掩盖数据丢失。事件附账本也已并入 brain.db，备份/恢复/导出覆盖完整事件生命周期。进度见 `STATUS.md` 与 `docs/tickets/p3/`。

1. 准备 Python 3.11 或 3.12。安装器的自定义安装可选择其他盘；不要把 Microsoft Store 的空启动入口当成已安装 Python。双击 `setup.bat` 按 `requirements.lock.txt` 安装固定版本的项目依赖，并在隔离环境安装固定版本的网页读取 MCP 服务；需要联网，只下载软件，不调用模型。项目更新后也要重新运行 `setup.bat` 以安装新增或更新的依赖；聊天、人设和记忆数据会保留。已安装过旧版也要重新运行一次；安装只更新 `.venv` 依赖和文件工具，不复制 Ombre 实现、不安装本地大模型；启动不会自动下载安装。其他 Python 版本不在支持范围内。
2. 将 `.env.example` 复制为 `.env`，在 `DEEPSEEK_API_KEY=` 后填写自己的密钥。将 `persona/peter.example.md` 复制为 `persona/peter.md`，再替换成本机真实人设；保留默认英文、需要时翻译成中文的规则。程序不会替你创建或覆盖这两份私有文件。人设缺失或为空会直接停止。
3. 在本机 `.env` 添加 `EMBED_API_KEY=` 并填入硅基流动密钥（不要发给别人）。双击 `start.bat`。用中文聊几句、说“翻译一下”，关掉再开，问“我们刚才聊到哪了”。没有真实人设或密钥前，只能运行离线测试，不能宣称已完成真实聊天验收。
4. 文件工具需要另装 Node.js LTS（[官网](https://nodejs.org/en/download)）。`setup.bat` 会联网把固定版本 `@modelcontextprotocol/server-filesystem@1.0.0` 安装到 `.deps\filesystem\`；安装失败时聊天、记忆和其余安装仍继续，但文件工具暂不可用，可在网络恢复后重跑 `setup.bat`。

## 日常使用

- `/cost`：先显示今天、本月按配置单价核算的费用、待核对预留，以及今天的聊天/自主活动分项（自主额度是上限，不是消费目标）；再显示今天聊天模型的缓存命中率。缓存行不含记忆打标和向量调用。
- `/self`：原样显示最近一次发给模型的“当前状态说明”，含角色、供应商、模型、本机时间、记忆范围、能力边界及请求前花费。在本次启动还没有发送消息时，显示标为“预览”的当前说明；真正发送前会刷新。这个命令不调用模型、不产生费用。
- `/quit` 或 Ctrl+C：退出。回复先落盘再显示；请求中途退出时，已输入的原文仍在，未完成请求的预留费用下次启动时按上述规则处理。
- `/memory`：最近 20 条记忆的编号、保存时间、标题；还没有记忆时明说。
- `/delete 编号`：先显示这条，再输入 `/delete 编号 确认` 才执行；删除后不再出现在 `/memory`、不再被想起，也不能恢复：程序把记忆正文、`memory-versions` 里的旧版本和删除申请里的标题从 Brain 与本地记录中抹掉（工单 091）。Peter 申请删除时也一样，都要先经 Lan 同意。一次一条，不批量删。隐藏功能（`/forget`、`/restore`）已在工单 090 取消。
- 不进入聊天查花费：在项目目录运行 `.venv\Scripts\python.exe veil_agent.py --cost`。
- 另一个角色：建立 `characters/<id>/character.toml` 和自己的本机人设，然后运行 `start.bat --character <id>`。人设、历史按角色分开；费用共用一个总账，只能同时开一个聊天窗口。

## 桌面版（浏览器网页版已在工单 093 停用）

更新：先退出 Peter，再双击根目录 `update.bat`（拉新代码、补装组件、桌面代码有变化时重新打包 `VEIL.exe`，然后打开 Peter）。只 `git pull` 不会更新桌面软件。

日常聊天用桌面版：双击 `start-desktop.vbs`（首次先运行 `setup.bat` 和 `setup-desktop.bat`，见 `desktop/README.md`）。桌面版在后台用 `web_launcher.py` 启动本机聊天服务（`web_agent.py`，只监听 `127.0.0.1`，带一次性令牌），聊天、记忆、花费和 `/memory` 等命令与终端版共用同一套代码。浏览器网页 `web/index.html` 已删除；`start-web.bat` 现在直接打开桌面版。桌面版和终端版共用聊天锁，同一时间只能开一个。

## 配置与预算

`characters/peter/character.toml` 的 `provider = "deepseek"` 改为 `provider = "gpt"`，就会同时切换模型、接口、密钥名称和价格配置。GPT 需在本机 `.env` 填 `OPENAI_API_KEY`。密钥只读取项目 `.env`，不读取系统环境变量，也不把密钥写进记录。

更方便的做法（工单 134）：桌面 App「更多」→「模型」里添加中转站接口和模型，给「Peter 本人」和「记忆后台」分别选模型，顶栏下拉随时切换 Peter 的模型。这些选择存在本机 `data/peter/models.json`，key 写进本机 `.env`，都不进仓库；character.toml 里的 provider 仍是默认。

默认读回最近 20 轮原文；可调 `history_rounds`。聊天记录是 `data/<id>/chat.jsonl`，每条有时间、角色和原文，只追加，不用模型概括替代原文。中断留下的用户原文也会读回；屏幕显示的轮数只计有回复的轮次。按字节保守估算的输入超过配置的长度边界时，这一次的请求会自动少带最旧的几轮原文（整轮丢，磁盘上的完整记录和长期记忆不受影响），屏幕上提示一次；如果人设、记忆和这条输入本身就超过边界，才会提示“太长，未发送”，不保存这条输入，也不退出，缩短后重发即可。完整历史仍留在磁盘。

每次请求会在人设之后、聊天原文之前加一条独立的 system 说明，明确这是程序提供的当前事实。它告诉角色自己是 AI、当前模型、文字聊天和已连接的自动记忆能力，以及没有角色主动工具或语音。未启用记忆的角色如实显示没有长期记忆。与人设描述冲突时，以当前事实为准，仍保持角色口吻。说明来自角色配置、时钟、花费总账及实际接入状态，不含人设正文、密钥或本机完整路径。

## 自动记忆

Peter 自己判断、自己调用记忆工具保存（`hold`）和查找（`breath_search`），每次保存屏幕有一行 `🔧 Peter 用了 hold`，同一件事再记会合并。`[memory] mode` 只能是 `"native"`；旧的“每 4 轮后台补记”（extract）已在工单 090 退役。启用记忆需在角色配置写 `[memory] enabled = true`。

记忆由本机 Brain（`data/<id>/brain.db`）唯一保存：正文、版本、向量、事件、事件阶段、证据、版本、修订、操作、投影、审查和 dream 状态都在同一个数据库里。开场读一次重要记忆，每次发消息前检索并按相关度挑选；没有命中会明确告诉模型不要假装记得。pinned/importance 由程序控制写入和排序；记忆页可浏览、置顶、归档和恢复。

程序不自动删除记忆；旧 Ombre 的自然衰减引擎已移除，P6 的认知字段仍保留在 Brain 中，但不在 P3 启用动态权重。向量默认是硅基流动 `BAAI/bge-m3`（1024 维），[官方价格页](https://siliconflow.cn/pricing)在 2026-10-19 标为免费；地址、模型、维度和每百万输入 token 人民币单价在角色 `[memory]` 配置中，更换模型时必须一起核对更新。密钥只从本机 `.env` 读取，不写日志。jieba 或 rank-bm25 缺失时启动会提示重新安装。

每个角色只访问自己的 `data/<id>/`；备份时先退出程序，再完整复制 `data/`、`characters/`、`persona/` 和 `.env`。Brain 备份/恢复使用 SQLite 在线备份，导出的历史归档只用于离线回退，不再是运行路径。

向量每次可能联网的工具调用前都预留并按保守输入量记账，**是估算，不是供应商返回的实际用量**；覆盖写入检索/索引、内部查重及 SDK 默认最多两次内部重试，可能高于实际费用。我们不额外重试工具调用。当前免费模型金额为零，仍留调用记录；更换收费模型后共享 ¥10/天和 ¥300/月上限。失败且结果不确定的预留保留。向量不可用时提示“记忆检索降级为关键词”，关键词质量较低，不保证换说法能找到。记忆正文、原文来源和查询文本会交给向量供应商。

说明里的花费与 `/cost` 来自同一份账本和同一种格式，但时间点是**本次请求发出前**，不包括正在生成的这条回复。可以先输入 `/cost`，再问“今天花了多少钱”，通过 `/self` 核对模型实际看到的数字；回复完成后再查 `/cost`，会增加这条回复的费用。`/self` 保留当时发出的原文，不会悄悄改成回复之后的账本数字。模型能否以角色口吻准确表达这些事实，仍需真实聊天验收。

本地预算固定 ¥10/天、¥300/月，所有角色合计，不设请求次数限制。请求前按配置的整个输入额度和最大输出量预留费用，输入同时按 UTF-8 字节数加消息开销做保守长度检查；请求后按接口返回的输入、缓存命中、输出 token 用量结算。日/月按本机日期、请求开始时间归属。程序不自动重试。失败、中断、缺少用量时，因为无法确定供应商是否扣费，预留额先保留；下次启动时，已记录“发出”的请求按预留上限记为已花费一次（不再占用以后的预算），没发出的请求释放为 0（例外：向量、记忆模型、记忆整理、便签这类估算预留没有发出标记，重启时一律按预留上限记账）。聊天请求遇到接口报告的用量超过配置上限时，按它报告的实际用量记账（标记 usage-over-bound）并继续运行；其他调用（便签等）仍会中止并保留预留。不要删账本来“重置”花费，应先核对供应商账单。

价格是本机核算参数，不是供应商账单。默认 DeepSeek 配置采用[官方人民币高峰价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)，未按空闲时段减半；因此可能比实际扣费高。GPT 配置使用保守的人民币核算单价，不实时换汇。切换模型或价格变动时，先核对供应商价格并一起更新对应配置；预算保护以配置正确、供应商遵守 token 上限为前提。

## Peter 的文件工具

- 工具只访问 `data/peter/workspace/`，首次启动时自动创建。这是 Peter 唯一的文件根：自己的作品、实验、项目都放这里；`lab/` 只是其中一个分类（离线 Python 实验、HTML 页面、小游戏），不再是另一套文件系统。请只放假文件或你愿意让所选模型读取的文件；文件内容会发送给聊天模型供应商。
- Peter 可直接列目录、查看目录树、读文本文件、搜索文件，也可以免确认地在整个 workspace 里新建目录和文件、用 `write_file` 覆盖、用 `edit_file` 原地编辑、追加、`move_lab_file` 移动/重命名、`delete_file` 删除。每次覆盖、编辑或删除之前，程序把旧版本存到 `data/<角色>/own-work-versions/`（lab 里的文件在 `lab/`，其余在 `workspace/`），他看不到也删不掉，可恢复。新建、追加、改写后核对 SHA-256；单次默认最多 200000 个 UTF-8 字节（`lab_max_write_bytes`），超限时零写入。路径越出工作区（包括 `..`、符号链接或联接点）会被拒绝；MCP 自带的 `move_file` 和未列出的工具仍禁止。
- 例外只有成长日记 `growth-journal.md`：不能覆盖、编辑、删除或移动，只能用 `append_file` 追加，未授权时每次都须在终端输入 `y`；让 Peter 调用 `grant_append` 并输入一次 `y` 可持续授权（跨重启，只绑定这个文件，文件被替换后需重新授权）。
- 工作台（见工单 237/238/247）开启后，OpenCode 也在同一个 workspace 里干活；改前自动留快照，日记在容器里被只读屏蔽。更早版本单独的 `data/<角色>/workbench/` 文件夹会在第一次使用工作台时整个搬进 `workspace/from-workbench/`，不覆盖、不丢文件。
- `[tools] lab = "lab"` 可改为其他安全单级目录名，`lab = false` 关闭实验室功能（Python 实验、页面预览、小游戏），不影响 workspace 权限；lab 目录是符号链接或 junction 时实验室功能关闭。Peter 用 `desktop_create_file` 在你桌面/文档/图片里新建的文件记在 `data/<角色>/desktop-created.json`：只有这些、且你没改过的文件，他能用 `replace: true` 改写（旧版本存到 `own-work-versions/desktop/`），或用 `desktop_move_to_trash` 挪进 `lab/trash/desktop/`；你的文件一律不动。
- 每轮最多 6 次模型步骤（`max_steps`），每次模型请求计入共用预算；单个工具结果最多带回 8192 字节，超出会截断并告知总字节数。达到步骤上限后会提示本次工具步骤用完。
- 在 `characters/peter/character.toml` 的 `[tools]` 中可设置 `enabled = false` 关闭工具；`folder`、`max_steps` 和 `max_result_bytes` 是本地工具配置。程序权限白名单不由人设或配置改变。
- 每次调用会在屏幕显示一行。工具过程保存在 `data/peter/tools.jsonl`，聊天记录仍只存你的消息和 Peter 最终回复。能力清单保存在 `data/peter/capabilities.json`，只写相对文件夹名和工具权限，不含本机完整路径。

## 用邮件和 Peter 聊天（工单 098）

不在电脑前时，Peter 可以给你发邮件，你直接回信，他再用邮件回你。电脑要开着、服务要在运行。

1. 给 Peter 准备一个 QQ 邮箱（可以新注册一个，也可以用你自己的小号）。网页版 QQ 邮箱 → 设置 → 账号与安全 → 安全设置 → 开启“POP3/IMAP/SMTP/Exchange/CardDAV 服务”，按提示拿到“授权码”。授权码不是 QQ 密码。
2. 在 `.env` 里填三行：
   ```
   PETER_EMAIL_ADDRESS=Peter 用的 QQ 邮箱
   PETER_EMAIL_AUTH_CODE=刚拿到的授权码
   LAN_EMAIL_ADDRESS=你自己收信的邮箱
   ```
3. 重启。只认 `LAN_EMAIL_ADDRESS` 发来的信，别的邮件不看、也不标已读。每天最多发 20 封（`character.toml` 的 `[email] daily_max_sent`）。往来记录在 `data\peter\email-log.jsonl`（只有时间、编号、主题），对话本身和桌面聊天记在一起。

## 用 QQ 和 Peter 聊天（工单 102）

不注入 QQ：程序像人一样操作电脑上登录着的 Peter 的 QQ 窗口。只在你两分钟没碰电脑、也没在全屏看视频时才读和发。

1. Windows 设置里关掉自动锁屏（锁屏后程序没法打字；显示器可以关）。
2. 在电脑上登录 Peter 的 QQ 小号，加你为好友，双击你打开一个单独的聊天窗口，一直开着。
3. `.env` 加一行 `QQ_LAN_CONTACT=` 那个聊天窗口顶部显示的你的名字，重新运行 `setup.bat`（会装 `uiautomation`），再重启。
4. 每天最多发 100 条（`character.toml` 的 `[qq] daily_max_sent`）；记录在 `data\peter\qq-log.jsonl`（只有时间和收/发）。

## 私有文件和备份

`.env`、本机人设、`data/`、日志、虚拟环境均已忽略。仓库只包含示例人设和空密钥模板。聊天发送时，人设与最近上下文会交给你选择的模型供应商。不要提交或公开私有文件。

完整的备份、更新前自动备份、恢复被打断后的处理步骤见 [docs/backup-and-restore.md](docs/backup-and-restore.md)。聊天或账本文件损坏、恢复旧备份后的费用账本核对，见 [docs/data-repair-and-ledger.md](docs/data-repair-and-ledger.md)。

### 手动冷备份

先用 `/quit` 退出并等待窗口关闭。备份程序会再检查聊天和 Brain 的写入锁；若仍在运行会停止，不创建备份。备份根目录必须在项目目录之外，建议选择另一块启用 BitLocker 的本地磁盘。备份包含 `.env`、`characters/`、`persona/` 和完整 `data/`：包括聊天、费用账本、Brain 记忆与事件生命周期、长期任务、授权、工具记录和 workspace。清单记录文件相对路径、大小和 SHA-256，不记录文件内容或项目本机路径。

```powershell
.venv\Scripts\python.exe backup_restore.py create --destination "X:\VEIL-backups"
```

成功时屏幕会显示新备份的完整保存位置。备份包含明文人设、记忆和可能含 API 密钥的 `.env`；程序不加密、不上传，也不打印其内容。只把备份放在加密的本地磁盘或受访问控制的离线介质上，不放云同步目录，也不要提交到 GitHub 或发给第三方。虚拟环境、第三方依赖、临时文件、进程锁/标志和运行日志会重新生成，因此不备份。

### 校验与恢复

先进入已安装好 VEIL 代码和依赖的项目目录；校验命令只读备份。恢复必须给出明确的项目根目录：默认遇到同名文件就拒绝且不更改目标；只有加 `--replace` 并输入 `RESTORE` 才会继续，目标中的 `data/`、`characters/`、`persona/`、`.env` 会整体移到目标旁的 `*.before-restore-*` 安全副本，再恢复干净备份。恢复前会验证格式/数据布局版本、角色配置和每个文件的校验值，任何不匹配都会停止。

```powershell
$backup = "X:\VEIL-backups\VEIL-Agent-Backup-实际时间戳"
$target = "D:\VEIL-recovery"
.venv\Scripts\python.exe backup_restore.py verify --backup $backup
.venv\Scripts\python.exe backup_restore.py restore --backup $backup --target $target --replace
```

只在确认目标是要恢复的项目目录时使用 `--replace`。安全副本位置会在成功时显示；失败时程序会将整套原有角色数据移回目标；若文件系统错误阻止回滚，安全副本会留在原处。每个备份记录来源代码提交和数据布局版本；当前程序只接受明确支持的布局版本。

## 离线验证

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试只用假人设、假消息、假模型或本机模拟 HTTP，不读真实密钥、不访问付费接口。CI 在 Windows/Linux、Python 3.11/3.12 运行同一套测试；这些检查不代替 lan 的真实聊天验收。

## 依赖安全检查

GitHub Actions 的 `Dependency security` 每周运行一次，也可在 Actions 页面手动运行。它检查主程序与 Nowhere 的依赖锁，并在运行摘要中列出存在风险的包、安装版本、漏洞编号、说明和可用修复版本。该检查只报告，不升级依赖、不创建升级 PR；只把公开依赖名和版本发给 PyPI/OSV 漏洞数据源，不读取或上传 `.env`、聊天、记忆或本机配置。

维护者修改 `requirements.txt` 后，应先更新 `requirements.lock.txt`，再让 Windows/Linux、Python 3.11/3.12 的安装与完整测试通过；不要直接把扫描结果自动应用到 Peter 的安装中。
