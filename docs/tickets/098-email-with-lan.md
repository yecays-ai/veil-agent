# 098 Peter 和 Lan 用 QQ 邮箱聊天

## 目标

Lan 不在电脑前（比如躺在床上）时，Peter 可以给她发邮件，她在手机上直接回邮件，他收到后再用邮件回她。

## 当前状态

- Peter 只能在桌面聊天窗口里说话；Lan 不在电脑前就收不到。
- 服务常驻在 Lan 的电脑上，电脑开着时一直在跑。

## 复用

用 Python 标准库 `smtplib` 发信、`imaplib` 收信（QQ 邮箱官方支持：`smtp.qq.com:465` SSL、`imap.qq.com:993` SSL，用“授权码”登录）。回信里引用的旧邮件按常见分隔行切掉（QQ 邮箱的 `------------------ 原始邮件 ------------------`、`发件人:`，以及 `On … wrote:`、`>` 引用行）。

## 改动

1. 配置：本机 `.env` 放 `PETER_EMAIL_ADDRESS`（Peter 用来发信的 QQ 邮箱）、`PETER_EMAIL_AUTH_CODE`（授权码）、`LAN_EMAIL_ADDRESS`（Lan 收信的邮箱）。角色配置 `[email] enabled = true`、`poll_seconds = 60`、`daily_max_sent = 20`。没配齐就没有这个功能，也不报错。
2. 主动发邮件：`send_message` 多一个可选参数 `email: true`。这条消息照常显示在桌面、记进聊天记录，同时发邮件给 Lan；同一轮里标了 email 的几条合成一封。收件人由程序固定为 `LAN_EMAIL_ADDRESS`，模型不能填收件人。程序硬上限每天 `daily_max_sent` 封。
3. 收信：服务每 `poll_seconds` 秒收一次未读邮件，只处理发件人恰好是 `LAN_EMAIL_ADDRESS` 的，其他一律忽略、不标已读。去掉引用的旧内容后，作为 Lan 的一条聊天消息走正常聊天流程，花聊天预算。Peter 正忙时这封先不标已读，下次再收。收过的邮件编号记在 `data/<角色>/email-log.jsonl`，不会重复交给他。
4. 交给模型时，这条消息前面加一行事实说明：`[Lan sent this by email; she is away from the computer.]`（只说事实，不提示他怎么回）。
5. 回复：对邮件来的消息，Peter 这一轮说的话（普通回复和 send_message 气泡）合成一封，作为那封邮件的回信发回（`In-Reply-To`），同时显示在桌面。
6. 平时主动找她，用桌面消息还是邮件，由他自己决定。
7. 失败（网络、授权码错）只在桌面显示一行中文原因，同样的原因不重复显示；收信失败下次照常再收。
8. 一次性英文能力通知写进 `capabilities.json`。

## 费用

收发邮件不花钱；邮件对话按普通聊天 token 计算。

## 验收（Lan 的体验）

1. 按说明在 QQ 邮箱设置里开启 IMAP/SMTP，拿到授权码，填进 `.env`，重启。
2. 在桌面跟他说“我去床上了，用邮件跟我说”，手机 QQ 邮箱收到他的邮件并弹通知。
3. 在手机上直接回复这封邮件，一两分钟内收到他的回信；回到电脑前，桌面聊天里能看到这段邮件对话。
4. 用别的邮箱给 Peter 的邮箱发信，他不会收到也不会回。
