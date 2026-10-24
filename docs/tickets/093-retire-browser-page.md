# 093 停用浏览器网页版

## 背景

2026-10-24 架构审计发现同一套聊天界面写了两份：桌面版 `desktop/chat.js` 和浏览器页 `web/index.html`，每加一个能力（图片、语音）都要改两遍。Lan 决定：浏览器网页版停掉，只保留桌面版。

## 改动

- `web_agent.py` 不再在 `/` 提供网页，也不再打开浏览器；`/api/*` 和 `/media/*` 不变，桌面版照常使用。
- `web_launcher.py` 不再打开浏览器。两者仍接受 `--no-browser`（旧版已打包的桌面程序会传这个参数），但它已不起作用。
- 删除 `web/index.html`、`tests/web_pacing.cjs` 和只检查网页源码的测试。
- `start-web.bat` 改为调用 `start-desktop.bat`，Lan 习惯双击它也能打开桌面版。

## 不变的

Peter 的行为、聊天、记忆、权限、桌面版界面都不变。

## 验收

1. 完全退出 VEIL，`git pull --ff-only`，双击 `start-desktop.bat`（或 `start-web.bat`），桌面版正常打开、能聊天、能发图片和语音。
2. 不会再自动弹出浏览器页面。
