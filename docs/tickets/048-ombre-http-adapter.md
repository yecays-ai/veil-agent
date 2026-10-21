# 工单 048：实验 017 适配 Ombre HTTP 接口

## 目标

开发者可以在隔离的 Peter/Ghost 虚构数据上继续运行实验 017，并得到与原实验相同含义的记忆与盲测结果。

## 当前状态

031-11 已确认 `experiments/memory-quality-017/run_experiment.py` 仍存在，但其 Ombre 入口导入已删除的 `ombre_config`，并用旧 stdio 参数构造 `MCP`；当前 `memory_agent.MCP` 接受 token 鉴权的 Streamable HTTP URL。复用仓库 `ombre_resident.start_or_connect()` 的隔离启动和 `memory_agent.MCP` 客户端，不复制 Ombre/MCP 实现。回归仅用临时 vault、虚构 token 和假连接器，不读取 `.env`、不发真实模型请求；完整质量复跑仍经过原本地预算代理并会产生真实费用。

## 验收步骤

1. 在仓库根目录运行 `python -m unittest discover -s tests -p test_experiment_017_ombre_http.py`，确认 HTTP 地址/token 传递正确，模拟连接失败时隔离进程会关闭。
2. 安装固定 Ombre 依赖后运行 `python experiments/memory-quality-017/run_experiment.py --preflight-ombre`，确认两个空实验 vault 的本机 HTTP/MCP 握手完成且费用账本没有付费请求。
3. 如需重新生成完整质量结果，按实验 README 的说明用虚构数据运行；结果仍按原报告字段输出，且费用由累计预算代理限制。此完整复跑会产生真实费用，不作为本工单自动回归步骤。

