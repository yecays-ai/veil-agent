# HANDOFF

> 更新时间：2026-11-27T15:42:00+08:00 · 基线 `main` = `2a264a7`（PR #458 已合并）· 当前分支 `ticket/460-p6-s5-retrieval-judge-association`（工单 460 · PR #461 已完成独立复审 F1–F8 集中修复、完整生产上下文三路盲评重生成与 S3+S5 隔离合流回归，待独立复审；未合并、未部署正式目录）

## 当前停点

- **本单（工单 460 · PR #461 · P6-S5 独立复审 F1–F8 修复完成）**：
  - **F1 / F6（完整生产链路回放与三路盲评）**：`s5_eval.py` 改用真实生产 `Memory.context()` + `finish_recall(space=4000)` + `confirm_delivery()` 回放，三路盲评使用完整生产请求组装（真实人设 + Learned Context + `relationship_base` + 近期历史 + 正常判官召回 + Ongoing），仅自然联想变化；`memory_agent._association_pool()` 改为轻量元数据缓存（不缓存全库正文），三层均衡覆盖长尾 `imp>=7` + 近期 + 普通生活。
  - **F2 / F3（任务意图与话题切换释放）**：`context_signal.py` 移除 `len > 70` 硬截断，明确技术任务与情绪并存时仍判 `technical_task`（抑制无关联想、保留情绪标记）；`memory_agent.context()` 先独立判定任务意图再判定 `followup`，修复话题切换释放逻辑。
  - **F4 / F5（最终联合对照与分项异常统计）**：补跑 `s5_final_candidate_sw_on_k8`（停用词开 + K=8 + S5 判官），实测与 S5 判官存在负交互（FPR 反弹回 `0.5455`），故停用词与 `K=12` 均保持默认关闭/`K=8`；单独统计正常拒绝/超时/报错/解析失败与降级注入（全臂次 0 异常/0 降级）。
  - **F7（首次联想回滚与请求归属核验）**：新增 `bind_recall_request()` 与 `_has_association_rollback`，修复首次联想旧状态为 `None` 的回滚遗漏，并拒绝不匹配 `request_id` 的确认/回滚。
  - **F8（S3+S5 合流验证）**：隔离合流 `de2cc06`（S3）+ S5，三个公共代码文件（`veil_agent.py`、`brain_p4_api.py`、`workspace-brain.js`）0 冲突自动合并，40 项联合测试 + 前端语法检查全绿。
  - **门禁状态**：S5 情境判官（`sw=off, K=8`）通过门禁；停用词、`K=12`、情境自然联想（生产回放 `work_trap_intrusions=2 > 0`）与 Qwen3-Reranker 均严格保持默认关闭；DeepSeek 实验累计花费 `¥2.54 / ¥10.00`。

## 下一个窗口第一步

1. 待本单 PR 合并后，若继续 `feat/p4-p5-brain-rework`，先在该分支的隔离副本运行 `.venv\Scripts\python.exe scripts/dev/migrate_layout.py --all --check`，确认无目标冲突后再运行 `--all --move --rewrite`；工具会先改写、再移动、生成兼容入口，并在失败时恢复原文件。
2. 读 `docs/ACCEPTANCE.md`，按 Lan 体验结果更新验收勾选并推进 P6-S3/S5/S6/S7。
3. 三项待授权操作（B-3 正式历史恢复、B-4 换 Embedding 重建、H-1 QQ 邮箱）只在 Lan 明确同意后做，且先 dry-run。

## 环境与工具注意

- 正式目录 `.git` 为写保护，**不要在正式目录开分支、提交或改文件**；开发一律在独立 worktree / 副本里做。
- 本机 `git` 走 HTTPS 拉取时可能因 schannel 凭据不可用而失败；本单改用 `gh api` 推送分支，未改任何全局 git 配置。
- 本机 Node 24 用于桌面构建；Python 3.11/3.12 跑测试。

## 红线（不变）

- 不启停正式 Peter、不写正式 `data/peter`、不向正式 Peter 发消息。
- 正式恢复必须先做协同 bundle + dry-run，经 Lan 确认后才 `--apply`。
- 换 Embedding 模型必须用 `scripts/embedding_reindex.py`（先 dry-run 再 `--apply`），不依赖后台重试。
- 不提交人设正文、密钥、真实聊天/记忆、本机绝对路径、日志。
