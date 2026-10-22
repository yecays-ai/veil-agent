# 工单 071｜给 Peter 发图片

## 目标

Lan 在网页聊天里选一张图片（可附一句话）发给 Peter，Peter 用自己的模型真正看到这张图并回复；之后聊天记录里能看到这张图，Peter 想记住时自己用 `hold` 存。

- 做完后能做什么：现在只能打字；做完后能把照片、截图直接发给他看，他能说出图里有什么，并接着聊。
- 比“让 AI 读一个文件”多什么：图片就在聊天气泡里，是他本人当轮看到的，不用 Lan 去放文件、再让他调用工具读。
- 怎么验收：只用网页发图聊天，见下方验收步骤。

## 现状（已核实）

- Peter 当前模型 `deepseek-flash`（`characters/peter/character.toml`）。DeepSeek 官方文档 https://api-docs.deepseek.com/guides/vision ：`deepseek-flash` 直接接受图片，OpenAI 兼容格式为 user 消息 `content` 数组里的 `{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,..."}}`；只能放在 `user` 消息；JPEG/PNG/GIF/WebP；每张图最多计 1024 个输入 token，与文字同价，无额外费用。不需要换模型，也不需要“先让别的模型描述”。
- 未验证：图片与 thinking 模式、工具调用同一请求是否都正常。施工第一步先用真实 key 发一次“图片 + thinking 开 + 带工具定义”的请求确认；不通就停下汇报，不要绕路。
- 费用估算：每张图首次约 1024 token × ¥2/百万 ≈ ¥0.002；之后命中前缀缓存 ≈ ¥0.00004/轮。每天 10 张约 ¥0.02。
- 参考实现（Lan 旧项目小手机，已在真机用过）：`yecays-ai/veil` 的 `lib/chat/image-input.ts`（只保留最新 1 个带图回合发图片，更早的回合只发文字占位，避免每轮重复发旧图）、`lib/chat/attachment.ts`（只收栅格图，不收 SVG；存消息时只存引用，不把 base64 写进聊天文本）、`lib/chat/provider.ts:68`（content 数组拼法）。照这个做法搬到 Python，不引入新库。
- Fount 的附件模块是 JS 多标签页运行时，不能直接复用；只借其已验证的发送契约：首次异步操作前冻结本次文字与附件，成功只清除该快照，失败保留。VEIL 继续使用 Python 本机服务和现有单会话结构，不引入依赖。
- PR #112 做出了选择、上传、气泡和本机存储，但没有把图片放进模型请求，Peter 实际只能收到 `[图片]`；初版还有发送崩溃、busy 卡死、异步串图和孤儿上传等问题。本工单在最新 main 上接管并重建发送生命周期。

## 要做的行为

1. 网页输入框旁加“图片”按钮，也支持粘贴/拖入图片。发送前显示缩略图，可取消。浏览器端把长边缩到 ≤2048px、转 JPEG，单张 ≤5MB；一次一张。现有 `/api/send` 的 `MAX_BODY = 200_000` 放不下图片：另设本地图片上传接口（限 token、限大小、只收栅格图），或相应扩大限制。
2. 图片存到角色目录 `data/peter/media/`（文件名含日期和随机 id），`chat.jsonl` 的这条 user 记录只存相对路径和 Lan 打的字，不存 base64。
3. 发给模型时：只有最新一个带图回合附真实图片；其余带图回合只发英文占位 `[Lan sent a photo]`（加上她打的字）。不往 system 或每轮上下文里加任何说明。`self_facts` 末尾有 “Unlisted abilities are unavailable.”，若真实测试中 Peter 因此说自己看不到图，只在能力事实里如实补一句可以看 Lan 发的图片，不加别的。
4. 网页历史（含刷新、重启后）在气泡里显示这张图。
5. 图片读不到或模型报错时：这条消息照常保留，页面给出清楚的中文提示，不自动重发。
6. 能力通知：按 067/#109 现有机制给 Peter 一次性英文通知并写入 `capability_history`，内容只说事实，例如 “Lan can now send you photos in chat; you see the newest one directly. Older photos appear as [Lan sent a photo].” 不加每轮提示，不写风格要求。
7. 更新 STATUS.md、HANDOFF.md。

## 验收步骤

1. `git pull --ff-only`，双击 `start-web.bat`。点图片按钮选一张照片，附一句 “what do you see?” 发送。Peter 说出的内容和照片对得上。
2. 不再发图，接着问一个关于这张图细节的问题，Peter 能答。
3. 粘贴一张截图发送，同样能看到；刷新页面、退出服务再启动后，两张图都还在聊天记录里。
4. 发完图后问 Peter 有没有新能力的变化，他能自己查到这条记录（`capability_history`），且之后几轮不再重复通知。
5. 用 `/cost` 看今天花费，发图前后差额在几分钱以内。
