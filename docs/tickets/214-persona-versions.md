# 工单 214：人设文件自动留版本，随时还原

## 目标
lan 想改 `persona/peter.md` 里一两句话时，不用先手动备份。每次 Peter 启动，只要人设内容是新的，程序就自动留一份带时间的副本；想回到以前的样子，双击一个文件，选编号即可。

## 现状
- 人设文件 `persona/peter.md` 在 lan 本机，被 `.gitignore` 忽略，不入库；Peter 启动时读一次（`load_character`），所以改完要重开。
- 之前只有 `undo-voice-rules.cmd`（工单 212），只能去掉那一块规则，不能还原其他改动。

## 做法
1. `persona_versions.py`：`load_character` 读到非空人设后调用 `snapshot()`。内容和已有任一副本相同就不存；不同就存成 `persona/versions/peter.<UTC 时间>.md`（同一秒重复则加 `-1`、`-2`）。文件问题只会跳过备份，不会让 Peter 启动失败。
2. `restore-persona.cmd`（及 `.ps1`）：列出副本（编号 1 最新）；输入编号后，先把当前人设另存（如果它还不在副本里），再换成所选版本。不碰 `data/`、聊天历史和记忆。
3. `persona/versions/` 加入 `.gitignore`；`tests/test_persona_versions.py`（假人设）。

## 验收（lan 在 Windows 上做）
1. `update-main.cmd` 更新，完全关闭并重开 Peter。打开 `persona\versions\`，里面有一个 `peter.<日期时间>.md`，内容和现在的人设一样。
2. 随便在 `persona\peter.md` 里改一句话（比如末尾加一行「测试」），保存，再关闭重开 Peter。`versions` 里多出第二个文件，里面带「测试」。
3. 双击 `restore-persona.cmd`，输入较早那个的编号（不带「测试」的）回车。人设里的「测试」消失；窗口提示重开 Peter。再看一次 `versions`，没有丢任何版本。
4. 在 `peter.md` 再加一行未重开就还原一次：还原前那份改动也被另存了。
