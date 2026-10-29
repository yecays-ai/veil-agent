# 工单 168：Lab 游戏成绩与桌面图片读取

## 目标

Lan 玩完 Peter 制作的 Lab HTML 游戏后，成绩和结局自动保存，Peter 后续可直接读取；Peter 也能主动查看已授权桌面目录内的 JPG/PNG 图片。

## 当前状态

本单从 `main` 的 `5d240da` 建立独立 worktree。原定编号 167 已被本机 `ticket/167-lab-python-sandbox` 使用，Lan 指定本单改为 168。主线本地提交与 GitHub PR #215 合并提交一致，但本机 GitHub DNS 失败，`git pull --ff-only` 未成功。

复用调查：MCP [filesystem `read_media_file`](https://github.com/modelcontextprotocol/servers/blob/main/src/filesystem/server.ts) 的媒体内容格式可借鉴，现有 VEIL 桌面路径权限和视觉消息链路更适合直接扩展，不引入第二个文件服务；MCP [图片内容规范](https://modelcontextprotocol.io/specification/2025-06-18/server/tools#image-content)确认图像应以带 MIME 的内容进入模型。2048 [本地成绩管理](https://github.com/gabrielecirulli/2048/blob/master/js/local_storage_manager.js)（MIT）仅借鉴“完成时保存、刷新后仍在”的思路；浏览器 localStorage 不能让 Peter 从 Lab 文件读取，因此成绩由现有本地服务写入游戏目录。页面提交和失败处理参考 [MDN Fetch JSON](https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API/Using_Fetch)。以上均未复制上游代码，未新增依赖或保存成绩的模型调用。

实现于 `lab_games.py`、`web_agent.py`、`tools_agent.py`、`desktop_files.py`，并复用 `windows_observer.observation_image_message` 的视觉消息入口。游戏页面在自有 Lab 子目录中，由 `open_lab_game` 打开常驻本地服务链接；游戏代码在一局结束时调用 `window.veilGame.saveResult({round_id, score, duration_ms, ending, ended_at})`，其中 `round_id` 应在每局开始生成并在重试时保持不变。服务只接收固定字段，游戏 ID 由入口文件绑定，成绩追加在同目录 `scores.jsonl`；重复局次返回已保存。页面浮层显示服务确认或失败。图片工具仅在 Peter 指定路径时读取，限 5 MB、2500 万像素及现有 Desktop 权限范围。

本地完整 Python 测试 781 项：770 项通过、11 项跳过（允许本机回环连接后运行）；游戏 HTTP 入口、成绩保存、重复局与跨来源拒绝的实连用例通过。CI 和 Windows 实机体验待验收。

## 验收步骤

1. Peter 在 `lab/<游戏>/index.html` 制作两局可玩的游戏，在结局代码调用 `veilGame.saveResult`。Peter 用 `open_lab_game` 打开，Lan 玩两局，确认每次页面显示“成绩已保存”；关闭页面、重启 VEIL 后让 Peter 读取同目录 `scores.jsonl`，准确复述两局分数与结局。
2. 模拟保存失败、同一 `round_id` 重试和非法数据，确认页面提示失败、重复局不增加记录，其他 Lab 文件不变。
3. 把 JPG、PNG 放进桌面已授权目录，让 Peter 自己列出并查看，描述实际画面；再尝试未授权路径、敏感文件与链接，确认拒绝且原有桌面工具、聊天、预算和自主唤醒正常。
