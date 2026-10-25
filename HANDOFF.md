# 交接

## 当前进度

- 工单 101「Peter 把自己做的东西发给 Lan」：分支 `ticket/101-send-lab-files` 基于 PR #153 合并后的 main `006a4b7`；PR #154 打开且未合并。Lan 已确认本地预览。CodeRabbit 审查提交 `4a1f251` 后指出两项实质问题：邮件回复可能累积超过 3 个附件，以及 UI 删除/撤回未清理附件；两项均已在 `245bd84` 修复，评审线程已解决。`245bd84` 的 Tests #1266、Dependency security #205、Windows desktop #106 全部通过。Cubic 因试用额度耗尽未能启动；CodeRabbit 本小时审查额度已用完，因此未对最终修复提交再跑自动审查。按 Lan 要求不自动合并。Lab 附件限制为普通、未链接、允许类型且不超过 5 MiB；聊天保存独立快照和索引，重启后历史可恢复；桌面卡片支持图片预览、纯文本查看和用户点击后用默认浏览器打开 HTML；邮件最多发送 3 个附件，QQ 不附文件。
- 复用现有 `Hub.media_root/index.jsonl`、认证媒体路由和 Python 标准库 `EmailMessage.add_attachment()`，不引入第三方运行依赖。每个读取/发送边界校验文件元数据；HTML 由本机浏览器打开前按 5 MiB 流式读取；无效附件不会触发语音合成。用户权限、费用和自主消息次数限制未扩大。
- 本地原提交验证：Python 全量 484 项通过、4 项跳过；`test_web.py` 57 项通过；Electron 桌面 7 项通过。审查修复后：3 项针对性回归测试通过，`test_email_channel.py` 10 项通过；Python 编译和 `git diff --check` 通过。`test_chat.py` 全文件有 2 项因缺 `httpx2`、`dotenv` 未能通过；`test_web.py` 全文件受缺依赖及本机 Windows 回环套接字权限限制（WinError 10013），未形成完整通过结果。PR 最新提交的 Tests #1266、Dependency security #205、Windows desktop #106 通过，CI 的 Windows 3.11/3.12、Ubuntu 3.11/3.12 全量测试均成功。未运行真实模型、付费接口或真实邮箱。Lan Windows 体验未做。
- Lan 已确认本地附件卡片效果；分支已推送并创建 PR #154（https://github.com/yecays-ai/veil-agent/pull/154）。PR 当前打开，等待审查和 CI；按 Lan 要求不自动合并。
- PR #153（工单 109）已合并为 `006a4b7`；PR 提交 `8af814d` 的 Tests #1259 与 Dependency security #202 成功。它改了 `runtime_monitor.py`、对应测试、STATUS 和 HANDOFF；101 与它的运行代码文件不重叠，文档已基于合并后的状态重新整理。
- PR #152（工单 100）已合并为 `c0de006`；最新 PR 提交 `f7951d5` 的 Tests #1256 与 Dependency security #201 成功。Lan Windows 定时唤醒体验仍待验收。
- PR #149（工单 106）已合并，Tests #1237、Dependency security #193 和 Windows desktop #100 均成功；Lan 看过预览，但真实拖出、合并、重启恢复仍未验收。

## 下一步

1. PR #154 的修复和 CI 已完成；保持 PR 打开，等待 Lan 决定下一步，不自动合并。若需要第二轮自动审查，Cubic 当前额度不足，CodeRabbit 本小时额度已用完。
2. 合并后按 `STATUS.md` 中的 Windows 步骤验收聊天附件、重启恢复和邮件附件。

## 此前工单当前状态

合并和 Lan 体验验收分别记录在 `STATUS.md`。已验收的 095、096、102、103–105 不再列为待验收。仍需观察/验收：097 持续观察；098 等待 2026-11-07 邮箱开通；099 网页读取及其他场景；100 定时唤醒；101 附件体验；106 真实窗口操作；107 QQ 表情整句发送；108 Windows 一键更新；109 普通故障恢复提示。

095 的 UI 视觉设计改版是独立后续事项。本次只增加附件卡片交互，不改整体 UI 视觉设计，也不修 QQ 表情问题。

## 重要边界

- 正式 checkout `E:\Peter-Agent\VEIL\veil-agent` 保持在 `main`；其中预存的两个脚本删除状态来自用户环境，未纳入本分支。当前任务只在独立 worktree 开发。
- 不覆盖角色数据；损坏或无法安全更新的闹钟状态停止并提示保留 data。闹钟只是一次自主活动触发，不扩大工具权限，也不保证模型一定选择发送消息。为避免重复，若进程在闹钟已消费后、活动开始标记写入前崩溃，该次唤醒可能跳过；服务在到期前重启或电脑睡眠/关机后恢复时，仍按持久状态处理。
- Windows 睡眠期间不能准点运行；恢复后服务在下次空闲轮询时至多补醒一次。电脑需恢复运行且 VEIL 服务启动，Lan 的 QQ 联系方式也需已配置并可用。

## 技术要点（长期有效，改写本文件时请保留）

- QQ（102–107）：不注入、不用协议；只在 Lan 两分钟没碰电脑且前台不是全屏程序时操作窗口，打字前核对聊天窗口顶部是 Lan。QQ 界面没有类名，`parse_chat` 按名字解析（常量在 `qq_channel.py` 顶部）；QQ 升级后读不到时，先在 Lan 电脑上跑 `.local-tmp\qq_check2.py` 打印结构。写剪贴板必须用 `qq_channel.set_clipboard_text`（按 UTF-16 长度）：uiautomation 2.0.29 的 `SetClipboardText` 遇到表情会截掉句尾，不要换回去。缺 uiautomation 时抛 `QQUnavailable` 提示重跑 setup.bat。授权码和 Lan 的 QQ 名字只放在本机 `.env`。
- 桌面活动窗口（106）：`Hub.tool_activity` 按 tools.jsonl 的大小/修改时间缓存，并在 Hub 锁外计算；每个窗口每 600 ms 轮询一次，不要改回每次都重读。
- 更新（108）：`update.bat` 只执行 `git pull --ff-only origin main`，不 stash、不切分支，出问题就停下说明原因；`tests/test_update_bat.py` 守着这些规则。改桌面界面的 PR，验收步骤写「退出 Peter → 双击 update.bat」，只 git pull 不会更新 VEIL.exe。
- 桌面版的服务入口是 `web_agent.py`：新工具要在它和 `veil_agent.py`（终端）两处都接上（099 就漏过一次）。工单 101 的附件工具只在 desktop `serve_chat` 能提供安全 Lab 读取与 Hub 保存回调时显示。
