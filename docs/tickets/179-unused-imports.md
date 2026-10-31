# 工单 179：清理无用 import

## 目标

- 用 pyflakes 扫生产代码，只删确认没人用、也没被其他模块或测试按名字引用的 import；不改变行为。
- 这是仓库整理期的第二项。

## 做了什么

- 删除 13 个文件中的无用 import：`autonomy.py`（`AUTONOMY_WAKE`、`GLANCE_WAKE`）、`desktop_files.py`、`desktop_icons.py`（`json`、`field`、`Path`）、`lab_preview.py`（`json`、`time`）、`lab_sandbox.py`（`PureWindowsPath`、`Set`）、`long_tasks.py`、`ombre_host.py`、`screen_pop.py`（`ctypes`）、`web_agent.py`（`ATTACHMENT_TYPES`）、`windows_discovery.py`（`fnmatch`、`glob`、`Path`）、`windows_interaction.py`（`hashlib`、`DesktopIconsUnavailable`）、`windows_observer.py`（`ctypes`）。
- `veil_agent.py`：删除 `append_tool_result` 里没有被赋值的 `nonlocal history_count`。

## 有意没动

- 测试文件里的无用 import（只影响测试，没有收益）。
- `screen_pop.py` 里放在 try 内用来探测 Windows 后端的 `ObservationError` 导入、`ombre_entry.py` 里的 `replay` 重定义、`desktop_icons.py` 的 `IID_IFolderView` 常量（用作 COM 标识说明）。
- 配置项与文档是否脱节：只做了环境变量对照，只有 `VEIL_OMBRE_STOP_PATH`（内部使用）没写进文档；文档内容与代码行为的逐项核对还没做，留给后续。
- `desktop/chat.js`：按函数是否被引用粗查，没有发现未使用函数；其他 JS 文件没查。

## 验收

- 没有行为变化，不需要专门验收。自动测试：全套与修改前失败项一致（均为沙箱环境限制，CI 为准）。
