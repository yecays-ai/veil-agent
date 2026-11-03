# 工单 210：顶部「模型」下拉能点开

## 目标
在桌面窗口顶部点「gemini-4-argon · jiushi」这类模型下拉，能展开列表并切换 Peter 的模型，下一句话开始生效。

## 现状
顶栏整条是窗口拖动区（`-webkit-app-region:drag`），只有右侧按钮单独设了 `no-drag`，模型下拉没设，所以点击被当成拖窗口，下拉打不开。判断依据：读 `desktop/chat.css` 与 `chat.html`，切换逻辑本身（`/api/models/use`）没问题；Windows 真机未验证。改动：给 `.header-model` 和它里面的下拉加 `-webkit-app-region:no-drag`。

## 验收
1. 更新并完全重开 Peter（桌面窗口）。
2. 点顶部模型下拉，能展开列表。
3. 选另一个模型，聊天里出现「下一句开始，Peter 用 … 」，再发一句，页面底部/账本里的模型是新选的那个。
4. 拖顶栏空白处，窗口仍能拖动。
