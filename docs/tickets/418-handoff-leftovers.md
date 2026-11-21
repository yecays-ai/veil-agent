# 418 把 HANDOFF 里过期的遗留标清楚，并列出真正还开着的尾巴

属于维护（不改产品行为）。

## 为什么

Lan 问「HANDOFF 里是不是还有遗留的小东西，另一个 chat 说它有个尾巴没做，写进去了」。逐条对着代码核了一遍：

- **409 那条「工具内部异常被报成参数不是合法 JSON」已经修了**：工单 415（PR #415，提交 `6c020af`）把它分开——参数错走 `json.JSONDecodeError` / `tool_failures.ArgumentError`；工具内部异常走 `tool_failures.internal_failure()`，给一个 error id，异常原文只写本机 `tool-errors.jsonl`，回归测试在 `tests/test_tool_failures.py`（19 条）。HANDOFF 里那句「建议另开单」是没更新。
- **401 那条「68 条长句未迁」也完成了**：`prompts/inline_allowlist.txt` 已删除，改由 `prompts/inline_feedback_allowlist.txt`（49 条，类型化原因、只许减少）做棘轮检查；402/405 之后没有未迁移的提示词。
- **407 那条写着「后续迁移单 409」是笔误**：409 是 Learned Context 保存那张单，没做旧记忆迁移；这件事至今没开单。

## 改了什么

只改 `HANDOFF.md` 和 `STATUS.md`：

1. HANDOFF 顶部新增「仍然开着的尾巴」一节，按代码现状写清楚：Electron 冒烟缺 Peter 页用例（413 留下）、407 的旧记忆迁移、记忆 `0e99e5` 的标题、`git -c … push` 的真机未验证。
2. 把 409、402 两处标成已解决并写明由哪张单解决。
3. 把 407 那处的「后续迁移单 409」改成「笔误，仍未开单」。
4. STATUS 顶部更新到 418，并修正 413 的「本 PR」为已合并。

## 不做

- 不改任何代码、不动记忆库、不补测试。这两处代码已经没有问题，缺的只是记录。

## 验收

不用真机验收。看 `HANDOFF.md` 顶部那一节和 409/402/407 三处，应当和上面一致。
