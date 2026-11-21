# 413 ongoing 只留「进行中 / 已完成」，删掉归档，Peter 能自己维护和查

属于 A（陪伴核心）。Lan 2026-11-21 定：归档不代表做完，所以不需要归档；ongoing 就是「没做完的事」，做完了就标完成，他以后能查到完成时间。

## 目标

1. 去掉「已归档」状态。旧文件里的 archived 读成 completed，不丢数据、不报错。
2. Peter 页：「进行中」只放 active/uncertain；已完成的收在下面默认折叠的「已完成 N 条」里，可重开、可删除；归档条目的正文留在磁盘上，由 Lan 自己维护。
3. 已完成的条目不进上下文、不占 4,000 字节；每轮只带一行计数「已完成 N 条」，他要看内容时用 `ongoing_update` 的 `list`，返回正文和完成时间。
4. `ongoing_update` 放常驻：他在 `other` 组里从未被调用过（工具日志 0 次），所以「ongoing 由 Peter 维护」这件事从来没发生。
5. 修 Peter 页五个缺陷（同一个 bug 家族）：
   - 删除按钮是死按钮：`for (var index)` 闭包，所有行的 index 都等于 `items.length`，`splice` 删不到东西；
   - 列表/条数不按状态过滤，归档或完成后仍显示在「正在进行」；
   - 字节条（前端 + `_content_bytes`）把已完成也算进 4,000；
   - `mark()` 找 `data-peter-card="ongoing/growth/facts"`，卡片实际叫 `learned`，从未显示过「未保存」；
   - 保存请求在飞时再点保存会被静默丢弃；响应回来会把期间输入的字覆盖掉。

## 改动

- `learned_context.py`：`ONGOING_STATUSES` 去掉 archived；`_upgrade_ongoing()` 读旧文件时把 archived 读成 completed 并补完成时间；`_content_bytes` 只算 active/uncertain；`dynamic_context` 为已完成加一行计数（登记文本 `memory.ongoing.done`）；`_invoke_ongoing` 去掉 archive，新增 `list`；从页面保存时给新完成的条目补完成时间。
- `learned_tools.py`：工具说明改成「未完成的事、每轮带、你自己维护、完成后用 list 查、归档已删除」。
- `tool_groups.py`：`ongoing_update` 进 `ALWAYS_ON`。
- `desktop/peter-page.js`：状态只剩三种；已完成单独折叠区（重开/删除）；计数和字节只算进行中；删除/重开立即落盘，失败就重读；保存串行化（不再静默丢点击）；保存响应不覆盖正在编辑的内容；`mark` 映射到 `learned` 卡片；空行保存前丢弃。
- `desktop/chat.html`、`desktop/chat.css`：`#peter-ongoing-done` 折叠块与样式。
- 测试：`tests/test_learned_context.py` 新增迁移/计数/list/预算 4 组；`desktop/tests/ui.test.cjs` 新增 3 条静态护栏（禁 `for (var`、页面 id 必须存在、已完成与进行中分开计数）。

## 验收步骤

1. 打开 Peter 页：旧的「已归档」那条出现在「已完成 1 条」里，点开能看到正文和完成时间。
2. 新增一条 → 保存 → 出现在「进行中」；把它改成「已完成」→ 保存 → 从列表消失，条数变 0，折叠区多 1 条，4,000 字节的数字降下来。
3. 点「删除」→ 确认 → 刷新页面它也不会回来。
4. 「已完成」里点「重开」→ 它回到进行中。
5. 下一句聊天时问他「你还记得有已完成的事吗」，他应知道有几条；让他用 `ongoing_update list` 查一条，能说出正文和完成时间。

## 不做

- 不改记忆库、召回、判官。
- 不给已完成条目做自动清理（Lan 自己删）。
