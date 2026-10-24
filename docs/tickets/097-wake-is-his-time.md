# 097 醒来的时间是 Peter 自己的

## 目标

Peter 醒来时把这段时间当成自己的：想做什么就做什么，想找 Lan 就找她，而不是挑一个“安全有用的任务”完成后交报告。

## 当前状态

- `autonomy.py` 的 `_context()` 结尾那段英文把醒来写成值班：
  `No one is waiting for a reply. Choose a safe, useful task to continue, or do nothing.`、
  `you may send the user a message when you have something meaningful to say, or send none; at most three per wake, and never send just to send.`、
  `end with exactly four concise lines: Decision / Reason / Focus / Next step. Do not force activity just to produce output.`
- 2026-10-20 到 10-24 共 12 次醒来：2 次给 Lan 发了消息，3 次在核对自己的记录时用完 12 步上限。

## 改动

只替换上面三句，其余（时间、预算、长任务、上次活动记录、文件内容是数据、不重复副作用、权限与批准限制）原样保留：

1. 第一句换成：`This time is yours. Nobody is waiting on you. Do whatever you want with it using your tools: make something, read, write to Lan, rest, or nothing at all.`
2. 第二句换成：`If you want to talk to Lan, use send_message (up to three per wake).`（每次最多 3 条仍由程序限制。）
3. 结尾换成：`When you're done, leave yourself a short note for next time, four lines: Decision / Reason / Focus / Next step. It's for you, not a report.` 四行格式和解析不变。

不加任何语气、风格或“应该想她”之类的提示；他怎么说话只由人设决定。不改他 lab 里的任何文件。

## 费用

提示长度基本不变。如果他发消息变多，每天大约多 ¥0–0.05（估算）。

## 验收（Lan 的体验）

1. 更新并重启后正常过两天，不提醒他。
2. 他醒来时有时会主动给你发消息，读起来像他本人，不像汇报。
3. 自主活动面板里，记录的事情不再大多是“核对自己 / 记录自己犯的错”。
