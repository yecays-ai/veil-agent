# 目标

让 VEIL 核心模块从低层共享模块取得基础错误类型与运行哨兵，移除它们为引用 `veil_agent` 而形成的循环依赖，同时保持现有聊天、记忆、自主活动和工具行为不变。

# 当前状态

`veil_agent.py` 定义了 `UserError` 异常层级、`now()` 和 `AUTONOMY_WAKE`；autonomy、long_tasks、memory、memory_native 与 tools 模块会反向导入这些定义，而 `veil_agent` 又导入这些模块，形成核心导入环。已确认纯本地类型/错误模块足以承接共享定义，不需要引入外部库或新依赖。修改涉及 `agent_types.py` 与相关核心模块。

# 验收步骤

1. 更新并启动 VEIL，确认桌面正常启动，发送普通消息后 Peter 能正常回复。
2. 使用一次文件读取和一次记忆查询，确认工具与记忆入口仍可用；没有新增配置步骤。

