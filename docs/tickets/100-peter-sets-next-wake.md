# 100 Peter 自己定下次几点醒

## 目标

Peter 能安排一次未来的自主唤醒，用于继续研究或按 Lan 的要求提醒并联系她；重启或错过时间后最多处理一次，同时保留原周期活动和现有安全、预算边界。

## 当前状态

- 最新基线为 `main` 的 `6d251cadd77322fd46827bc141777a1404914045`。`AutonomyController` 已按既有周期机制计算唤醒，`Hub` 在空闲轮询中启动自主活动；`Store.replace()` 提供原子 JSON 状态替换。实现复用这三处现有机制，不另起调度线程或重复持久化层。
- 新增直接工具 `set_next_wake(at, reason)`，同一时间只保留一个闹钟，可覆盖或取消。时间接受本地 ISO 格式，至少提前配置的 60 分钟、最多 7 天。到期闹钟与正常周期唤醒取较早时间；正常周期频率不变。闹钟在 Hub 空闲门内持久标记为已消费后才启动一次自主活动，理由作为数据加入上下文；每日最多 6 次、每日 ¥2、单次既有预算/步骤/消息限制均继续生效。达当日次数上限时记为错过，重启或恢复运行只做一次过期补醒，不重复执行。
- 闹钟保存在 `data/<角色>/autonomy-alarm.json`，服务重启后读取；能力更新通过现有 `capabilities.json` 仅通知一次。计划时间及理由也写入现有工具活动账本和状态 API。桌面 UI 文件未改。为避免重复，若进程恰在闹钟已消费后、活动开始标记写入前崩溃，该次唤醒可能跳过；电脑睡眠/关机导致时间经过时，待处理闹钟会在恢复后最多补醒一次。
- 复用调查：检查 APScheduler；调查时 GitHub 显示约 7.7k stars、MIT 许可，master 最新提交为 2026-10-12。固定版本的 [`AsyncScheduler.add_schedule()` 源码](https://github.com/agronholm/apscheduler/blob/bdaceb12792f104f25798dee2850d45f9e7d83f8/src/apscheduler/_schedulers/async_.py)与[依赖清单](https://github.com/agronholm/apscheduler/blob/bdaceb12792f104f25798dee2850d45f9e7d83f8/pyproject.toml)确认其支持一次性调度、持久数据存储与 misfire grace；Python 3.11+ 的基础依赖包括 AnyIO、attrs、tenacity、tzlocal，持久存储还需对应后端。这里复用 VEIL 已运行的 Hub 触发、原子状态存储及权限/预算门控，不增加第二套 scheduler 或存储依赖；没有复制 APScheduler 代码。
- 本地验证：Autonomy 33 项、能力通知事务 6 项、聊天 65 项通过；工具权限专项 20 项通过、2 项因当前账户无法创建符号链接而跳过；Python 编译和 `git diff --check` 通过。桌面完整测试 6 项通过、1 项因本地 Agent 服务未运行而失败，未据此判断桌面集成通过。代码提交 `ef69296` 的 PR CI 已通过；Lan 的 Windows 体验仍待完成。
- 当前分支 `ticket/100-peter-sets-next-wake`，PR [#152](https://github.com/yecays-ai/veil-agent/pull/152)。该 PR 同步维护 `STATUS.md`、`HANDOFF.md`；代码完成；CodeRabbit 对提交 `ef69296` 无 actionable comments、风险评估 Minimal；该提交 Tests #1252 与 Dependency security #199 通过。Cubic 因试用审查额度用完未能启动。Lan 实际体验验收待进行。

## 验收步骤

1. 更新并重启 Peter，告诉他“明天下午 3 点继续研究蜘蛛丝强度”，确认他答复安排成功；在活动记录里查看包含时间和理由的 `set_next_wake` 操作。
2. 告诉 Peter“明早 8 点叫我”。保持电脑和 VEIL 服务运行，并确认 QQ 联系方式可用；约定时刻确认 Peter 只联系 Lan 一次。另在到期前重启 VEIL，确认计划仍在。
3. 再安排一次未来时间，在电脑睡眠或关机期间让该时间经过；恢复并启动 VEIL 后确认最多补醒一次、没有重复消息或重复自主活动记录。
