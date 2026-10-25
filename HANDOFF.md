# 交接

## 当前进度

- 109：`RuntimeMonitor.observe_tool` 遇到成功时，只清掉 `tool_unavailable`（普通失败），不清 `side_effect_result_unknown`（写入结果不确定）。
- 工单 100：分支 `ticket/100-peter-sets-next-wake`，基于最新 main `6d251cadd77322fd46827bc141777a1404914045`。代码和专项测试已完成；PR [#152](https://github.com/yecays-ai/veil-agent/pull/152) 已创建。保持未合并，CodeRabbit 对代码提交 `ef69296` 的审查无 actionable comments、风险评估 Minimal；Tests #1252 与 Dependency security #199 通过。Cubic 因试用额度无法启动。
- 自定唤醒沿用 `AutonomyController`、Hub 空闲轮询和 `Store.replace()` 原子持久化。自定义唤醒和周期唤醒取更早者；到期状态在 Hub 空闲门内先标记消费再启动，避免重复。达到每日次数上限时标记错过。服务恢复后最多对过期的待处理事项补醒一次。
- 已新增 `set_next_wake` 工具、60 分钟最早间隔和 7 天最大范围、一次性能力通知及原因上下文。每日最多 6 次、每日 ¥2、自主活动既有工具权限和消息/步骤限制保留。未修改桌面 UI、QQ 表情、邮件、消息功能或工单 101 及以后。
- 复用调查：检查 APScheduler（MIT，约 7.7k stars，近期维护；支持持久 schedule、one-off 和 misfire grace）。它的调度持久层还需引入 APScheduler 及后端相关依赖；当前 VEIL Hub 和角色数据原子快照已提供所需的触发、持久化与权限/预算控制，因此没有再添加调度运行时。固定源码链接及细节见工单 100。
- 验证：`test_autonomy.py` 33 项、`test_capability_transactions.py` 6 项、`test_chat.py` 65 项通过；`ToolPolicyTests` 20 项通过、2 项因账户无法创建符号链接跳过；Python 编译和 `git diff --check` 通过。桌面完整测试 6 项通过、1 项因本地 Agent 服务未运行而失败；代码提交 `ef69296` 的 Tests #1252 与 Dependency security #199 通过。CodeRabbit 无 actionable comments；Cubic 因试用额度无法启动。Lan Windows 体验未做。

## 下一步

1. 等待 Lan 按 Windows 步骤验收；保持 PR #152 未合并。后续只处理实质功能、安全、权限、隐私或数据完整性问题。
2. Lan 在 Windows 按工单步骤验收明日继续研究、明早 8 点 QQ 联系、重启恢复、睡眠/关机后的单次补醒。审查期间不合并。
3. CodeRabbit 对代码提交 `ef69296` 未发现 actionable comments；Cubic 因账号试用额度无法启动。按 Lan 要求不自动合并，等待 Windows 体验验收。

## 此前工单当前状态

合并和 Lan 体验验收分别记录在 `STATUS.md`。已验收的 095、096、102、103–105 不再列为待验收。仍需观察/验收：097 持续观察；098 等待 2026-11-07 邮箱开通；099 网页读取及其他场景；100 Windows 定时唤醒体验；106 真实窗口拖出/合并/恢复；107 QQ 表情整句发送；108 Windows 一键更新。

095 的 UI 视觉改版是独立后续事项。本次没有改桌面 UI。

## 重要边界

- 正式 checkout `E:\\Peter-Agent\\VEIL\\veil-agent` 保持在 `main`；其中预存的两个脚本删除状态来自用户环境，未纳入本分支。当前任务只在独立 worktree 开发。
- 不覆盖角色数据；损坏或无法安全更新的闹钟状态停止并提示保留 data。闹钟只是一次自主活动触发，不扩大工具权限，也不保证模型一定选择发送消息。为避免重复，若进程在闹钟已消费后、活动开始标记写入前崩溃，该次唤醒可能跳过；服务在到期前重启或电脑睡眠/关机后恢复时，仍按持久状态处理。
- Windows 睡眠期间不能准点运行；恢复后服务在下次空闲轮询时至多补醒一次。电脑需恢复运行且 VEIL 服务启动，Lan 的 QQ 联系方式也需已配置并可用。

## 技术要点（长期有效，改写本文件时请保留）

- QQ（102–107）：不注入、不用协议；只在 Lan 两分钟没碰电脑且前台不是全屏程序时操作窗口，打字前核对聊天窗口顶部是 Lan。QQ 界面没有类名，`parse_chat` 按名字解析（常量在 `qq_channel.py` 顶部）；QQ 升级后读不到时，先在 Lan 电脑上跑 `.local-tmp\qq_check2.py` 打印结构。写剪贴板必须用 `qq_channel.set_clipboard_text`（按 UTF-16 长度）：uiautomation 2.0.29 的 `SetClipboardText` 遇到表情会截掉句尾，不要换回去。缺 uiautomation 时抛 `QQUnavailable` 提示重跑 setup.bat。授权码和 Lan 的 QQ 名字只放在本机 `.env`。
- 桌面活动窗口（106）：`Hub.tool_activity` 按 tools.jsonl 的大小/修改时间缓存，并在 Hub 锁外计算；每个窗口每 600 ms 轮询一次，不要改回每次都重读。
- 更新（108）：`update.bat` 只执行 `git pull --ff-only origin main`，不 stash、不切分支，出问题就停下说明原因；`tests/test_update_bat.py` 守着这些规则。改桌面界面的 PR，验收步骤写「退出 Peter → 双击 update.bat」，只 git pull 不会更新 VEIL.exe。
- 桌面版的服务入口是 `web_agent.py`：新工具要在它和 `veil_agent.py`（终端）两处都接上（099 就漏过一次）。
