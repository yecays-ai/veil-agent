# 工单 237：桌面 UI 全状态整理与预览验收

## 目标

让 Peter 的主窗口、独立页面、桌宠、菜单和弹窗在常用尺寸下对齐、完整、易操作；先由用户审阅 Windows 截图预览，再决定是否提交 PR。

## 当前状态

- 按单页主画布重新整理桌面框架：左侧导航浮动且可展开/收起，今日活动面板独立开合，页面在窄窗下为导航留出空间。
- 修复收起导航图标偏左：将导航图标和文字分离，收起时隐藏文字标签；用 Edge 预览测得 38px 按钮和 19px SVG 的中心偏差为 0px。
- 记忆刷新按钮移入分组行；生活记录筛选去掉双层边框，改为无框分段按钮；恢复工单 235 的米白、灰褐和鼠尾草绿配色。
- 生活记录总览改成固定宽度的独立月历卡片与当天清单并列；统一日期格尺寸，选中日期用圆形底色，记录改用圆点提示，具体数量仅留在无障碍标签和当天清单中，避免挤占日期格。
- 审批预览固定按 `main.cjs` 的 520×270 尺寸；正文在弹窗内滚动，操作栏尺寸固定。
- 可复核的全状态清单见 [`237-preview-manifest.md`](237-preview-manifest.md)。目前只将已提供并经用户确认的生活记录预览标为已检查；主窗口全页、原生独立窗和浮层状态保留待验收，不用本机路径或浏览器预览冒充 Windows 原生验收。
- reuse-first：核对 [InternalBeyond 的日历交互（commit 0bdac818）](https://github.com/Sui-IB/InternalBeyond/blob/0bdac818e6c4815af2a562be1b1ad4e7c6157b16/InternalBeyond.html#L7658-L7725)：月历、当天安排、回到当前日期和快速新增；VEIL 复用自身生活记录数据与渲染，在总览中并排呈现日历和选中日的日程/待办，并加入轻量日期切换动效。该仓库代码为 PolyForm Noncommercial，设计与素材为 CC BY-NC-SA 4.0，只借鉴交互，不复制代码、素材、图标或独特版式。
- 1440×900 Edge Chromium 预览：`life-calendar-preview.png`；内容为虚构记录。该图只验收浏览器布局，不代表 Electron 拆窗、拖动或真实数据验收。
- Edge Chromium 隔离夹截图为虚构内容；Electron 本轮未能启动，因此拖动、缩放、合并和系统级操作仍须真实 Windows 验收。
- 用户已确认生活记录预览并要求提交 PR（#295）；其他全状态和真实 Windows 验收尚未完成。

## PR #295 跟进

- Cubic 的 8 条意见均已核对为有效并在本地修复；最新状态以 GitHub 上本 PR head 的检查为准。最初提交的 `Tests` 和 `Dependency security` 通过，`Windows desktop` 的 smoke 在生活记录标题字号断言（预期 15px、实际 13px）失败，需以修复后的新提交重跑结果为准。本地 UI 单测 9/9 通过、`chat.js` 语法检查通过；整套桌面 Node 测试 18/19，唯一失败是 `backend.test.cjs` 因 Agent 服务未就绪。复用 E 盘 Electron 本地启动 smoke 返回 `STATUS_BREAKPOINT`，不算通过。
- 影响范围：

| 用户场景 | 代码证据 | 改后行为 | 验证 |
|---|---|---|---|
| 主窗和页面/窗口透明度 | `desktop/desktop-visual-system.css` 的 body、topbar、page、rail、surface 背景 | 背景读取现有窗口/视图透明度变量；独立窗口不显示主窗活动栏 | `desktop/tests/ui.test.cjs` 断言 alpha 变量与 detached rail 规则；需 Windows 实窗验收 |
| 展开/收起导航与窄窗右栏 | 同文件 `.navigation`、page/log/footer inset、rail media query、`#life-view` inset | 展开导航给内容留出空间；窄窗导航收为图标、活动栏不挤压页面 | UI 回归断言；各窗口尺寸实操待 Windows 验收 |
| 日历每日待办标记 | `desktop/chat.js` `lifeCalendar()` | 每日重复项勾完成后仍与当天清单一致显示日历标记 | 已加入 UI 静态回归断言；待最新 Windows CI smoke |
| 审批弹窗圆角 | `desktop/screen-pop.css` `.popup` | 标题栏和操作栏按弹窗圆角裁切 | UI 样式断言；真实窗口边缘验收待 Windows |
| 复核材料 | `docs/tickets/237-preview-manifest.md` | 清单可随仓库打开，区分已看预览与待原生验收 | 已移除机器专属路径；逐项核销仍待实机 |

## 验收步骤

1. 查看预览清单和截图，确认新结构、图标居中、文字清楚、筛选无套框、审批尺寸和颜色符合预期。
2. 在真实 Windows/Peter 中逐页检查 1024、1440、1920 宽度下导航收起/展开、右栏开/关、选择器和浮层。
3. 逐页拖出、移动、缩放、滚动并合并归位；检查桌宠、右键菜单、通知和审批窗。
4. 用户已确认生活记录截图预览，PR #295 已创建；用户确认不等于 Windows 原生拖动/缩放验收，后者仍需按清单逐项核销。
