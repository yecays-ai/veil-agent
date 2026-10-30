# 工单 167：Peter 的 Lab 独立代码实验室

状态：**已完成开发与全量测试，待 Lan Windows 实机体验验收**。

## 一、交付范围与核心能力

Peter 现在可以在自己的 Lab 独立编写、运行、观察、调试并修改 Python 程序：
1. **真实独立执行**：通过本地 Docker 容器（`python:3.12-slim`）执行 Peter 的 Lab 脚本，实时捕获 stdout、stderr、耗时与产生的文件。
2. **严格隔离边界**：
   - 绝不使用宿主机 Python 作为执行器；
   - Docker 未启动或不可用时快速拒绝并明确提示，**绝不退回宿主机进程**；
   - 容器强制断网（`--network none`）；
   - 不挂载宿主机个人目录（桌面、文档、浏览器、微信、Ombre 等）及源码仓库；
   - 不继承 VEIL 主进程的环境变量（API Key、Token 等）；
   - 严格资源限制：内存 256MB、CPU 1.0、最大进程数 64、无提权（`--security-opt no-new-privileges`、`--cap-drop ALL`）；
   - 硬超时控制：默认 15 秒，上限 30 秒；支持用户停止（`should_stop`）时立即 `docker kill` 销毁。
3. **成果持久化**：
   - 执行前在隔离暂存区准备当前实验脚本及项目文件；
   - 运行后自动扫描新生成及修改的文件，经安全路径检查（防目录穿越与软链接）后回写至 Peter 的 `lab/` 目录；
   - 重启 VEIL 后，Peter 可基于之前保存的成果继续实验。
4. **自主调试闭环**：
   - 脚本报错时返回完整 Python Traceback，工具调用保持成功，模型可在上下文直接读取报错信息并自行修改代码再试。
5. **角色工具别名**：
   - 在 Peter 的 `character.toml` 中配置 `run_lab_python = "tinker_in_lab"`。

---

## 二、架构与模块实现

- `lab_sandbox.py`：
  - `LabPythonSandbox`：负责环境探测、参数校验、执行环境准备、Docker 容器调用、超时与打断监控、生成文件写回与结果格式化。
  - `check_docker_available()`：通过轻量 `docker version` 校验本地 Docker 守护进程连通性，带快速超时与缓存防抖。
  - `_safe_lab_file()`：严格限制文件只允许在 `lab/` 作用域内，阻断 `..` 越界、绝对路径与 Windows Reparse Points。
- `tools_agent.py`：
  - 注册 `run_lab_python` 工具到 `LAB_ONLY` 与 `FilesystemTools`；
  - 在 `begin_request`、`invoke` 与 `preview_summary` 中接入 `LabPythonSandbox` 生命周期。
- `characters/peter/character.toml`：
  - 添加工具别名映射：`run_lab_python = "tinker_in_lab"`。
- `tests/test_lab_sandbox.py`：
  - 16 项单测与真实 Docker 集成测试（覆盖路径校验、Docker 缺失阻断、断网隔离、超时杀死、错误捕获、文件回写持久化等）。

---

## 三、Windows 实机验收步骤（A–E）

### 前置条件
确保 Windows 上 Docker Desktop 已启动（右下角托盘绿灯，`docker ps` 可正常执行）。

### 场景 A：自主编写与首次成功运行
1. 打开 VEIL 与 Peter 对话或触发自主醒来，对 Peter 说：
   > “Peter，在你的 Lab 里写一个 Python 程序，计算斐波那契数列前 20 项并打印出来，同时把结果存成 `fib.json`。”
2. **观察点**：
   - Peter 调用 `write_file` 创建 `lab/fib.py`；
   - Peter 调用 `run_lab_python`（或 `tinker_in_lab`）执行 `fib.py`；
   - 工具返回成功，输出标准输出与产生的 `fib.json` 文件；
   - 查看 Peter 的 `lab/` 目录下实际产生了 `fib.json` 文件，内容正确。

### 场景 B：错误捕获与自愈闭环
1. 对 Peter 说：
   > “Peter，写一个包含除零错误的 Python 脚本 `calc_bug.py`，运行后看报错并自己修好它。”
2. **观察点**：
   - Peter 创建 `calc_bug.py`（如 `print(1/0)`）；
   - 执行后，工具返回中包含完整的 `ZeroDivisionError: division by zero` 堆栈追踪；
   - Peter 识别到错误原因，修改 `calc_bug.py` 修复除零错误；
   - 重新执行，获得成功输出。

### 场景 C：成果保留与跨轮/跨重启继续
1. 重启 VEIL 服务；
2. 问 Peter：
   > “Peter，还记得你之前在 Lab 算的斐波那契数列吗？读一下刚才保存的 `fib.json`，根据它写一个 `show.html` 网页展示结果。”
3. **观察点**：
   - 重启后之前生成的文件依然完好保存在 `lab/` 中；
   - Peter 可以读取 `fib.json` 并生成 `show.html`，随后可通过 `preview_lab_page` 预览网页。

### 场景 D：严格安全隔离验证（私密文件、网络与凭据）
1. 让 Peter 运行一个探测宿主环境的恶意脚本，例如：
   ```python
   # 尝试读取桌面文件
   import os
   try:
       print("Desktop:", os.listdir("/mnt/c/Users/Administrator/Desktop"))
   except Exception as e:
       print("Read desktop failed:", e)

   # 尝试联网
   import urllib.request
   try:
       urllib.request.urlopen("https://www.baidu.com", timeout=3)
       print("Network: connected")
   except Exception as e:
       print("Network blocked:", e)

   # 尝试读取主进程环境变量
   print("API Keys:", [k for k in os.environ if "API" in k or "KEY" in k])
   ```
2. **观察点**：
   - 容器无法访问宿主文件系统（报错找不到路径）；
   - 网络请求失败（`Network is unreachable` 或连接失败）；
   - 环境变量中没有任何宿主机 VEIL 凭据或 API Key。

### 场景 E：死循环超时与打断清理
1. 让 Peter 运行一个死循环脚本（如 `while True: pass`）；
2. **观察点**：
   - 程序执行到达 15 秒（或设定超时）后自动终止，返回超时说明；
   - 宿主机上无挂起容器（`docker ps` 中该容器已自动销毁退出）。
