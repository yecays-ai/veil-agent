# 工单 145：工具说明瘦身

Lan 2026-10-28 提出：「上下文」页里工具定义占了一半以上。Claude 实现。

## 为什么

每次请求都带全部工具定义。改之前在 Lan 电脑上每次 46KB，DeepSeek 聊天请求平均约 1.76 万 token，工具定义接近六成。最大的是 Ombre 自带的中文说明（`trace` 一个 8.3KB），里面多是 Peter 用不到的内部细节，还有 VEIL 会拒绝的参数（硬删除、删除审批）。

## 改了什么

能力一个不删，只改说明文字：
- Ombre 九个工具（trace、dream、信件三个、anchor、release、plan、pulse）：保留 Ombre 的参数，说明改成简短英文，写的是 VEIL 里实际能做的；`trace` 去掉 VEIL 本来就拒绝的 `hard_delete`、`deletion_request_id`、`deletion_decision`、`deletion_ai_reason` 参数；去掉参数里的 `title` 字段。`dream` 不再提 VEIL 没有的 `hold(feel=True)`。
- 文件工具（filesystem 服务的 8 个）：说明改短，并写清 lab 里直接写、改自己的文件会留旧版本、lab 外问 Lan；去掉 `$schema`。
- `long_task_update`、Windows 观察/交互 6 个工具：说明改短，去掉程序内部检查的描述（检查照旧由程序做）。
- 6 个写入类工具后面挂的「输出预算」一句改短。

测量（Linux 本机，无 Windows 工具）：35.5KB → 25.1KB；Windows 工具 7.3KB → 5.3KB。估计 Lan 电脑上 46KB → 约 34KB，每次约少 3.5–4k token（未验证，看「上下文」页）。不加花费。

## 验收（Lan）

1. 退出 Peter → update.bat。
2. 打开「上下文」页，看工具定义的占比是否明显下降（约 34KB）。
3. 正常聊几句，让他存一条记忆、改一条记忆的标签或标题、在 lab 里写个东西，确认他照常会用这些工具。
