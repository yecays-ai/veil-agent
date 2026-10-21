# 工单 042：自主活动摘要只向聊天交接一次

## 目标

Peter 在自主活动后首次成功的普通聊天中获知最新活动；成功交接后，同一摘要不再进入后续聊天，重启后仍有效，新活动仍可交接。

## 当前状态

基线为最新 main `8d71b06d52efaa65ee1dc4a535bc62fc5fef632a`。普通聊天每轮调用 `autonomy.recent_chat_context()`，当前活动摘要没有持久化交接状态。修复在普通聊天成功后向独立交接日志追加活动 ID；模型请求失败时不确认，原始 `autonomy.jsonl` 不改。

复用检查：检查了 [AWS transactional outbox 示例的锁定与持久化实现](https://github.com/aws-samples/transactional-outbox-pattern/blob/23e0519c7a8048c6db4acd8f3d7de534a9d5deac/outbox-implementation/src/main/java/com/amazonaws/samples/repository/OutboxRepository.java)（MIT-0，Java/Spring、数据库悲观锁、较旧提交）。它用于跨服务投递，不适合本地单体聊天；不复制代码或引入依赖，只采用成功后持久确认、失败保留重试的原则，使用现有本地存储。

## 验收步骤

1. 用专用虚构测试角色完成一次自主活动；下一条成功普通聊天包含摘要，之后的聊天和服务重启后都不再包含同一摘要。
2. 追加一条新的虚构自主活动；下一条成功聊天收到新摘要。确认 `/autonomy` 与活动详情仍可查看，旧活动历史未改变。
3. 让一次虚构模型请求失败后重试；确认失败没有消费交接机会，首次成功聊天仍收到摘要。
