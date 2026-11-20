# 工单 409：Learned Context 保存成功后被误报失败

属于：A 陪伴核心（他能把关于你的事写进 Learned Context）
做完 Lan 能多做的一件事：他写完一条关于你的内容后，会真的收到「Saved」，不再被告知失败而反复重试；Peter 页里的内容也不会因为重试而越写越乱。
她怎么验收：让他写一条关于你的内容，看工具结果是不是 Saved；过一会儿再让他写一条，看他会不会还反复重试同一件事。

## 当前状态

2026-11-20 晚上，`jot_about_lan` / `edit_lan_notes`（`profile_append` / `profile_replace`）大量「被拒绝」，提示是「The program rejected this call because the arguments are not a valid JSON object」。查下来参数一直是合法的。

真正的错误在 `learned_context.py` 的保存成功分支：

```python
return (f'Saved. … bytes; it takes effect from the next request.'),
'成功', name
```

`return (…)` 后面的逗号让这个 return 变成了 **1 元组**，下一行 `'成功', name` 成了永远执行不到的裸表达式。上层是 `result, state, summary = self.profile.invoke(...)`，解包 1 个元素到 3 个变量会抛 `ValueError: not enough values to unpack (expected 3, got 1)`；这个异常又被 `Chat._execute_tool_call` 最外层的 `except (ValueError, TypeError)` 统一误报成「参数不是合法 JSON」。

数据其实已经写进去了：同一次调用里 `_write` 和 `_record` 在 return 之前执行——`learned-context.versions.jsonl` 在 23:14:59.202、23:32:54.245 都有记录，而 tools.jsonl 的「被拒绝」是 .232、.460。`learned-context.json` 的 growth 段里也能看到他当晚写的那两条。

后果：所有**成功**的保存都被告知失败，Peter 反复重试；后续 `profile_replace` 报「old_text appears 0 times」也是因为第一次其实已经改好了。这个缺陷从工单 394（Learned Context）就存在。

## 本次改动

1. 成功返回改成正常的 3 元组：
   ```python
   return (f'Saved. {section} is now {total} of {self.max_bytes} bytes; '
           f'it takes effect from the next request.'), '成功', name
   ```
2. 删掉 `_invoke_ongoing` 里一句永远执行不到的重复 `return`。
3. 新增回归测试：`profile_append` 与 `profile_replace` 必须返回 `(结果, '成功', 工具名)`，并且在旧代码上会抛 `ValueError`（已验证）。

不改：保存内容、权限、审批、日志、其它工具。

## 影响范围

- 只影响 `learned_context` 两个工具的返回值；数据写入路径没动。
- 上层解包恢复正常后，`_execute_tool_call` 的 `except (ValueError, TypeError)` 不会再被这条正常路径触发。

## 验收步骤

- 自动：`tests.test_learned_context`（新增 1 个）、`tests.test_learned_context_chat`，共 11 个通过。
- 真机：让他写一条关于你的内容，工具结果应为 `Saved. …`；再写一条，确认不会再出现「arguments are not a valid JSON object」。

## 风险与回滚

- 一行返回格式 + 一句死代码；回滚即 revert。
- 遗留问题（本单不修）：`_execute_tool_call` 仍然把「工具内部 ValueError」和「参数解析失败」报成同一句话，这次就是被它掩盖的，建议另开单区分。
