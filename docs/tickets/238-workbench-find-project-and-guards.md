# 工单 238：工作台自己找项目，加快照、权限分级和密钥代理

接在工单 237（工作台原型，同一分支，237 尚未合并）之后。lan 的要求：Peter 自己找项目，不要她给目录或看命令；改之前保留退路；联网/安装/推送先问她；密钥不能被命令读到；优先用 OpenCode 自带机制，不自研整套安全系统；先完成安全和隔离验证，再做真实项目体验验收。

## 做了什么
- **自己找项目**（`workbench_scope.py`）：只在 `[executor] project_roots` 列出的文件夹里找（默认空=只用工作台自己的文件夹，也就是只限隔离测试项目）。按任务文字或 Peter 填的 `project` 名字匹配；唯一就用，多个就让 Peter 按名字问 lan，没有就用工作台文件夹做新东西。
- **快照**：有 Git 时在 `refs/veil-snapshots/<id>` 记一个引用（不动分支、暂存区、工作区、stash，不提交），未跟踪文件另存一份；没有 Git 时复制文件（上限 200 MB / 2 万个文件）。留不下退路时，聊天里问 lan，醒来里不做。`restore()` 可回滚。
- **权限分级**：OpenCode 自己的 allow/ask/deny 规则放行 `git status/diff/log`、`ls`、跑测试等；读 `.env`、`secrets`、`.ssh`、浏览器密码库直接 deny；其余 ask 交给 VEIL。VEIL 对 `git push/clone/pull/fetch`、`pip/npm install`、`curl/wget`、递归删除，聊天里问 lan，醒来里拒绝并让 Peter 存进度。
- **密钥代理**：VEIL 在本机回环开一个转发，OpenCode 和它跑的命令只拿到本次随机令牌，真实密钥只在 VEIL 进程里；令牌只对这一次有效，转发次数有上限。

- **容器隔离（可选，`[executor] container_image = "veil-workbench"`）**：OpenCode 在 Docker 容器里跑，只挂载项目文件夹；项目里 `.env`、`*.pem`、`id_rsa*` 这类文件用空文件盖住（只读）；不带宿主机环境变量，去掉所有权限，限内存和进程数。镜像见 `docker/workbench.Dockerfile`。配置是内联传给 OpenCode 的，不往项目里写文件，项目自己的 `opencode.json` 也松不开权限。

## 没做、也不能说已经解决的
- **命令越权**：不用容器时，命令里的路径没法被程序完全解析，文件夹内批准的命令能读写文件夹外的文件（已用脚本实测复现：`验证脚本 verify_isolation.py` 的「没有容器」一栏）。容器方式是否真的挡住，**要在 lan 的 Windows + Docker Desktop 上跑 `run-isolation.cmd` 验证；验证通过之前不开放真实项目**。
- 快照还原后，用户原来的「已暂存」状态不保留（文件内容保留）。
- 项目名不出现在任务里时，Peter 要自己填 `project`；还没有「列出所有项目」的能力。

## 测试
`tests/test_executor_opencode.py`：22 项（含真实 OpenCode 经密钥代理的集成测试，装了 OpenCode 才跑）。
