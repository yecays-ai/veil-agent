# 交接

## 当前进度

- 097 已合并（#143），098 邮件已合并（#142，邮箱 11-07 后才能验收）。
- 102 QQ 由 Claude 在分支 `ticket/102-qq-with-lan` 实现：`away_bridge.py`（通用 `AwayBridge`：渠道消息进同一段聊天、本轮回复走原渠道、`send_message(<渠道>=true)` 主动发），`email_channel.py` 改为实现这个接口，`qq_channel.py`（`QQChannel` + `QQWindow`，uiautomation，Windows 才启用）。
- 新依赖 `uiautomation==2.0.29`（及 `comtypes==1.4.17`），只在 Windows 安装。
- 测试：`tests/test_email_channel.py`、`tests/test_qq_channel.py` 通过（QQ 窗口用假对象）。真实 QQ 窗口操作只能在 Lan 的 Windows 上验收。

## 下一个窗口第一步

102 已合并（#144）。103：`QQWindow._auto` 缺 uiautomation 时抛 `QQUnavailable`（提示重跑 setup.bat），`AwayBridge` 两个线程捕获意外异常并报一次。Lan 的电脑第一次没重跑 setup.bat 就踩到了这个。105：发送前核对用 `comparable()`（去掉表情、空白），因为 QQ 把表情转成图片读不回文字。104：QQ 界面元素没有类名，`parse_chat` 按名字解析（常量在 `qq_channel.py` 顶部）。若 QQ 升级后读不到，先在 Lan 电脑上跑 `.local-tmp\qq_check2.py` 打印当前结构（本地诊断脚本，不在仓库里）。101 做完后，邮件和 QQ 回复要带上附件。

## 边界

- 不注入 QQ、不用协议；只在 Lan 两分钟没碰电脑且前台不是全屏程序时操作窗口；打字前核对收件人。
- 授权码和 Lan 的 QQ 名字只在本机 `.env`。
