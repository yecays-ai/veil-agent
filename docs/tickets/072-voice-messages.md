# 工单 072｜给 Peter 发语音

## 目标

Lan 在网页或 Electron 聊天窗里按住麦克风说话，松开就作为一条语音消息发给 Peter：气泡里能回放录音、显示识别出的文字；Peter 收到的是“这是一条语音”和识别文字，照常回复。

- 做完后能做什么：现在的 🎙（工单 029）只是把话转成输入框里的字、还要再点发送，且依赖 Chrome 在线识别；做完后是真正的“发语音”，像微信一样按住说、松开发，录音本身也留在聊天里。
- 比最简单做法多什么：最简单是继续用 029 的听写。区别是录音保存、可回放、Peter 知道这是 Lan 说的话而不是打的字，中英文混说识别更稳。
- 怎么验收：分别在网页和 Electron 聊天窗发语音，见下方验收步骤。

## 现状（已核实）

- 识别服务用硅基流动 `FunAudioLLM/SenseVoiceSmall`：`POST https://api.siliconflow.cn/v1/audio/transcriptions`（multipart：`file` + `model`），定价页标“免费”（https://siliconflow.cn/pricing ）。Lan 已有硅基流动 key（`.env` 里的 `EMBED_API_KEY`，记忆向量在用），直接复用，不新增 key。识别费用 ¥0/天。
- 录音：浏览器原生 `MediaRecorder`，不引入新库。参考 Lan 旧项目小手机（真机用过）：`yecays-ai/veil` 的 `lib/chat/voice-recorder.ts`（录音）、`lib/tts/transcribe.ts`（OpenAI 兼容转写请求、错误分类）、`lib/chat/attachment.ts`（语音最长 120 秒、≤10MB、未识别时的占位）。
- 识别请求由本地 Python 服务发出（key 不进网页），网页把录音交给本地服务。
- 桌面壳原先拒绝所有设备权限。本工单只给本机已认证聊天窗口开放麦克风，不开放摄像头或其他来源；旧的浏览器听写按钮仍在 Electron 中禁用，新语音消息按钮可用。
- 029 的听写按钮保留不动。

## 要做的行为

1. 输入框旁加“按住说话”按钮（鼠标按住或空格长按），松开即停止录音；录音中显示时长，最长 120 秒自动结束；按住时移出按钮可取消。
2. 录音存到 `data/peter/media/`；本地服务调用 SenseVoiceSmall 识别。`chat.jsonl` 这条 user 记录存录音相对路径和识别文字。
3. 发给模型的内容是 `[voice message] <识别文字>`，只此一个事实标记，不加任何风格或语言提示。`self_facts` 的 “You have no voice.” 说的是 Peter 不能说话，与他收到转写文字不矛盾，本单不改它（073 再改）。
4. 气泡显示播放按钮和识别文字；刷新、重启后仍可回放。
5. 识别失败（无网、key 错、没识别出字）：录音保留在气泡里，显示中文原因和“重试识别”，不自动发给 Peter，不自动重试。
6. 能力通知：按现有机制给 Peter 一次性英文通知并写入 `capability_history`，例如 “Lan can now send you voice messages; you receive them as [voice message] followed by the transcript.”
7. 更新 STATUS.md、HANDOFF.md。

## 验收步骤

1. `git pull --ff-only`，双击 `start-web.bat`。在网页按住说一句中文、松开；气泡出现录音和文字，Peter 回复内容对得上。
2. 再按住说一句英文或中英混说，文字基本正确；点播放能听到自己的录音。
3. 刷新页面、退出服务再启动后，两条语音都能回放。
4. 拔网线（或关 Wi-Fi）后发一条语音：看到中文失败原因和“重试识别”，Peter 没收到；联网后点重试，正常发出。
5. 问 Peter 能不能听语音，他能自己查到这条能力记录，之后不重复通知。
6. 打开 Electron 聊天窗，允许麦克风并发送一条语音；确认行为与网页一致，摄像头及其他页面仍没有设备权限。
