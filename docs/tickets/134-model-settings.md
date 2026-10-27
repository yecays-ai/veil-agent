# 工单 134 设置里加模型：接入中转站、分模块选模型、聊天里切换

## 目标

Lan 2026-10-27：在中转站充了钱，想让 Peter 试 Sonnet 5.5，以后还要试别的模型。要在界面上加接口和模型，聊天时能切换；记忆后台单独用便宜的 DeepSeek。Claude 实现。做法照 Cherry Studio 一类软件：先加接口，再加模型，再给每个用途选模型。

## 做了什么

- 「更多」页新增「模型」卡片：
  - 接口：名字、地址（必须 https）、API Key。key 只写进本机 `.env`（变量名 `VEIL_KEY_<接口编号>`），页面只显示「key 已保存 …后四位」；删除接口时一并从 `.env` 删掉它的 key。
  - 模型：属于哪个接口、模型名（照中转站抄）、显示名、计费方式（按次 ¥/次，或按 token ¥/百万）、最长输入、思考模式开关。character.toml 里的 deepseek、gpt 作为「内置」出现，不能在页面改。
  - 用途：「Peter 本人」（聊天、自主活动、瞄一眼、QQ、邮件都是他在说话，用同一个）和「记忆后台」（Ombre 打标签、短期便签整理），各自下拉选。默认都是 character.toml 的 provider。
  - 费用上限：每天 / 每月，替代原来写死的 ¥10 / ¥300。
- 顶栏加一个下拉，直接切 Peter 用的模型；下一次请求开始生效（正在回复的那一轮不中途换）。
- 记忆后台换模型要完全退出 Peter 再打开才生效（Ombre 常驻进程启动时读配置），页面会提示。
- 本机文件 `data/<角色>/models.json`（不进仓库），所以 update.bat 不会和它冲突。删掉的模型被选中时自动回到 character.toml 的默认。
- 记账：按次计费的模型每次请求记固定价格；Ombre 打标签如果用按次模型，预留 6 次（分析+判断，各最多重试 3 次），成功后记 2 次。`/cost` 多一行「今天按模型」，列出每个模型今天花了多少、几次。
- 程序文件：`model_settings.py`（新）、`veil_agent.py`（读取本机选择、按次记账、可切换的模型调用、上限）、`memory_agent.py` / `ombre_resident.py`（Ombre 用记忆后台模型）、`web_agent.py`（`/api/models`、`/api/models/use`，状态随 `/api/events` 下发，不含 key）；桌面 `chat.html`、`chat.js`、`chat.css`、`icons.js`、`main.cjs`（放行两个新接口，独立「更多」窗口也可用）。测试 `tests/test_model_settings.py`。

## 验收（Lan）

1. 退出 Peter → 双击 update.bat（会重新打包 exe）→ 打开 Peter。
2. 「更多」→「模型」→「添加接口」：名字 `jiushi`，地址 `https://api.jiushi.xin/v1`，粘贴中转站的 key → 保存接口。列表里显示「key 已保存 …后四位」。
3. 「添加模型」：模型名 `[k]claude-sonnet-5.5`，显示名 `Sonnet 5.5`，按次，每次 0.06 → 保存模型。
4. 费用上限填每天 30、每月 600 → 保存。
5. 顶栏下拉选「Sonnet 5.5 · jiushi」，聊天里出现「下一句开始，Peter 用 Sonnet 5.5」。跟他聊几句：让他存一条记忆、一次发两个气泡、用一次工具。
6. 「记忆后台」保持 deepseek（内置）。点「花费」，`/cost` 里「今天按模型」同时有 deepseek 和 Sonnet 两行，Sonnet 每次 ¥0.06。
7. 下拉切回 deepseek（内置）继续聊，正常回复。
