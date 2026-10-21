# 工单 045：统一 Ombre 后台调用预算边界

## 目标

Peter 的记忆保存、检索和后台整理，在共享日/月预算不足时应在 Ombre 付费操作前停止，且请求失败或进程重启后账本不丢失预留状态。

## 当前状态

- 基线：GitHub main `29f2379ae7aace048001991d75628deb2e038fda`（PR #81 / 044 合并提交；保留其 `memory_agent.py` 与失败诊断修复），已包含 PR #81（044）；独立分支 `ticket/045-ombre-budget`。
- 已查复用：LiteLLM（MIT，约 61.7k stars；截至 2026-10-21 最新 main 提交 `3f93350411ddb50b1beb413665df4d0eb5a065de`）在 `litellm/proxy/spend_tracking/budget_reservation.py` 与 `budget_reservation_release_middleware.py` 按请求预留、结算并防止无 owner 的预留残留。它是大型网关，不适合引入本地单机 VEIL；只借鉴明确的预留/结算/恢复生命周期，继续使用现有共享 `Budget`，未复制代码。参考固定源码：[reservation reconciliation](https://github.com/BerriAI/litellm/blob/9d8fe1ddb51c0cb4c3190aa51274fbdde98eb05f/litellm/proxy/spend_tracking/budget_reservation.py)、[release middleware](https://github.com/BerriAI/litellm/blob/9d8fe1ddb51c0cb4c3190aa51274fbdde98eb05f/litellm/proxy/middleware/budget_reservation_release_middleware.py)。
- 固定 Ombre 版本 `115d831d979bf67efc7150f976e2cb34c333c935`（仓库 README 声明 MIT）源码核对：[dehydrator.py](https://github.com/P0luz/Ombre-Brain/blob/115d831d979bf67efc7150f976e2cb34c333c935/src/dehydrator.py)、[hold/core.py](https://github.com/P0luz/Ombre-Brain/blob/115d831d979bf67efc7150f976e2cb34c333c935/src/tools/hold/core.py)、[_common.py](https://github.com/P0luz/Ombre-Brain/blob/115d831d979bf67efc7150f976e2cb34c333c935/src/tools/_common.py)、[embedding outbox](https://github.com/P0luz/Ombre-Brain/blob/115d831d979bf67efc7150f976e2cb34c333c935/src/ombrebrain/storage/embedding_outbox.py)。

| 付费路径 | 当前约束 | 本单结论 |
|---|---|---|
| `Memory.call`: `hold`、`breath_search`、`feel`、`dream`、`pulse`、`anchor`、`release`、`letter_read`、`letter_write`、正文/语义 `trace`、有内容的 `plan` | 调用前向共享账本预留 embedding 估算，按 12 × 最大估算输入；只有 `hold` 且 `ombre_model=true` 时预留模型估算 | 已修正 hold 模型预留：固定 Ombre 的 `analyze` 输出上限为 4096、同事件判断为 1024，瞬时失败最多 3 次尝试；费用仍为聚合估算，不是逐次实际用量或硬闸门。部分 Ombre 路径在 VEIL 可见入口里未证明一定发出请求，保守预留只用于避免已识别 embedding 路径漏账 |
| Ombre `hold` 后的后台计划完成判断 | 子进程 `asyncio.create_task(check_plan_resolution)`；向量检索后可能对最多 20 个候选调用模型 | 未进入 VEIL 共享账本预留；无法通过 `Memory.call` 成功返回后的预留证明这些延迟调用仍受限 |
| Ombre 后台重复候选检测 | `hold` 新建后 fire-and-forget，使用向量服务 | 单次 `hold` 的 embedding 估算尝试覆盖范围有限；逐请求未独立进入共享账本 |
| Ombre embedding 持久 outbox 重试 | 上游可无限退避重试；VEIL 生成的 Ombre 配置明确 `background_indexing=false` | 当前正常配置下不启用；若 Ombre 配置被外部更改或未来版本路径改变，不能宣称受控 |
| 自然衰减 | Ombre 周期性本地评分、归档，不是模型调用 | 本身不产生模型请求；由衰减触发的后台索引若开启仍会进入上游 embedding outbox |
| Extract 模式四轮后台补记 | VEIL 工作线程在调用模型前走共享 `Budget.reserve`，成功按接口 usage 结算 | 失败与重启遗留预留改为按预留上限保守结算；失败不会重复重试导致新的未记账调用 |
| `memory_catalog`、`memory_read`、`memory_history` 及 `/memory` 元数据读取 | 本地文件/账本读取 | 不触发付费请求 |
| Ombre 本机 Dashboard / 其他上游 HTTP 维护入口 | `ombre_entry.py` 只封锁配置、更新、隧道和本地模型安装等路由；其余上游入口仍由 Ombre 自行处理 | 未证明所有可产生向量/模型调用的 HTTP 路由都经过共享账本；保持未受硬控状态 |

已实现的账本修正：Ombre 预留在调用失败后以估算上限结算；后续预检拒绝时只释放确定未发送的预留；成功/失败结算幂等；重启时将未结算 Ombre 估算转成同额保守支出，不误释放，也不永久挂在 pending。Extract 后台模型失败同样结算预留上限。**这些修改改善 VEIL 可见的账本生命周期，但未给 Ombre 子进程的每个上游请求加共享预算硬闸门。**

## 验收步骤

1. 以假模型和虚构角色跑自动回归：预算充足的 hold/召回能完成并生成 reserve/settle；预算不足在调用后端前拒绝；模拟 provider 失败不会遗留 pending。
2. 用带未结算 Ombre 预留的虚构账本启动新 `Memory`，确认重启恢复后预留按保守金额结算一次，重复恢复不增加账目。
3. Windows 真实体验另行验收；在逐请求上游硬闸门和 Dashboard 绕行路径解决前，不把场景 C/D 标记为通过，也不把本单描述为已实现完整硬性费用限制。

