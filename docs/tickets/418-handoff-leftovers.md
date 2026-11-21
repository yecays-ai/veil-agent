# 418 清掉 HANDOFF 里的过期遗留 + 修「表情包后面的文字没有气泡」

属于维护 + 一个桌面渲染修复。

## 一、截图里那条尾巴（409 留的）

Lan 发来的截图是另一个会话写下的：

> 留了一个尾巴（没在本单修）：`_execute_tool_call` 现在仍把「工具内部的 ValueError」和「参数解析失败」报成同一句「不是合法 JSON」……要不要明天开个小单把两类错误分开报？

**已经修了**，不是待办：工单 415（PR #415，提交 `6c020af`）把两类错误分开——

- 参数错：仍说「不是合法 JSON」，并带回他收到的原文（脱敏后）；
- 工具内部异常：走 `tool_failures.internal_failure()`，给一个 error id（`err-xxxx`），异常原文只写进本机 `tool-errors.jsonl`，不再冒充参数错。
- 覆盖测试：`tests/test_tool_failures.py` 里 `test_the_exception_text_never_leaves_the_program`、`test_every_offered_tool_survives_a_crashing_backend`、`test_a_non_object_argument_is_reported_as_such`、`test_json_parse_failure_redacts_the_arguments`，共 19 条。

本单只把 HANDOFF 里这句过期的话改成「已解决、由 415 解决」，不改代码。

## 二、表情包后面的文字没有气泡

**现象**：一条消息以 `[表情包：图集/名字]` 开头时，后面的正文被当成贴图配文，消息框的背景、边框、内边距全被去掉，图和字挤在一起，正文没有气泡。

**根因**：`desktop/chat.js` 的 `stickerBubble()` 给整条消息加了 `sticker-msg`，这条 CSS 把整条消息变透明；正文是它的子节点，所以一起变透明。只有「贴图标记在开头 + 后面有正文」时才会走这条路径，所以大多数贴图消息看起来正常。

**改法（最小）**：正文单独给一个 `.msg-sticker-caption`，按普通气泡画（背景、边框、圆角、内边距、换行保留、最大宽度）。只有贴图时仍然是裸图。旧消息按同一规则重新渲染，不改聊天记录。

- `desktop/chat.js`：`stickerBubble()` 里给正文 div 加 `msg-sticker-caption`。
- `desktop/chat.css`：新增 `.msg.sticker-msg .msg-sticker-caption` 与用户消息配色。
- `desktop/tests/ui.test.cjs`：加一条守卫（正文必须带这个类、CSS 必须有气泡样式）。

**没做**：没有把两段文字拆成两个气泡。要那样得给这条路径补空行拆分，属于另一个行为改动。

## 三、HANDOFF 里其余遗留的核对结果

- 401「68 条长句未迁」：已完成（402/405，`inline_allowlist.txt` 已删，49 条反馈清单棘轮检查）。
- 407「后续迁移单 409」：笔误；145 条旧记忆的重新归组/迁移**仍未做**，要动正式库，需 Lan 同意后另开单。
- 336 `git -c … push`：规则已写，真实 OpenCode 未真机验证。
- 记忆 `0e99e5` 标题：要在 Peter 关闭时改，本单没动。
- Electron 冒烟没有 Peter 页用例：本机跑不了 `npm run test:windows`，本单没加没验过的测试。

## 验收

1. 找一条「贴图 + 正文」的旧消息（例如截图那条）：应变成一张裸贴图，下面一个正常气泡，两段文字都在气泡里。
2. 只有贴图、没有正文的消息：仍然是一张裸图，没有多出空气泡。
3. 用户自己发的贴图+正文：气泡用用户色，不再是透明。
