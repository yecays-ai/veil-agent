# 工单 237 预览与状态核对清单

状态含义：**已检查**仅代表对应截图中的浏览器布局；**待 Windows**代表尚未由真实 Windows/Electron 窗口逐项操作验收。没有截图或实机证据的项目不标为通过。

## 已有预览

| 状态 | 预览 | 覆盖范围 | 限制 |
|---|---|---|---|
| 已检查（用户确认） | [`life-calendar-preview.png`](../../life-calendar-preview.png) | 1440×900 生活记录总览、月历与选中日清单 | 浏览器合成预览；不证明主窗口其他页面、原生窗口拆并或浮层完整 |

## 主窗口：页面与导航

页面：聊天、自主活动、上下文、记忆、生活记录、MCP、设置。

| 状态 | 窗口宽度 | 页面/状态 | 待核项目 |
|---|---:|---|---|
| 待 Windows | 1024、1440、1920 | 上述七页各自默认内容 | 左右侧栏展开/收起、右栏显示/隐藏、页面滚动、内容边界 |
| 待 Windows | 1024、1440、1920 | 页面切换与导航 | 每个导航项选中、悬停、键盘聚焦；图标居中、点击区域和标签 |
| 待 Windows | 1024、1440、1920 | 聊天 | 消息滚动、输入区、工具菜单、审批提示、窄窗内容宽度 |

逐项确认无意外横向滚动、文字挤压/截断、图标偏位、边缘内容裁切；需要时纵向滚动可访问完整内容。

## 可拆页面：主窗和独立窗

每页都需在主窗口和拆出窗口各检查默认内容与有内容时的纵向滚动，并执行拆出、拖动、缩放、合并归位：

| 页面 | 主窗口 | 独立窗口 | 拆出/拖动/缩放/合并 |
|---|---|---|---|
| 聊天 | 待 Windows | 待 Windows | 待 Windows |
| 自主活动 | 待 Windows | 待 Windows | 待 Windows |
| 上下文 | 待 Windows | 待 Windows | 待 Windows |
| 记忆 | 待 Windows | 待 Windows | 待 Windows |
| 生活记录 | 待 Windows | 待 Windows | 待 Windows |
| MCP | 待 Windows | 待 Windows | 待 Windows |
| 设置 | 待 Windows | 待 Windows | 待 Windows |

## 桌宠、菜单和浮层

| 项目 | 默认 | 展开/选中 | 关闭/边界 |
|---|---|---|---|
| 桌宠窗口 | 待 Windows | 待 Windows | 待 Windows |
| 桌宠右键菜单 | 待 Windows | 待 Windows | 待 Windows |
| 通知提示 | 待 Windows | 待 Windows | 待 Windows |
| 审批提示/确认窗 | 待 Windows | 待 Windows | 待 Windows |
| 聊天右键菜单、消息菜单 | 待 Windows | 待 Windows | 待 Windows |
| 工具菜单、语音菜单 | 待 Windows | 待 Windows | 待 Windows |
| 工具提示 | 待 Windows | 待 Windows | 待 Windows |
| 选择 Peter 模型（`model-switch`） | 待 Windows | 待 Windows | 待 Windows |
| MCP 调用权限（`mcp-permission`） | 待 Windows | 待 Windows | 待 Windows |
| Peter 本人模型（`use-peter`） | 待 Windows | 待 Windows | 待 Windows |
| 自主活动模型（`use-autonomy`） | 待 Windows | 待 Windows | 待 Windows |
| 委派任务模型（`use-delegate`） | 待 Windows | 待 Windows | 待 Windows |
| 瞄一眼模型（`use-glance`） | 待 Windows | 待 Windows | 待 Windows |
| 记忆后台模型（`use-background`） | 待 Windows | 待 Windows | 待 Windows |
| 模型接口（`model-endpoint`） | 待 Windows | 待 Windows | 待 Windows |
| 生活记录类型/归属/重复选择器（动态表单） | 待 Windows | 待 Windows | 待 Windows |

每个项目需确认图标视觉居中且点击区域正常、文字完整、菜单及浮层不被窗口边缘裁切；下拉项可见且选中状态明确。
