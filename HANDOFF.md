# 交接

新窗口或新 agent：按顺序读 `AGENTS.md` → `STATUS.md` → 本文件。

## 上一位停在哪

- 2026-10-19，Codex：在既有分支 `ticket/002-text-chat` 实现工单 002，[PR #2](https://github.com/yecays-ai/veil-agent/pull/2) 已开；尚未合并、尚未完成 lan 的真实聊天验收。
- 主程序 `veil_agent.py`；配置 `characters/peter/character.toml`；安装/启动 `setup.bat` / `start.bat`；安装与使用说明 `README.md`。
- Windows Python 3.12 离线测试 20 项通过。测试只用假人设、假数据、假模型及官方 SDK 模拟传输。启动检查确认缺失人设会停止，`--cost` 无需人设或密钥。
- 已配置 Windows/Linux × Python 3.11/3.12 CI；代码提交 `d7676c6` 的首次运行在分配 runner 前失败/取消（`runner_id=0`、零步骤、日志 BlobNotFound），远程测试未运行，原因未确认。CodeRabbit 已开始评审，完成情况须看 PR。
- 没有设置真实人设/密钥，没有调用付费接口；英文回复、翻译及真实模型重启衔接未验证。

## 下一个窗口第一步

查看此分支 PR 的最新提交、CI 和 CodeRabbit 评审，按 `pr-review` skill 处理功能/安全问题。未通过评审和 CI 不合并。交给 lan 前按 `README.md` 完成本机私有设置，再按 `STATUS.md` 体验验收。

## 背景与注意

- 旧仓库代码不使用；本工单只做文字聊天，不接入 Ombre Brain、语音、avatar 或工具。
- 角色由配置选择；历史按角色分开，费用总账和进程锁共用。真实人设、密钥、聊天、账本只留本机，禁止提交。
- 预算先预留本次上限，接口返回用量后结算；失败或中断的预留额跨重启/跨日保留。费用是配置单价核算，默认 DeepSeek 以高峰价保守计算，须定期核对价格。不要通过删账本解决预留占用。
- 程序、依赖和数据支持放在其他盘；虚拟环境移动后需重建，保留私有配置和数据。
- Git 直连在本窗口不可用，已通过 GitHub 连接读取固定提交、恢复准确的浅检出并提交；继续同步前先核对远程分支头，不能覆盖别人新提交。
