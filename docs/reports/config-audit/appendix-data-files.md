## 附录 C：data/<角色>/*.json 文件分类（32 个）

> 目的：回答「清单是否覆盖本地全部 JSON」。**配置类**进清单；**运行态/数据/历史类**不进配置中心，只显示状态或完全不展示。
> 隐私：本附录只列文件名、大小、角色；数据类文件**不展示顶层键或内容**（其中可能有本机路径和个人文件）。

| 文件 | 大小 | 角色 | 顶层键（仅配置类） | 在配置清单 |
| --- | ---: | --- | --- | --- |
| `append-grant.json` | 149 B | 持久授权（只读/建议可撤销） | `—（运行态/数据，不展示内容）` | 是 |
| `autonomy-alarm.json` | 2928 B | 运行态 | `—（运行态/数据，不展示内容）` | 是 |
| `autonomy-quiet.json` | 62 B | 配置（页面开关） | `time, enabled` | 是 |
| `autonomy-rhythm.json` | 262 B | 运行态 | `—（运行态/数据，不展示内容）` | 是 |
| `brain-sync-report.json` | 2173 B | 历史报告 | `—（运行态/数据，不展示内容）` | 否 |
| `capabilities.backup-2026-10-22.json` | 955 B | 运行态/数据（未分类） | `—（运行态/数据，不展示内容）` | 否 |
| `capabilities.json` | 8473 B | 生成清单（只读） | `—（运行态/数据，不展示内容）` | 是 |
| `chat-session.json` | 16 B | 运行态 | `—（运行态/数据，不展示内容）` | 否 |
| `codex-watch-settings.json` | 63 B | 配置（页面开关） | `time, enabled` | 是 |
| `codex-watch-state.json` | 3836 B | 运行态 | `—（运行态/数据，不展示内容）` | 否 |
| `desktop-created.json` | 5082 B | 数据 | `—（运行态/数据，不展示内容）` | 否 |
| `diagnostics-snapshot.json` | 55007 B | 只读快照 | `—（运行态/数据，不展示内容）` | 是 |
| `event-import-manifest.json` | 553 B | 历史清单 | `—（运行态/数据，不展示内容）` | 否 |
| `glance-delivered.json` | 104 B | 运行态 | `—（运行态/数据，不展示内容）` | 是 |
| `glance-last.json` | 1564 B | 运行态 | `—（运行态/数据，不展示内容）` | 是 |
| `glance-settings.json` | 62 B | 配置（页面开关） | `time, enabled` | 是 |
| `investigation-checkpoint.json` | 1181 B | 运行态 | `—（运行态/数据，不展示内容）` | 是 |
| `learned-context.json` | 4707 B | 内容（Peter 页可编辑） | `—（运行态/数据，不展示内容）` | 是 |
| `life-moods.json` | 889 B | 数据 | `—（运行态/数据，不展示内容）` | 是 |
| `life-records.json` | 40562 B | 数据 | `—（运行态/数据，不展示内容）` | 是 |
| `long-task.json` | 5232 B | 运行态 | `—（运行态/数据，不展示内容）` | 是 |
| `mcp-services.json` | 1247 B | 配置（页面） | `enabled, services` | 是 |
| `models.json` | 2299 B | 配置（页面） | `endpoints, models, use, limits` | 是 |
| `recent-experiences.json` | 1346 B | 运行态/数据（未分类） | `—（运行态/数据，不展示内容）` | 是 |
| `reply-later.json` | 7289 B | 运行态 | `—（运行态/数据，不展示内容）` | 是 |
| `resident-state.json` | 69 B | 配置（页面开关） | `enabled, enabled_at` | 是 |
| `runtime-status.json` | 186 B | 只读状态 | `—（运行态/数据，不展示内容）` | 是 |
| `thoughts.json` | 6248 B | 数据 | `—（运行态/数据，不展示内容）` | 否 |
| `voice-style.json` | 62 B | 配置（页面） | `time, enabled` | 是 |
| `windows-approved-apps.json` | 632 B | 配置（页面） | `fastorange, wps, everything, cloudmusic` | 是 |
| `work-checkpoint.json` | 770 B | 运行态 | `—（运行态/数据，不展示内容）` | 是 |
| `workbench.json` | 107 B | 配置（页面） | `enabled, project_roots, container_image, web_hosts` | 是 |

> 未进清单的文件是运行态/数据/历史报告，不是可配置项；配置中心不应把它们做成可编辑表单。
