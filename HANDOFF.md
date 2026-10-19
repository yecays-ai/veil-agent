# 交接

新窗口按顺序读 `AGENTS.md` → `STATUS.md` → 本文件。

## 上一位停在哪

- 2026-10-19，Codex 在 `ticket/005-auto-memory`，基于远程工单提交 `22a41ed36ff522762c965119ec1f6f7e52f01c7e`。对应 main 已含工单 004（`415410fe46212756811c73a13ca16f0ed241fb4d`）。
- Windows Python 3.11/3.12 的指定 Ombre 安装、stdio 启动、写入、重启、隐藏/恢复通过；预热后的整句召回仅 2/11，且 breath_search 排除 feel 桶。**触发工单“不达标就停下在 PR 里说明”，未接入产品代码。**
- 证据见 `docs/ombre-005-compatibility.md`；可复跑测试 `tests/test_ombre_compatibility.py`。本 PR 只改测试和文档，不改上游代码、主程序、依赖清单、安装入口和预算。
- 原有 30 项离线测试通过；默认套件跳过可选 Ombre 测试。启用真实依赖后，该测试暴露召回失败；CI 绿色不能说 005 通过。
- 无付费请求、真实聊天/长期记忆验收。后台补记、重试状态、注入、角色记忆隔离、管理命令均未实现。

## 下一个窗口第一步

评审方 Claude：先看证据和草稿 PR，不要按已完成工单合并。由 lan 决定是否评估向量或调整无向量方案，同时明确情感记忆的原生检索入口。方案确认后仍在同一工单分支继续实现并复测。

## 背景和注意

- 002 / 003 已合并且 lan 已验收；004 已合并（PR #4），待真实聊天验收。`build_self_description()` 仍如实说明没有长期记忆，不应提前改成已有。
- 上游固定提交 `115d831d979bf67efc7150f976e2cb34c333c935`。不用旧 VEIL 代码，不动 `yecays-ai/veil`。
- hold(feel=True) 需要 source_bucket，返回 feel_... 编号；breath_search 随机联想区不是检索命中，只应信结构化命中 ID，且没有逐条相关度。
- 原生隐藏 trace(dont_surface=1)，恢复 trace(dont_surface=0) 已验证；不直接删文件，不用完整编号特殊路径替代自动隐藏验证。
- 依赖实验只在忽略的 `.local-tmp/`，未纳入安装入口。项目仍在 E 盘；真实 .env、persona、data 不动、不提交。
- 本地 `characters/peter/character.toml` 的 thinking="enabled" 是 lan 的试验设置，留在工作区，不纳入 PR。
- Git 直连可能重置连接；可用 GitHub 接口核对固定 SHA 和源码树。发布前重查远程头，不能覆盖别人提交。
- 实现方 Codex 开 PR，评审方 Claude 评审并决定何时合并；当前门槛未过，保留草稿、不合并。
