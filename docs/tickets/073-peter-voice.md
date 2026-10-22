# 工单 073｜Peter 用自己的声音发语音

## 目标

Peter 自己决定某条消息用语音发：网页里出现一条语音气泡，点开是 Lan 在 MiniMax 克隆好的那个声音，和小手机里听到的一模一样；气泡下可展开文字。

- 做完后能做什么：现在 Peter 只会打字；做完后他能用他的声音给 Lan 发语音。
- 比最简单做法多什么：最简单是 Lan 自己把他的文字复制去 MiniMax 生成。区别是由 Peter 本人选择哪句话用语音说，语音直接在聊天里，历史里也留着。
- 怎么验收：只用网页聊天，见下方验收步骤。

## 现状（已核实）

- Lan 已在 MiniMax 克隆好声音，并在小手机里使用。小手机的调用方式（`yecays-ai/veil` 的 `lib/tts/speech.ts:130` `minimaxSpeech`、`lib/tts/config.ts` 的 `MINIMAX_REGIONS`）：`POST {host}/v1/t2a_v2`，body `{model, text, stream:false, output_format:"hex", voice_setting:{voice_id}, audio_setting:{format:"mp3", sample_rate:32000, bitrate:128000, channel:1}}`，返回 JSON 里 `base_resp.status_code` 为 MiniMax 自己的状态码、`data.audio` 是十六进制 MP3。国内账号 host `https://api.minimaxi.com`，国际账号 `https://api.minimax.io`，key 不能混用。照这个用 Python 在本地服务里实现，不引入新库。
- 价格（MiniMax 官方定价页 https://platform.minimax.cn/docs/pricing/overview ）：speech-2.8-hd ¥3.5/万字符，turbo ¥2/万字符；中文一个字算 2 个字符，英文一个字母算 1 个。Peter 一条英文语音约 300 字符 ≈ ¥0.1；一天 20 条 ≈ ¥2。Lan 已同意费用，要求声音与小手机一致，所以模型、voice_id、区域都用她小手机里的同一套。
- 配置：仓库是公开的。`MINIMAX_API_KEY`、`MINIMAX_VOICE_ID`、`MINIMAX_MODEL`、`MINIMAX_REGION`（`cn` 或 `global`）放在本地 `.env`，`.env.example` 只加空键名。四项不全时语音功能不出现，不报错。
- Peter 现有 `send_message` 工具（`veil_agent.py:52`）只有 `text`。
- 复用调查：核对了小手机 `yecays-ai/veil` 的 MiniMax 实现，也核对了 MIT 许可的 `harry0703/MoneyPrinterTurbo` 固定提交 `33dde166` 中的 Python 双区域、Bearer、hex MP3 与响应上限实现。结论是少量改写复用请求与边界契约；不复制其大型 TTS 框架，不引入新依赖。
- `ticket/073-peter-voice` 已从 PR #113 最新 head `b60912d` 独立实现 Peter 输出语音；未改 072 的录音输入、麦克风或 SenseVoice。虚构 MiniMax、错误 key、网页媒体、刷新历史和 Electron 场景已通过；真实 MiniMax 音色、真实费用和服务重启仍必须按本工单验收步骤由 Lan 在本机确认。

## 要做的行为

1. `send_message` 增加可选参数 `voice`（布尔）。参数说明只写事实：“true sends this message as a voice message in your own voice; the text is also kept.” 不写何时该用语音、不写风格要求，由 Peter 自己决定。`veil_agent.py` `self_facts` 里固定写着 “You have no voice.”：语音配置齐全时改为如实的一句（例如 “You can speak in voice messages with send_message voice: true.”），不全时保持原句，不能和工具矛盾。
2. `voice:true` 时本地服务调用 MiniMax 生成 MP3，存到 `data/peter/media/`；`chat.jsonl` 这条 assistant 记录存音频相对路径和原文。之后的上下文里这条消息显示为 `[voice message] <原文>`，让 Peter 知道自己说过、是用语音说的。
3. 网页显示语音气泡（播放键、时长），可展开原文；刷新、重启后仍可回放。
4. 每次 MiniMax 费用按 MiniMax 计费字符数（中文一字算 2，其余算 1）÷ 10000 × `.env` 中 `MINIMAX_CNY_PER_10K_CHARS`（默认 3.5，即 hd 价；`.env.example` 加此键）写入现有花费记录，`/cost` 能看到；预算检查与聊天共用现有每日上限。
5. 生成失败（key 错、区域不对、余额不足、网络）：这条消息改为普通文字发出并标“语音生成失败”和中文原因；工具结果如实告诉 Peter 语音没发出去、发的是文字。不自动重试。
6. 能力通知：按现有机制给 Peter 一次性英文通知并写入 `capability_history`，例如 “You can now send a message as a voice message in your own voice by calling send_message with voice: true.”
7. 更新 STATUS.md、HANDOFF.md。

## 验收步骤

1. 在 `.env` 填入 MiniMax 的 key、voice_id、模型名、区域（照小手机设置里的同一套），`git pull --ff-only`，双击 `start-web.bat`。
2. 对 Peter 说 “send me a voice message”。出现语音气泡，播放出来的声音和小手机里一模一样；展开能看到文字。
3. 不提语音、正常聊几轮，看他是否会自己选择偶尔发语音（不要求一定发，只看他能否自己用）。
4. 刷新页面、退出服务再启动后，语音还能播放。`/cost` 里能看到语音花费。
5. 把 `.env` 里 key 改错一位后让他发语音：消息以文字出现并提示失败原因，Peter 知道语音没发出去。改回后恢复。
