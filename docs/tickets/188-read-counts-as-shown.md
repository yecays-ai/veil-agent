# 工单 188：读过的记忆也算「展示过」，可以追加或合并

## 背景
11-01 Peter 整理重复记忆时，用 `memory_read` 逐条读了几十条，再用 `hold(target_id=…)` 合并或追加，却被拒绝：「target_id must be a memory you were shown: an automatic recall or a breath_search result」。他读过的那一条并不在「展示过」名单里，因为只有自动召回和 `breath_search` 会登记，`memory_read` 不登记；名单上限 40 条，读得多了更早的也会掉出去。工单 183 的本意是「Peter 看到过的记忆都能直接追加」，`memory_read` 漏了。

## 改动
`memory_native.py` 的 `_read`：成功读出一段记忆后，把它登记为「展示过」（`mark_surfaced`）。读失败、被隐藏、不存在的不登记。其他规则不变：隐藏或已归档的记忆仍被拒绝，名单仍有 40 条上限，追加前仍存旧版本。

## 测试
`tests/test_native_memory.py` 加 2 项：先读后追加成功；读不存在的记忆不会登记。

## 没改的
聊天里每次工具调用一张卡片，连续几十次会把屏幕占满，这是界面行为，冻结期不改，需要的话另开一单（连续同类卡片折叠）。

## 验收
让 Peter 对一条没搜过的记忆先 `memory_read`，再 `hold` 带 `target_id` 追加一句，应成功，且旧版本留在记忆历史里。
