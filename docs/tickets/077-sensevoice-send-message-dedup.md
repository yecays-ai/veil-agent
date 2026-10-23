# 077：修复语音转写 emoji 泄漏和重复聊天气泡

## 目标

普通语音转写只保留识别文字；`send_message` 已显示的气泡不再作为额外最终回复重复显示或保存。

## 当前状态

最新 main `4eab6bbc97eb3eca8e32720a68d3e52c1d395f63` 的 `web_agent.py` 全局删除已知 emoji，漏掉 `😞`；`veil_agent.py` 在 `send_message` 已显示后仍可能把相同文本作为最终回复再次保存和显示。纯 `send_message` 轮会按每条成功发送记录完成；混合工具轮只抑制与已显示气泡相同的最终回复，不限制消息条数。SenseVoice 的官方实现将事件 emoji 放在文本前、情绪 emoji 放在文本后，并提供对应 metadata 标签；复用项目现有清洗函数，不增加依赖或引入外部实现。参考：[QwenAudio/SenseVoice](https://github.com/QwenAudio/SenseVoice)。

## 验收步骤

1. 说一句普通语音，确认转写没有自动附加情绪 emoji，真实文本里的 emoji 不会被整体删除。
2. 让 Peter 一轮发送多条独立消息，确认 UI、`chat.jsonl` 和重开后的历史都只保留实际发送的消息；混合工具调用后相同的最终文本不重复，不同文本仍保留。消息条数不限，单条和 `voice:true` 发送正常。
