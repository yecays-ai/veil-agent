# 工单 167：Lab Python 执行沙盒调查与阻塞

状态：**未交付执行能力；Windows 实际验收未开始**。不能将宿主机 Python 或无隔离后端暴露给 Peter。

## 本机检查（2026-10-30）

- `git fetch origin main` 因 `Could not resolve host: github.com` 失败；本分支基于本机 `origin/main` 的 `5d240da`，尚未证明是远端最新 main。
- 未找到 `docker` 命令；`wsl --status` 和 `wsl --list --verbose` 均提示尚未安装 WSL。没有可运行的容器后端，因此无法进行真实隔离测试。
- 现有 `tools_agent.py` 提供 Lab 路径检查、文件读写和 `preview_lab_page`，工具调用接收 `should_stop`。这些不是 Python 执行隔离。聊天与自主醒来共用工具循环、权限及预算；接入前仍需逐一验证关闭 MCP、暂停和人工打断时的执行终止。

## 开源方案核对

| 方案 | 许可证及运行条件 | 已知边界与缺口 |
| --- | --- | --- |
| [Onyx python-sandbox](https://github.com/onyx-dot-app/python-sandbox) | MIT；依赖 Docker，推荐方式挂载宿主 Docker socket，另一方式需要 privileged Docker-in-Docker；README 给出默认 128 MB 内存、5 秒 CPU、10 秒执行超时、1 MB 输出上限 | 提供容器、文件上传/下载 API，但服务拥有 Docker 控制能力，必须验证只绑定本机、无网络、无宿主挂载、进程数及磁盘限制、取消清理和文件回写检查；文档声明不能替代 Windows 实测。镜像容量需安装后测量。 |
| [mcp-python-exec-sandbox](https://github.com/lu-zhengda/mcp-python-exec-sandbox) | MIT；服务需 Python 3.13+、uv，Windows 走 Docker 后端；默认 30 秒、最高 300 秒，输出默认 100 KB | Docker 不可用时会警告并退回 `none`，直接接入有执行宿主代码的风险。Linux bubblewrap 后端允许网络。其脚本参数及自动依赖下载也不符合本单只执行 Lab 项目、默认断网、不自动安装依赖的范围；文件持久化还需适配。镜像容量需安装后测量。 |

## 继续条件和验收

1. 在 Lan 的 Windows 环境安装并启动 Docker Desktop/WSL2，记录 Docker、WSL、镜像的实际磁盘和内存占用。安装与启用属于用户操作；当前未安装，不能声称可用。
2. 选择容器隔离后端，接入前强制检测后端健康；检测失败必须拒绝执行，绝不退回宿主进程。限制容器网络、挂载、环境变量、CPU、内存、时长、进程、写盘、输出；只传选定实验文件，检查生成文件后回写 Lab。
3. 本机真实运行验收 A–E：自主编写和运行、错误修正、重启继续、阻断私人文件/网络/宿主程序/凭据、停止及资源上限。需记录每次真实输出和容器退出状态；代码审查或单测不能替代。
4. 通过后再接入独立开关、模型工具循环、停用与停止传播，更新安装说明；本分支只记录阻塞，不构成可用版本。

模型调用沿用 VEIL 现有 Token 预算；本地执行消耗电脑 CPU、内存、磁盘。没有额外模型服务。
