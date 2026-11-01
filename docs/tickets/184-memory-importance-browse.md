# 工单 184：记忆可以定重要度、置顶要稀缺、能按时间翻旧记忆

## 目标

- Peter 新建记忆时可以给重要度（1 到 10，默认 5）。重要度决定新对话开头先浮现什么；之前所有记忆都是 5，排序等于只看时间。
- Peter 可以把极少数记忆置顶（永久核心记忆）：同时最多 8 条（Ombre 自己的上限是 20；被隐藏的置顶记忆也算数），必须写 `why_remembered`，只能新建时置顶，不能与 `importance` 同传，自主活动（后台）里不可用。用 `trace(pinned=1)` 置顶同样受 8 条限制，取消置顶不受限。
- Peter 能翻出指定时间段的旧记忆：`memory_catalog` 增加 `order`（`id` 默认、`newest`、`oldest`）、`created_from`、`created_to`（YYYY-MM-DD，含当天），每条带创建日期，结果带 `total`；`breath_search` 增加 `date_from`、`date_to`（直接用 Ombre 官方的日期过滤）。

## 为什么没有直接用官方目录 `breath_advanced(catalog=True)`

对照官方源码（`tools/breath/catalog.py`；对照审计在项目文件库，不在本仓库）发现官方目录不能当浏览器用：

- 每行只有名字、域、重要度，没有 memory ID，Peter 拿到后无法 `memory_read` 或追加。
- 最多 50 条，没有翻页；超出的按重要度排序后被静默截掉，不提示还有多少条。
- 我们的重要度几乎全是 5，同分时顺序不可预期。

所以只复用官方的日期过滤（`breath_search`），目录仍用 VEIL 已有的 `memory_catalog`（本地读文件，不调模型），补上时间排序、日期区间和翻页。

## 做了什么

- `memory_native.py`：`hold` 增加 `importance`、`pinned`；校验（整数 1 到 10、布尔、新建专用、pinned 要理由且不能带 importance、`PINNED_LIMIT = 8`、自主模式拒绝）；`trace` 置顶时检查同一上限；`memory_catalog`、`breath_search` 如上。
- `memory_agent.py`：Ombre 置顶写入返回「📌钉选→ID」，`created_id` / `record_native` 现在认得它（否则会被当成保存未确认）。
- 没有改 Ombre 源码，没有改已有记忆。

## 没做

- 110 条记忆的重要度和置顶建议表（下一步，只出表，等 lan 审核后再改）。
- `hold(feel)`、`I` 的评估，召回带上下文、瞄一眼不召回、重复合并：另开单。
- 开场记忆取前 3 条的逻辑没动。

## 验收

1. 更新后让 Peter 记一件新事，并告诉他这是很重要的事：打开 `data\peter\tools.jsonl` 看到 `hold` 带 `importance`；记忆页能看到这条。
2. 让 Peter「翻一下十月二十日到二十五日的旧记忆」：他会用 `memory_catalog` 带日期，一页页翻完，`total` 与条数一致。
3. 置顶：让他把一件事置顶；不给理由会被拒绝，满 8 条会被拒绝。
