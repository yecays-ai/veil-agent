# 工单 174｜第三阶段：连续自主操作与创作 + Windows 桌面图标整理

**分支：** `ticket/174-desktop-drawing-and-icons-fix`  
**状态：** 施工中 / 待验收  

## 一、目标

让 Peter 在正常聊天或自主唤醒时，能够连续使用已授权的 Windows 软件完成简单任务，根据观察结果调整行动，处理可恢复的失败，并将自主创作成果保存到 Lab。

同时，Peter 可以在自主活动中，自己产生整理 Windows 桌面的想法。
他能够观察桌面图标、规划布局、自主调整图标的视觉排列，并在整理后验证结果。
不需要 Lan 提前提出整理要求，也不需要每次整理前审批。
所有图标整理操作必须可以恢复，不改变桌面文件的实际路径、名称或内容。

## 二、核心实现

1. **桌面图标管理引擎（`desktop_icons.py`）**：
   - 提取桌面显示坐标与显示器配置（分辨率、多显示器边界、网格间距、自动排列与贴齐网格状态）。
   - **COM 多线程与 Windows 11 DirectUI 兼容**：
     - 在 Web 服务与后台工作线程中，调用 `pythoncom.CoInitialize()` 与 `pythoncom.CoUninitialize()`，保证多线程 COM 环境安全无泄漏。
     - 针对 Windows 11 无传统 `SysListView32` 句柄的特性，全面使用 Shell COM `IFolderView` 原生接口：
       - 通过 `GetCurrentInfo()` 精准获取自动排列 (`FWF_AUTOARRANGE`) 与贴齐网格状态，无需依赖 ListView 窗口样式。
       - 使用 `folder_view.SelectAndPositionItem(pidl, (x, y), 0)` 精确定位每一个桌面图标，并调用 `GetItemPosition` 回读验证物理像素坐标。
   - **严格程序安全边界**：
     - 只调整 Windows Explorer 图标视图中的视觉显示坐标。
     - 绝不调用真实文件系统的移动、复制、重命名或删除。
     - 绝不修改文件内容或快捷方式目标。
   - **坐标安全校验（`validate_layout_plan`）**：
     - 确保目标坐标完全落在可见显示器区域内，防止移出屏幕。
     - 强制执行最小间距与防重叠检查，杜绝图标重合。
     - 检测 Windows 自动排列状态，若开启则安全拒绝并提示原因，不破坏系统设置。
   - **强制快照备份与一键恢复（`DesktopIconsManager`）**：
     - 任何移动前强制自动拍摄完整布局快照，持久化记录在 `Store` 中。
     - 恢复时安全匹配图标身份，安全跳过已删除或新增的图标，恢复后重新读取并校验坐标。
     - 整理与恢复过程每步检查人类输入，Lan 移动鼠标键盘时立即中止操作并保留快照。
2. **Windows 画图与画布交互能力（`windows_canvas_action`）**：
   - 为 Peter 提供受控的真实鼠标画布交互工具 `windows_canvas_action`（角色别名 `draw_on_canvas`）：
     - `click`：在窗口客户区指定 `(x, y)` 坐标点击。
     - `drag`：在窗口客户区从 `(x, y)` 平滑拖拽至 `(to_x, to_y)`。
     - `stroke`：沿指定坐标序列 `[[x1, y1], [x2, y2], ...]`（或多组笔画 `strokes`）平滑绘制连续线条。
     - `save_to_lab`：将当前窗口画布的实际成果保存为 PNG 文件至 Lab（例如 `drawings/sun.png`）。
   - **严格安全保护**：
     - **客户区严格边界校验**：实时读取目标窗口客户区范围 `GetClientRect`，坐标必须完全在客户区内部 `(0, 0, width, height)`，严禁向窗口外部、系统托盘或其他程序发送鼠标事件。
     - **目标窗口激活与校验**：操作前确认目标窗口处于前台且属于已授权应用（如 `mspaint`）。
     - **人类优先立即中止**：每次移动与插值循环均检查 `input_monitor.changed_since(snapshot)`，一旦 Lan 触碰鼠标或键盘，立即释放鼠标按键并中止操作。
     - **按键防黏连保护**：在 `try...finally` 中绝对保证 `auto.ReleaseMouse()` 释放鼠标左键，避免任何异常导致鼠标按键处于按下状态。
     - **Lab 保存路径沙箱**：保存至 Lab 时严格校验路径无 `..` 越界，仅允许保存到 `lab/` 内部，保护 Lan 的真实桌面与个人文件。
3. **工具集成与权限控制**：
   - `desktop_icons_read`：只读读取当前桌面图标与布局指标，权限策略 `desktop_read`，纳入只读长任务工具。
   - `desktop_icons_arrange`：安全调整桌面图标坐标，权限策略 `desktop_control`。
   - `desktop_icons_restore`：从指定或最新快照恢复图标布局，权限策略 `desktop_control`。
   - `windows_canvas_action`：画布鼠标操作与 Lab 保存，权限策略 `desktop_control`。
   - 角色别名：`scout_desktop_icons`、`tidy_desktop_icons`、`restore_desktop_layout`、`draw_on_canvas`。
4. **Lan 一键恢复入口（API 与 Web 端）**：
   - `GET /api/desktop-icons/snapshots`：查看历史布局快照清单。
   - `POST /api/desktop-icons/restore`：Lan 直接还原桌面图标布局，无需 Peter 批准，不消耗模型预算。
5. **连续自主操作与 Lab 创作**：
   - 在 `autonomy.py` 中注入自主行为指引：Peter 自主醒来时可观察桌面布局并决定是否整理，或使用授权应用（如画图、记事本等）连续操作创作，遇错调整方案，最终产出保存至 Lab。

## 三、Windows 实机验收步骤

### A. 连续自主操作与创作
1. Peter 在一次正常聊天或自主唤醒中，自行启动已授权软件（如画图或记事本）。
2. 连续完成多个安全操作（绘制线条、文字编辑等），每步读取观察界面状态确认。
3. 遇到可恢复失败时（如控件未就绪），能够观察并调整重试，不盲目重复危险副作用。
4. 成功制作一份真实作品并保存到 Lab，文件规则与权限保持完整。

### B. 自主桌面整理与一键恢复
1. 故意将桌面上的几个图标打乱拖散，保持自主活动开启。
2. Peter 自主醒来，观察桌面后自主决定是否整理。
3. Peter 规划布局，调用 `tidy_desktop_icons` 调整图标位置，使其按类别整齐分布。
4. 整理后重新观察坐标，确认无重叠、无超出屏幕。
5. 检查桌面真实文件：路径、文件名、修改时间及快捷方式目标完全不变。
6. 打开 VEIL Web 界面或调用恢复接口，直接一键恢复整理前的布局。
7. 检查图标瞬间还原至打乱前的初始位置。
8. 整理过程中晃动鼠标，Peter 立即停止后续图标移动让位。
