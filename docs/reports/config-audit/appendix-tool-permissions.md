## 附录 A：工具权限矩阵（73 个工具）

> 生成方式：从 tools_agent.py 的 ALLOW/ASK/DENY/OWN_WORK/LAB_ONLY/NATIVE/AUTONOMY_DIRECT 与 tool_groups.py 的 ALWAYS_ON/GROUP_TOOL_NAMES 静态解析；权限判定顺序同 tool_policy()（tools_agent.py:92-109）。
> 说明：MCP 工具是动态的 mcp__<服务>__<工具>，权限由服务级 ask/direct 决定（mcp-services.json），不在本表逐一列出；服务发现见报告 §4.5。

| 权限档 | 工具数 |
| --- | ---: |
| 直接可用（只读/公开）（allow） | 16 |
| 需要审批（ask） | 3 |
| 不提供（deny） | 1 |
| 工作区内直接可用（workspace） | 5 |
| Lab 内直接可用（lab） | 3 |
| 桌面只读（desktop_read） | 5 |
| 桌面文件只读（desktop_file_read） | 4 |
| 桌面操作（保存/发送等仍需批）（desktop_control） | 6 |
| 在桌面创建/清理自己的文件（desktop_create） | 2 |
| 记忆工具（记忆工具（memory_native 校验）） | 7 |
| 聊天/长任务/维护等 Chat 级工具（Chat 级工具（Chat 注册/校验）） | 21 |

合计 73 个。

| 工具 | 权限档 | 分组 | 常驻 |
| --- | --- | --- | --- |
| browser | allow | web | 否 |
| directory_tree | allow | 常驻 | 是 |
| get_file_info | allow | 常驻 | 是 |
| github_read | allow | github | 否 |
| list_directory | allow | 常驻 | 是 |
| list_directory_with_sizes | allow | 常驻 | 是 |
| read_multiple_files | allow | 常驻 | 是 |
| read_text_file | allow | 常驻 | 是 |
| screen_pop | allow | 常驻 | 是 |
| search_files | allow | 常驻 | 是 |
| set_next_wake | allow | 常驻 | 是 |
| set_wake_rhythm | allow | 常驻 | 是 |
| web_fetch | allow | web | 否 |
| web_research | allow | web | 否 |
| web_search | allow | web | 否 |
| xiaohongshu | allow | xiaohongshu | 否 |
| append_file | ask | 常驻 | 是 |
| grant_append | ask | 常驻 | 是 |
| write_file | ask | 常驻 | 是 |
| move_file | deny | —（不提供） | 否 |
| create_directory | workspace | 常驻 | 是 |
| delete_file | workspace | 常驻 | 是 |
| edit_file | workspace | 常驻 | 是 |
| lab_file_digest | workspace | 常驻 | 是 |
| move_lab_file | workspace | 常驻 | 是 |
| open_lab_game | lab | 常驻 | 是 |
| preview_lab_page | lab | 常驻 | 是 |
| run_lab_python | lab | 常驻 | 是 |
| desktop_icons_read | desktop_read | desktop_files | 否 |
| windows_discover_apps | desktop_read | 常驻 | 是 |
| windows_foreground | desktop_read | 常驻 | 是 |
| windows_list | desktop_read | 常驻 | 是 |
| windows_observe | desktop_read | 常驻 | 是 |
| desktop_find_file | desktop_file_read | desktop_files | 否 |
| desktop_list | desktop_file_read | desktop_files | 否 |
| desktop_read_image | desktop_file_read | desktop_files | 否 |
| desktop_read_text_file | desktop_file_read | desktop_files | 否 |
| desktop_icons_arrange | desktop_control | desktop_control | 否 |
| desktop_icons_restore | desktop_control | desktop_control | 否 |
| windows_canvas_action | desktop_control | desktop_control | 否 |
| windows_control_action | desktop_control | desktop_control | 否 |
| windows_launch | desktop_control | desktop_control | 否 |
| windows_manage | desktop_control | desktop_control | 否 |
| desktop_create_file | desktop_create | desktop_files | 否 |
| desktop_move_to_trash | desktop_create | desktop_files | 否 |
| breath_search | 记忆工具（memory_native 校验） | 常驻 | 是 |
| hold | 记忆工具（memory_native 校验） | 常驻 | 是 |
| memory_catalog | 记忆工具（memory_native 校验） | memory_admin | 否 |
| memory_history | 记忆工具（memory_native 校验） | memory_admin | 否 |
| memory_read | 记忆工具（memory_native 校验） | 常驻 | 是 |
| memory_timeline | 记忆工具（memory_native 校验） | memory_admin | 否 |
| trace | 记忆工具（memory_native 校验） | memory_admin | 否 |
| autonomy_history | Chat 级工具（Chat 注册/校验） | 常驻 | 是 |
| capability_history | Chat 级工具（Chat 注册/校验） | 常驻 | 是 |
| card_action | Chat 级工具（Chat 注册/校验） | 常驻 | 是 |
| delegate | Chat 级工具（Chat 注册/校验） | build | 否 |
| expand_capability | Chat 级工具（Chat 注册/校验） | 常驻 | 是 |
| life_record | Chat 级工具（Chat 注册/校验） | 常驻 | 是 |
| long_task_status | Chat 级工具（Chat 注册/校验） | 常驻 | 是 |
| long_task_update | Chat 级工具（Chat 注册/校验） | 常驻 | 是 |
| maintenance | Chat 级工具（Chat 注册/校验） | build | 否 |
| mcp_status | Chat 级工具（Chat 注册/校验） | 常驻 | 是 |
| ongoing_update | Chat 级工具（Chat 注册/校验） | 常驻 | 是 |
| profile_append | Chat 级工具（Chat 注册/校验） | 常驻 | 是 |
| profile_replace | Chat 级工具（Chat 注册/校验） | 常驻 | 是 |
| recall_message | Chat 级工具（Chat 注册/校验） | 常驻 | 是 |
| reply_later | Chat 级工具（Chat 注册/校验） | 常驻 | 是 |
| search_chat_history | Chat 级工具（Chat 注册/校验） | 常驻 | 是 |
| send_message | Chat 级工具（Chat 注册/校验） | 常驻 | 是 |
| stay_silent | Chat 级工具（Chat 注册/校验） | 常驻 | 是 |
| stickers | Chat 级工具（Chat 注册/校验） | 常驻 | 是 |
| thought | Chat 级工具（Chat 注册/校验） | 常驻 | 是 |
| workbench | Chat 级工具（Chat 注册/校验） | build | 否 |

> 注：deny 的工具不会出现在请求里；ask 的工具每次调用走审批卡；workspace/lab/desktop_* 的边界由程序路径校验与白名单保证，不靠提示词。
