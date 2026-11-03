# 工单 208：短期便签后台提取默认关闭

## 目标
Lan 11-02 决定先关掉短期便签后台提取（没有体会到作用，只是在花钱）。已有便签数据、`notes.jsonl` 和「上下文」页面都保留；以后想重做，把 `characters/peter/character.toml` 的 `[short_term_notes] enabled` 改回 `true` 即可。

## 现状
Lan 电脑上的 `character.toml` 已手改为 `enabled = false`。本工单把仓库默认值也改成 `false`，避免 `update.bat` 因本地改动冲突或把它改回开启。只改配置，不改代码。

## 验收
1. 退出 Peter，运行 `update.bat`（若报 `character.toml` 本地改动冲突，告诉我）。
2. 聊天一会儿后，`data\peter\notes.jsonl` 不再新增 `attempt` 行，`costs.jsonl` 里不再出现新的 `short_term_notes`。
