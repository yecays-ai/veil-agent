# 156 · Peter 专属小红书阅读与本地图文草稿

## 目标

Peter 在聊天及自主醒来时，用独立账号按自己的兴趣搜索、阅读笔记和有限评论，选择性使用已有念头/记忆能力，在自己的 Lab 写原创图文草稿；用户在专属窗口登录，不把密码交给模型。此版本的公开发布、评论、私信、关注、点赞和收藏全部关闭。乌有乡与独立 Python 实验室不在本单范围。

## 当前状态

- 从最新 main `27cceb4e8a746b83b06f130affbbb25378cddddf` 开始，分支 `ticket/156-xiaohongshu`。155 的 PR #203 已合并，其实现 head 跨平台 Python、审计等检查通过；独立浏览器的 Lan Windows 整段体验仍待验收。
- 复用结论：直接使用 **xpzouying/xiaohongshu-mcp v2.5.5** 的独立 Windows 服务与登录程序，VEIL 只增加限制明确的阅读适配器，不重写站点自动化。GitHub 相似项目搜索还找到 iFurySt/RedNote-MCP、autoclaw-cc/xiaohongshu-mcp-skills；优先项目已经覆盖阅读需求，后者也是其集成层，没有必要再引入第二套。
- 已核验 [Apache-2.0 LICENSE](https://github.com/xpzouying/xiaohongshu-mcp/blob/a5c8f7799980ba1fdd501999843eb2d17e4c9a9f/LICENSE)、[Windows 官方发行文件](https://github.com/xpzouying/xiaohongshu-mcp/releases/tag/v2.5.5)。仅下载原发行文件并按固定 SHA-256 校验，不复制/分发上游源码或二进制；版权归 xpzouying 及贡献者。若未来捆绑分发须保留许可证和适用 NOTICE。上游需要额外下载 CloakBrowser，内置指纹一致性功能且本版无关闭开关；这不是普通 Chrome，不构成平台认可的证明，人工登录入口会明确告知并要求先确认当前平台规则允许自动化访问。不要求 Node/Go 来运行其预编译程序。
- 源码证据：[routes.go](https://github.com/xpzouying/xiaohongshu-mcp/blob/a5c8f7799980ba1fdd501999843eb2d17e4c9a9f/routes.go)、[service.go](https://github.com/xpzouying/xiaohongshu-mcp/blob/a5c8f7799980ba1fdd501999843eb2d17e4c9a9f/service.go)、[types.go](https://github.com/xpzouying/xiaohongshu-mcp/blob/a5c8f7799980ba1fdd501999843eb2d17e4c9a9f/xiaohongshu/types.go)、[登录程序](https://github.com/xpzouying/xiaohongshu-mcp/blob/a5c8f7799980ba1fdd501999843eb2d17e4c9a9f/cmd/login/main.go)、[Cookie 路径](https://github.com/xpzouying/xiaohongshu-mcp/blob/a5c8f7799980ba1fdd501999843eb2d17e4c9a9f/cookies/cookies.go)、[鉴权中间件](https://github.com/xpzouying/xiaohongshu-mcp/blob/a5c8f7799980ba1fdd501999843eb2d17e4c9a9f/middleware.go)。上游支持登录状态、推荐、搜索、正文和评论，也支持图文/视频发布、评论和回复、点赞/收藏；没有独立平台草稿、私信或关注接口。VEIL 不转发它的写入接口或通用 MCP 入口。
- `xiaohongshu_tools.py` 固定白名单：status/feed/search/detail。`xiaohongshu-login.cmd` 双击运行人工安装、登录、核对昵称/ID 并绑定。账号 ID 每次操作前重新验证，错误账号或缺少 ID 一律拒绝继续。专用 Cookie 位于角色私有数据目录，必须在文件工具 workspace 外；不复用个人 Chrome 资料，不回退到临时目录旧 Cookie。登录与读取有跨进程互斥。服务仅监听随机回环端口并使用随机 Bearer Token，日志不回传模型。
- 读取开关是角色配置 `[web].xiaohongshu_readonly`，默认缺省关闭，Peter 示例开启但未登录前不可用。所有外部写操作在模型 schema 和传输白名单两层关闭，开关不能开启发布；需要未来实现逐次绑定账号、操作及完整内容的人工确认后才可开放。自然语言“发布一下”在此版本也不会发送。
- 自定保守上限：每次模型请求 4 次操作，每日 60 次侧车 API 调用（账号检查也计数且持久化），调用间至少 10 秒；一次操作会先核对账号，内容读取还需第二次调用。读取时限 45 秒，另有最多约 12 秒的有界进程/线程清理，单次响应最多 1 MB，给模型最多 8 KB / 6 篇卡片 / 10 条顶层评论及各 2 条现有回复，不展开全部评论。侧车 API 调用次数不等于浏览器底层网络请求数；上游有自己的页面加载与有限导航重试。适配器不在错误后重复提交操作。限额是 VEIL 自身边界，**不是平台允许额度或不封号保证**。停止/暂停会中止等待并停止服务；错误后当前进程暂停读取，需人工检查登录环境后重启。不增加模型调用或模型预算，复用原聊天/自主活动费用硬上限。
- 返回的笔记内容均为非可信数据。正文与评论为文本，当前不分析图片画面、不播放视频；不批量收集、不自动长期保存原始阅读结果。图文草稿复用现有 Lab 读写、版本和附件交付，可包含标题、正文、自己的图片/配图计划、来源；明确是**本地草稿**，没有上传平台。兴趣选择复用已有 thought/Ombre，不增加新的兴趣引擎、记忆库或调度器。
- 平台限制：这是第三方浏览器自动化，不是平台官方授权 API。用户应在登录时阅读小红书当前协议/规则；遇到验证码、实名认证、访问限制或风控必须暂停，由用户在平台完成处理，不代答、不换代理、不绕过。不得把上游经验性“安全/发帖额度”当平台规则。上游提示同账号其他网页端登录可能挤掉本会话，建议只在移动 App 查看该账号。自动化是否符合账号当前适用规则未获平台确认，不能宣称获准或无风险。官方协议页面 [入口](https://agree.xiaohongshu.com/h5/terms/ZXXY20220331001/-1) 当前文本无法由本次抓取核验，须以用户登录时展示版本为准。
- 检查与体验分别记录：离线安全专项已通过；Windows 发行文件校验/启动参数、回归与 PR CI/评审结果见 PR。真实专属账号登录、真实推荐/搜索/评论及自主醒来草稿体验尚待验收，不能报告产品已完成。不自动合并。

## 验收步骤

草稿内容依据：[小红书官方社区公约 2.0](https://pgy.xiaohongshu.com/help/detail?id=1eda0a065dd894063c2e029a49e8f6a1&userType=4) 倡导标明 AI 参与、尊重原创、真实分享。模型工具说明已要求 AI 标识、区分虚构叙事与真实经历，不捏造真人生活经历、不搬运他人作品。该公约不代表平台授权第三方自动化访问。

1. Windows 获准合并并通过现有更新方式取得本版本后，关闭 VEIL，双击 `xiaohongshu-login.cmd`，输入 LOGIN 后在新窗口登录 **Peter 独立账号**。核对显示的昵称和 ID，输入 ID 绑定，再启动 VEIL。已有登录可回车继续；误登或失效时重新运行入口，输入 RELOGIN 清除专属环境的本机登录状态，原账号绑定和每日配额会保留。不要将密码或 Cookie 发到聊天；不用个人 Chrome 登录。让 Peter 检查小红书状态，应报告专属账号已登录；未绑定、登录过期或账号不匹配时应明确停止。
2. 聊天说“去小红书找你感兴趣的内容，选一篇读正文和评论，说说吸引你的地方，再在 Lab 准备一份你自己的图文草稿给我看”。应有来源、真实读到的正文/有限评论和可打开的本地草稿；不能声称读完所有评论或看懂未分析的图片。再观察一次正常自主醒来，让他可自行选择阅读并留下兴趣/草稿，也允许不浏览，不强迫每次醒来执行任务。
3. 让 Peter “现在发布/评论/关注”，应明确本版没有执行权限，平台账号没有新增发布或互动。读取中点停止/暂停，应停止继续请求；短时间连续操作应提示等待，登录失效/风控应暂停并让你人工处理。退出重启后限额仍保留；个人账号和浏览器继续独立使用。真实场景通过前保留“待验收”。
