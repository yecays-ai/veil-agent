# 工单 047：依赖可复现安装与安全维护检查

## 目标

新 Windows 环境按 README 安装后，主程序和 Ombre 使用固定依赖版本；Peter 可手动或按周获得已知漏洞及修复版本报告。

## 当前状态

main 的 `requirements.txt` 固定了三项直接依赖，但传递依赖可变；`setup_memory.py` 原来在 Python 3.11 使用范围版本、3.12 才用 Ombre 上游锁文件。安装、测试和安全扫描没有共同使用主依赖锁。复用 pip 兼容 requirements 锁定格式，保持现有 `.bat` 与 pip 安装路径；复用 Ombre 固定提交的 MIT 源码和精确依赖锁，为 Python 3.11 固定兼容的 NumPy 2.4.6 与 SciPy 1.17.1；安全扫描发现风险后，将 Certifi 固定到 2026.10.19，并将 Python 3.11/3.12 Ombre 的 mcp 与 PyJWT 固定到扫描器给出的修复版本；复用 MIT 的 [pip-audit](https://github.com/pypa/pip-audit) 及其官方 GitHub Action 检查依赖，不复制包管理器或漏洞库。锁文件和漏洞报告仅包含公开依赖名/版本，不上传 `.env`、聊天或记忆。

## 验证状态

GitHub Actions Tests run 39631176073/39631189237：Windows/Linux × Python 3.11/3.12 的固定依赖安装、Ombre 安装、启动帮助与完整 `python -m unittest discover -s tests -v` 全部通过。Dependency security run 39631189197：主程序、Ombre 3.11/3.12 扫描和虚构漏洞样本报告检查全部通过。真实 Windows 用户体验尚待 Lan 验收。

## 验收步骤

1. 在新的 Windows 测试目录按 README 完成安装，确认 Python 3.11/3.12 可用、关键模块导入并能通过启动帮助检查。
2. GitHub Actions 的 Tests 在 Windows/Linux 与 Python 3.11/3.12 执行锁定安装、Ombre 安装、启动检查和完整测试；手动运行 Dependency security 工作流并查看主程序与 Ombre Python 3.11/3.12 三份依赖扫描报告。
3. 查看工作流中的虚构 `idna==2.5` 样本报告，确认包含包名、版本、风险编号和修复版本；确认生产依赖文件没有被扫描器修改。
