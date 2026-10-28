# 工单 155：Peter 的独立浏览器交互

## 目标

Lan 用现有语音或文字让 Peter 找一个有意思的 B 站视频，Peter 能在自己的隔离浏览器选择并打开页面、根据页面证据报告准备状态；自主醒来时也能探索公开网页。

## 当前状态

- 分支 `ticket/155-isolated-browser`，基于 main `42f277c11cf1fc0903fdb5322aaa663e0c43e9f8`。154 已由 PR #202 合并，该 main 的四项跨平台检查通过，154 的 Lan Windows 体验仍待验收。本单不自动合并。
- 复用官方 `microsoft/playwright-python` 1.64.1（Apache-2.0，支持 Windows，仓库已有依赖及 Chromium 安装步骤），不复制第三方源码。核对过 `Browser.new_context`、`BrowserType.launch` 的实际安装源码，固定源码为 [`_browser.py` 的 new_context](https://github.com/microsoft/playwright-python/blob/bcb9debb4f3a9bb22f954e0f71e4c5b512ad7142/playwright/_impl/_browser.py#L130) 与 [`_browser_type.py`](https://github.com/microsoft/playwright-python/blob/bcb9debb4f3a9bb22f954e0f71e4c5b512ad7142/playwright/_impl/_browser_type.py)。官方 [Playwright MCP](https://github.com/microsoft/playwright-mcp/tree/0c4107acbecb75472b23c05c0e42471a0011874f) 已评估，其源码入口已迁往 Playwright monorepo；本单直接使用已有 Python 库，由 VEIL 裁剪工具和执行权限，不增加 Node MCP 服务或第二个 Agent。
- `browser_tools.py` 提供单一 `browser` 工具：open / observe / click / scroll / close。click 只导航最新观察里的可见公开链接，不执行页面点击处理器；含链接的翻页可用，纯按钮/表单翻页暂不支持。搜索复用现有 `web_search` 或公开搜索结果 URL。工具开关为角色配置 `[web].browser_interaction`，Peter 开启，其他角色默认关闭。沿用既有语音转文字，无新语音服务。
- 新启动独立 Chromium、全新非持久 context，不连接 CDP、Chrome 扩展或用户资料；不读取 Lan 的 Chrome、Cookie、存储或账户。现有 Chrome Guest 启动工具及 UIA 白名单保持原边界，不能用本工具接管其窗口。页面会保留供 Lan 查看，close 或退出 VEIL 清理本工具会话。
- 最小版只读公开 HTTPS/443；不支持 HTTP、账户页面、登录、发送、点赞、评论、发布、购买或下载操作。无任意脚本、选择器、输入、Cookie、存储、上传、文件或凭据接口；所有请求只允许 GET/HEAD，禁 service worker、WebSocket、额外页面与权限，拒绝下载并关闭弹窗。Chromium 沙盒开启；独立本地 CONNECT 代理将 DNS 验证到的公网 IP 直接拨号，拒绝私网、混合 DNS、回环及 DNS 重绑定，禁 QUIC/非代理 WebRTC。每会话最多 64 个流和 100 MB 传输。
- 共享 `desktop_access` 互斥、`DesktopInputMonitor` 人工输入优先（3 秒空闲）；聊天停止与自主暂停/插话信号进入浏览器操作。每轮最多 20 步、5 分钟，单步 22 秒、导航 12 秒；取消关闭本工具浏览器，清理不确定时禁止继续。页面结果最多 8192 UTF-8 字节，作为不可信网页数据交给现有 Chat 工具循环，活动日志不额外保存整页正文。沿用现有模型、上下文及费用硬限制，无新增付费服务。
- 已通过 8 项新增检查，含真实隔离 Chromium 的滚动、链接翻页、视频页、Cookie 清空、途中取消，以及聊天/自主工具派发。130 项现有工具/桌面/网页/自主检查通过；全量运行 710 项：700 项通过、10 项跳过。真实公网 B 站已从首页链接进入视频 `BV1eKMn6MEHo`，读到标题、正文和可见 video；观察为 `readyState=0, paused=true, currentTime=0`，因此只确认进入页面，不能确认视频已加载或播放。真实科普搜索已返回候选并进入无穷小亮的视频 BV1mfn662EHb；Lan 的整段使用场景尚待验收；无付费模型调用。
- Windows 可见窗口开页检查被实际人工输入优先拦下；不把无界面 Chromium 检查或脚本选择当作 Lan 实机体验。CI 与功能/安全审查按 PR 当前提交核对。未完成 Lan Windows 语音/文字整段体验验收。

## 验收步骤

1. 获准合并并更新后，在已有语音或文字聊天说“帮我打开 B 站，找一个有意思的视频，我回来要看”。短暂放开鼠标键盘，确认出现独立浏览器，Peter 能搜索、观察候选、滚动/通过链接翻页并停在选中的视频页；回答含标题和页面证据，视频没播放时如实说明。原有个人 Chrome 的标签和登录状态不被读取或操作；不要在 Peter 的浏览器登录账户。
2. 操作中移动鼠标/打字或按“停下”，确认 Peter 停止、不会继续排队点击；停止可能关闭其浏览器。自主活动开启后，让他在一次自然醒或既有闹钟中逛公开网页，暂停自主活动或发新消息会让位。预算、夜间安静保持原限制。
3. 要求登录、点赞、评论、下载、读取个人 Chrome 或打开内网地址，确认拒绝；要求关闭自己的浏览器，确认个人 Chrome 不受影响。网站验证码、地区限制、资源未加载或只支持按钮的页面应明确说明，不能声称已完成任务。
