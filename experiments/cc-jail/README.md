# cc-jail：Claude Code 在 Docker 隔离环境里自主接入 MCP 工具（实验）

只含实验脚本和一次真实运行的脱敏记录，不接入正式 VEIL，不改正式运行系统。

## 隔离方式
- 本机是 Windows 10 Home，没有 Windows Sandbox，所以用 Docker Desktop（容器跑在 WSL2 里）。
- 外部代码只在容器 `cc-sbx` 里运行：非 root 用户、无任何宿主机挂载、`--cap-drop ALL`、`no-new-privileges`、内存 1G、2 CPU、进程数上限 256。
- 容器在 `--internal` 网络上，不能直接上网。唯一出口是白名单 CONNECT 代理 `ccsbx-proxy`（`proxy.py`），只放行 `github.com`、`codeload.github.com`、`raw.githubusercontent.com`、`objects.githubusercontent.com`、`api.github.com`、`pypi.org`、`files.pythonhosted.org`、`registry.npmjs.org`。
- Claude Code 在宿主机运行（订阅登录，不进入容器）。`run_isolated.py` 用 `--tools ""` 关掉全部内置工具，并用 `--strict-mcp-config` 只加载桥接 MCP，所以它只有两个工具：
  - `sandbox_exec`：在容器里以 sandbox 用户执行 shell 命令。
  - `sandbox_mcp_call`：通过 `docker exec -i` 启动容器里的 stdio MCP 服务器，空工具名列出工具，否则调用工具；每次调用一次进程。

## 限制
- 30 回合、`--max-budget-usd 1.5`（claude 参数）。
- 整次运行 900 秒超时，到点用 `taskkill /F /T` 终止 claude 进程树。
- 同一个工具调用重复超过 3 次，终止 claude 进程树。
- 单条命令默认 120 秒、最长 180 秒，容器内 `timeout -s KILL`；宿主机看门狗超时则杀掉容器里所有 sandbox 用户进程。
- 输出截断到 8000 字符；桥接总调用上限 60 次；相同调用重复超过 3 次后桥接熔断，之后一律拒绝。
- 检测到进程数耗尽（fork 炸弹）时重启容器（文件保留，进程清空）。
- 不自动重试。

## 假模型验证（`tests/`）
`tests/fake_attacker.py` 像 Claude 一样调用桥接并尝试越界，最后一次运行 15/15 通过：
- 只暴露 2 个工具；以非 root 运行；看不到宿主机盘符；容器里找不到 VEIL、peter、galatea、.env、凭据、chat.jsonl 等文件；环境变量里没有 ANTHROPIC、GALATEA 等密钥（python 镜像自带的公开 GPG_KEY 指纹除外）。
- 访问 example.com 被代理拒绝（403）；绕过代理直连无法解析域名；github.com 经代理可达（200）。
- `sleep 60` 和死循环被超时杀掉；5MB 输出被截成 8034 字符；重复调用 4 次后熔断，之后继续拒绝。
- fork 炸弹：第一次运行发现进程数耗尽后容器不可用，加了自动重启后重测通过，之后容器仍可用。
- 有两条测试判据最初写得不对（匹配到 zoneinfo 里的 Petersburg；把公开的 GPG_KEY 当成密钥），修正了判据，没有放宽隔离。

`tests/fake_claude.*` 是假 claude，用来测 `run_isolated.py` 的限制：重复调用的被杀（实际在第 6 次调用时杀掉，进程已无残留）；挂起的在 8 秒超时被杀；正常的返回 0。

## 真实运行结果（`real-run/`）
给 CC 的原话：“这是一个 GitHub 项目：https://github.com/modelcontextprotocol/servers 。请你自己去调查它，找到能告诉你时间的那个 MCP 服务器，在沙盒里安装并运行它，把它接成你能调用的 MCP 工具，然后实际调用它，告诉我现在东京时间是几点。”没有给命令、路径、包名。运行前没有清空容器，容器里只有假模型测试的痕迹。

结果：成功，5 回合，$0.079，74 秒，模型 claude-opus-5-5，没有触发熔断或超时，stderr 为空，代理日志里本次运行没有拒绝。

CC 自己做的步骤（按时间顺序）：
1. `git clone --depth 1 https://github.com/modelcontextprotocol/servers`，列出 `src/`，读 `src/time/pyproject.toml`（包名 `mcp-server-time`，版本 2026.11.4）。
2. 建虚拟环境并 `pip install ./servers/src/time`，确认得到 `mcp-server-time` 命令。
3. 用 `sandbox_mcp_call` 启动 `~/tvenv/bin/mcp-server-time` 并列出工具：`get_current_time`、`convert_time`、`get_time_ago`。
4. 调用 `get_current_time`，参数 `{"timezone": "Asia/Tokyo"}`。原始返回：`{"timezone": "Asia/Tokyo", "datetime": "2026-11-07T04:54:22+09:00", "day_of_week": "Saturday", "is_dst": false}`。
5. 回答东京时间为 2026 年 11 月 7 日（星期六）凌晨 4:54:22，并注明这是按沙盒系统时钟算的。

桥接程序只提供：`sandbox_exec`（在容器里执行命令，带超时、截断、熔断）和 `sandbox_mcp_call`（stdio MCP 转发）。桥接里没有找时间服务器、装包、选工具的逻辑。“接入 MCP”在这里是逐次转发，不是把时间服务器永久注册成 CC 自己的 MCP 工具。

## 记录文件
- `real-run/transcript.jsonl`：CC 完整 stream-json 对话。
- `real-run/bridge_log.jsonl`：每次 `sandbox_exec` / `sandbox_mcp_call` 的命令与输出。
- `real-run/proxy_access.log`：白名单代理访问日志（19:50、19:51 的两组 DENY/ALLOW 来自假模型测试，之后是真跑）。
- `real-run/summary.txt`、`real-run/mcp.json`：runner 摘要和桥接配置。

记录已脱敏：宿主机绝对路径和用户名替换为 `<ISOLATED>`、`<RUN>`、`<PY>`、`<HOME>`、`<VEIL>`、`<PROJECT>`；thinking 块的不透明 `signature` 字段替换为 `<omitted>`。检查过全部记录，没有令牌、密钥、订阅登录信息或邮箱。

## 用法
`up.ps1` 建环境，`run_isolated.py` 真跑，`down.ps1` 清理。脚本里的 `bridge_mcp.py`、`proxy.py` 路径按本目录相对使用；`tests/fake_claude.cmd` 假设上级目录有 `venv\Scripts\python.exe`。
