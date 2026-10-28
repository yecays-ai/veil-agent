# 工单 149：Peter 实验室自主预览

## 目标

Peter 在自己的 `lab/` 里创建或修改 HTML 作品后，可以直接调用 `preview_lab_page` 打开实际渲染页面。截图会作为本轮临时视觉输入交给当前模型；工具还返回页面加载状态、JavaScript 异常、静态资源失败和被拦截的网络请求。Peter 可以按截图和错误再修改文件并预览，最后仍用现有 Lab 附件能力把 HTML 发给 Lan。

预览只在 Windows、Lab 启用且文件服务正常时开放。作品及资源路径逐段解析并检查 Lab 权限；拒绝符号链接、junction、目录和不支持的扩展名。单文件不超过 5 MB，每次预览最多 128 个资源请求和 20 MB 资源数据，每轮最多两次预览，30 秒总超时。预览不写截图到磁盘或聊天历史，截图只进入当前模型请求；图像输入被当前模型拒绝时，Peter 会收到“尚未看到截图”的明确事实并以文本恢复。

## 实现与依赖

复用 Microsoft Playwright Python 的 Chromium 控制接口（Apache-2.0，固定 `playwright==1.64.1`），不另造浏览器自动化引擎。此版本兼容仓库 Python 3.11/3.12；安装脚本和更新脚本会安装锁定的 Playwright 包及其 Chromium。参考 [Playwright Python 安装文档](https://playwright.dev/python/docs/intro)、[BrowserType API](https://playwright.dev/python/docs/api/class-browsertype) 和 [Playwright Python 1.64.1 项目元数据](https://github.com/microsoft/playwright-python/blob/v1.64.1/pyproject.toml)。

每次预览使用新的非持久 BrowserContext 和独立 headless Chromium，不读取真实浏览器配置、Cookie、扩展、历史或登录态。临时 HTTP 服务仅从 Peter 的 Lab 逐请求提供资源；CSP sandbox、Chromium sandbox、路由拦截、禁用下载/Service Worker/弹窗、封闭 WebSocket 与浏览器端连接 API，限制网络、文件、下载和外部程序启动。页面可以执行普通游戏 JavaScript。现有模型请求的图像路径使用与桌面观察相同的临时消息机制，并计入现有请求/费用预算；截图不进入长期记忆或持久聊天记录。默认 `deepseek-flash` 的图像支持见 [DeepSeek Vision 文档](https://api-docs.deepseek.com/guides/vision/)，其他模型不支持时不声称已视觉检查。

## 状态与证据

- 分支：`ticket/149-peter-lab-preview`；当前 PR 与自动检查状态记入 `STATUS.md`。
- 已添加路径/权限、隔离策略、工具授权、两次预览上限、调用超时、截图模型传递和非视觉模型恢复测试。
- 真实 Chromium 渲染测试配置在 Windows Python 3.12 CI：验证截图、JS 错误、缺失资源、网络与本地文件请求拦截，以及连续预览的隔离。
- 本地 Windows 受限执行环境不允许绑定回环端口，且不能创建 junction，因此本机不能完成真实 Chromium 端到端运行；这些环境限制不代表 Windows Lan 体验已验收。
- 自动测试/CI、PR 合并、Lan 的实际 Windows 体验分别记录，不互相替代。

## 验收步骤（Lan 在 Windows 上做）

1. 更新 VEIL 并重启后，对 Peter 说：“你自己做一个蜘蛛主题的小游戏，做好以后先打开看看效果，有问题自己修改，满意了再发给我。”确认他创建 HTML、调用预览，并能描述截图里真实的布局和交互，再按需修改并重新预览；最后发送附件，点击后能打开游戏。
2. 在作品中故意加入按钮重叠/文字溢出和一处 JavaScript 运行异常、缺失图片。确认 Peter 能从截图发现布局问题、从诊断看到异常/资源失败，并在最多两次预览内尝试修正；若一轮不足，可在下一轮继续。
3. 确认页面无法读取 Lab 外文件、访问外网或浏览器个人资料，不能下载/弹窗启动程序；切换到不支持图像的模型时 Peter 必须明确说未看到截图。用一个损坏或卡住的页面确认预览有明确错误/超时，聊天仍可继续。

