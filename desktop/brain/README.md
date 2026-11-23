# VEIL Brain · 记忆中心（只读原型）

这是 P4 的复用原型：在 VEIL 桌面 App 里开一个独立窗口，直接读 `brain.db`，用来验证
「开源记忆管理界面能不能用、还缺什么」。本轮不写入、不接 Peter 的聊天/记忆保存、不接 P1 检索。

## 运行

```bash
# 1) 单独跑服务（不启动桌面 App）
node desktop/brain/server.cjs \
  --db <agent root>/.local-tmp/brain-p0a/brain.db \
  --port 8799

# 2) 桌面 App 内打开
#    右键 Peter → 菜单 →「记忆中心（原型）」
```

`brain.db` 默认取自 `<agent root>/.local-tmp/brain-p0a/brain.db`，可用 `VEIL_BRAIN_DB` 覆盖。
文件不存在时窗口不会打开，只弹提示，不会创建或修改任何数据。

开发验证用两个环境变量（正常用户不需要）：

- `VEIL_BRAIN_AUTOOPEN=1`：启动桌面 App 后自动打开记忆中心（用于冒烟）。
- `VEIL_BRAIN_SHOT=<png 路径>`：窗口打开后截一张图再继续。

## 结构

| 文件 | 作用 |
|---|---|
| `server.cjs` | 静态资源 + 只读兼容 API；`DatabaseSync(..., { readOnly: true })` |
| `ui/` | vendored mnemory 前端（Apache-2.0，见 `LICENSE-mnemory.txt`） |
| `tools/patch-ui.py` | 对上游 `index.html` / `app.js` / `memories.js` / `fsck.js` 的可重放中文与深链补丁 |
| `tools/shot.cjs` | 用 Electron 引擎截图的开发工具（可选） |

接口（全部只读）：

- `GET /api/whoami`、`GET /api/stats`、`GET /api/categories`
- `GET /api/memories/browse`（分页、类型、分类、时间范围、排序）
- `GET /api/memories/:id/history`、`GET /api/memories/:id/links`
- `POST /api/memories/search`、`POST /api/memories/find`（本地关键词，不是 P1 语义检索）
- `POST /api/fsck`、`POST /api/fsck/auto-run`、`GET /api/fsck/:id`（本地确定性检查）
- 未带 token 的 `/api/` 请求返回 `401`；已认证的未知 `/api/` 路径和所有写操作返回 `403`；非 `/api/`、非 `/ui/` 路径返回 `404`。

## 数据映射

- `memories.body` → 详情正文；`title` 放正文首行。
- `bucket` → 类型；`domain_json` → 分类；`importance` → 重要度。
- `is_hidden` / `is_tombstone` / `is_deleted` / `sidecar_state.locked` → 状态。
- `versions` → 版本历史（旧正文、原因、时间）。
- `sources`、`relations`、`events` → 来源、关联与事件。
- 健康检查：重复标题/正文、事件来源断链、墓碑仍带向量、超长记忆、隐藏记忆。语义重复/矛盾不在本轮。

## 安全

- 服务只绑 `127.0.0.1`，端口随机；`/ui/` 与 `/api/` 需要进程级随机 token（首次 URL → HttpOnly cookie，随后从地址栏移除）。
- 窗口只允许导航到该本地服务 origin；`nodeIntegration` 关闭，`sandbox` 打开。
- 所有写接口返回 `403`；前端把编辑/删除/修复按钮隐藏（`ui/js/veil-readonly.js`）。
- 不读取正式记忆 API，不改 `brain.db`，不影响 Peter 聊天和保存。

## 更新上游

```bash
# 用新版本替换 ui/ 后，重跑补丁；补丁里的 replace_once 会在上游结构变化时报错
python desktop/brain/tools/patch-ui.py
```

## 已知缺口（留给下一阶段）

1. 召回诊断（候选 → 判官 → 最终注入及原因）没有任何候选提供，需要 VEIL 自己补 UI，数据来自 P1/召回日志。
2. 语义搜索、语义重复/矛盾检查需要 P1 检索，当前只做关键词和确定性检查。
3. 版本历史的时间顺序、diff 字段名还需按 brain.db 的真实语义再校准。
4. 窗口视觉是 mnemory 深色控制台风格，和 VEIL 浅色主界面不统一；是否继续适配由 Lan 体验后决定。
