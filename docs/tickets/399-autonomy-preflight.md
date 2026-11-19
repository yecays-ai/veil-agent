# 399 自主活动预检与参数容错

#398 之后还剩两类“代码能确定”的红字：

1. Docker 没启动时，每次自主仍会拿到 `run_lab_python`，调用后只能失败（最近 80 次 6 条）。
2. `jot_about_lan` 最近 4 次“参数不是合法 JSON”，模型用了 Python 风格的单引号对象或纯文本。

## 修什么

- 自主活动构建工具列表时检查 Docker；不可用就**不提供 `run_lab_python`**，这一轮不会撞红墙。聊天不受影响。
- 工具参数解析先走 JSON，再尝试 `ast.literal_eval`；`profile_append` 收到纯文本时当成 text。仍解析不了才拒绝。

## 明确不修

Docker 本身、`hold` 待选择、文件先读后写、长期任务 ID、读文件/看屏幕/花园/UIO 环境错误、3 条消息上限。这些是环境或模型行为，不假装代码能保证。

## 验收

1. Docker 关闭时自主醒来，工具清单里没有 `run_lab_python`，不再产生“Docker 未就绪”拒绝。
2. Docker 开启后工具恢复。
3. 单引号/纯文本的 `jot_about_lan` 参数不再报“不是合法 JSON”。
