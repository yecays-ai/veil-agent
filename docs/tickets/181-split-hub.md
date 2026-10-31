# 工单 181：拆分 `Hub`（第一步：屏幕弹幕）

## 目标

- `web_agent.py` 的 `Hub` 类 1446 行、56 个方法。这一步先把职责最独立的一组方法搬出去，不改行为。

## 做了什么

- 新增 `hub_screen_pop.py`，里面的 `ScreenPopMixin` 放 `request_screen_pop`、`next_screen_pop`、`claim_screen_pop`、`record_screen_pop_event` 四个方法，代码一行不变。
- `Hub` 改为 `class Hub(ScreenPopMixin)`；屏幕弹幕用到的状态仍然在 `Hub.__init__` 里创建，所以对外接口、锁和调用方式都没有变。

## 怎么证明没有改行为

- 核对改动前后所有代码行：除类定义、导入和文件头说明外，没有任何一行增减。
- 全套测试与修改前失败项一致（均为沙箱环境限制，CI 为准）。

## 有意没做

- 媒体与语音（约 310 行）、消息操作、设置开关这几组也适合拆，但测试里有对 `web_agent.urlopen` 之类名字的替换，直接搬会让替换失效，需要先处理，留给后续单独的工单。
- 没有改 `Hub.__init__`，没有顺手修任何逻辑。

## 验收

- 没有行为变化。更新后让 Peter 弹一次屏幕弹幕即可。
