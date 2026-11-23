# 工单 433 · P1 试运行：Peter 自动召回切到 Brain

## 背景

#430（`f2f68c7`）已合并，Brain 检索代码在正式 main。Lan 决定不再等七天影子观察结束，先让 Peter
在真实聊天里试用 Brain 检索。影子同步继续对账，正式写入仍由 Ombre 负责；不提前下线 Ombre。

## 目标

- 把 Peter 的 `[memory.retrieval] backend` 从 `ombre` 改为 `brain`，自动召回走 Brain；判官、去重、
  冷却、预算、事件层和正式写入链不变。
- 切换前确认：Brain 已同步到最新记忆、embedding 预算走 `estimate/settle_estimate`、brain 失败自动
  回退 Ombre。
- 保留手动回退：把这一行改回 `backend = "ombre"` 并重启，不需要改数据。
- 盲测结果和评估局限照实保留，不宣称 Brain 全面优于 Ombre。

## 回退

出现明显漏召回、错误召回、异常费用或聊天故障时，立刻把 `backend` 改回 `ombre` 并重启；影子库
和 Ombre 都不需要动。

## 验收（Lan 真实使用）

1. 真实聊天里问几件旧事，能找回正确旧事，不出现明显乱召回。
2. 召回延迟和费用没有明显增加；预算记录正常结算。
3. Brain 检索失败时自动回退 Ombre，聊天不中断。
4. 改回 `backend = "ombre"` 重启后恢复原检索。
