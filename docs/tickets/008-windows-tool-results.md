# 工单 008：Windows 文件工具结果长度误拒绝

## 目标

lan 能在 Windows 对话中让 Peter 列出授权工作区文件并读取 `test.txt`，现有文件权限和费用上限继续生效。

## 当前状态

006 已合并；真实 DeepSeek 对话中 `list_directory`、`read_text_file`、`get_file_info` 在执行前均因结果长度预估被拒绝。`veil_agent.py` 的工具循环按每个结果都达到 `max_result_bytes` 预估，并重复计入工具调用记录；实际结果尚未读取。继续复用官方 filesystem MCP server，修复应用层请求长度计算。

## 验收步骤

1. 在 Windows 的 `data/peter/workspace/` 放假数据 `test.txt`，让 Peter 列出工作区文件、读取它并准确复述内容。
2. 让 Peter 尝试读取工作区外文件，确认程序拒绝；让他写新文件，确认仍须你同意且不能覆盖同名文件。
