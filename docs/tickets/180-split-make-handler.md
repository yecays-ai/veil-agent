# 工单 180：拆分 `make_handler` 的路由

## 目标

- `web_agent.py` 里的 `make_handler` 原来一个函数 624 行，`do_GET` 200 行、`do_POST` 369 行。把每条路由的处理搬成独立方法，`do_GET`/`do_POST` 只做分发。
- 只搬代码，不改任何行为：路由顺序、鉴权顺序、状态码、返回内容、错误处理都与之前一致。

## 做了什么

- 新增 `_get_game`、`_get_memory_panel`、`_get_memory`、`_get_attachment`、`_get_events`、`_get_mcp`、`_get_screen_pop_next`、`_get_desktop_icon_snapshots`、`_get_media` 九个 GET 方法。
- 新增 `_post_game_score`、`_post_screen_pop`、`_post_memory_change`、`_post_media`、`_post_transcribe`、`_post_voice`、`_post_message_action`、`_post_send`、`_post_voice_retry`、`_post_approve`、`_post_stop`、`_post_desktop_icons_restore`、`_post_typing`、`_post_request_reply`、`_post_activity`、`_post_glance`、`_post_models`、`_post_mcp`、`_post_quiet_hours`、`_post_codex_watch`、`_post_exit` 等 POST 方法。
- 方法仍在 `make_handler` 内，继续使用同一个 `hub` 和 `token`；`do_POST` 保留「游戏路由 → 令牌校验 → 各自带请求体的路由 → 通用 JSON 解析 → 其余路由」的原有顺序。

## 怎么证明没有改行为

- 用脚本按行号搬移，并核对：改动前后 `make_handler` 内的每一行代码（忽略缩进）完全一致，只多出方法定义、分发调用和几个 `return`，唯一一处 `if` 变为 `elif`（`/api/memory/panel`，等价）。
- 网页相关测试（`test_web` 等 82 项）结果与修改前完全一致。

## 有意没做

- 没有把这些方法移出 `make_handler`，也没有合并重复的请求体解析；那是更大的改动，留给后面的工单。
- 没有顺手修任何逻辑。

## 验收

- 没有行为变化。更新后正常聊天、发图片、发语音、打开记忆面板、桌面整理各用一次即可。
