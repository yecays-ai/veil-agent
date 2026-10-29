# 工单 159：小红书页面没加载完不算拒绝

## 原因（10-29 Lan 实测）

更新 158 后运行登录：登录窗口显示「当前登录状态: true」，紧接着后台复查却报「Xiaohongshu refused the read」。

上游 xiaohongshu-mcp v2.5.5 的 `/api/v1/login/status` 在自己的浏览器页面 30 秒内没加载完或出错时返回 HTTP 500 `STATUS_CHECK_FAILED`。真正没登录是 200 + `is_logged_in: false`。搜索、推荐、详情同理（`SEARCH_FEEDS_FAILED`、`LIST_FEEDS_FAILED`、`GET_FEED_DETAIL_FAILED`）。158 把这些 500 当成「平台拒绝」并锁死。

## 改动

- 上游 500 且代码属于上面四个页面失败代码 → 临时失败，不锁死；只保留代码本身，不转述上游详情。
- 连续 3 次临时失败（含超时）→ 暂停读取并说明「连续 3 次没加载出来，为保护账号先暂停」，防止风控时反复访问；任何一次成功清零。
- 其他拒绝在错误文字里带上 HTTP 状态和上游代码（仅大写字母/下划线），写进 `data\peter\xiaohongshu\errors.jsonl`，方便判断。
- 登录脚本：登录成功但后台复查页面没加载完时，已有绑定直接提示「不是登录问题，直接启动 VEIL」；没有绑定时提示稍后再运行一次。

## 验收

1. 退出 Peter，双击 update.bat，启动 VEIL。
2. 让 Peter 查一次小红书状态或搜索。偶尔一次「太慢」可以接受，他之后还能再试。
3. 如果失败，把 `data\peter\xiaohongshu\errors.jsonl` 最后几行发给 Claude。
