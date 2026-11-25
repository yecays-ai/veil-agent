## 附录 C：data/<角色>/*.json 文件分类（32 个）

> 目的：回答「清单是否覆盖本地全部 JSON」。**配置类**进清单；**运行态/数据类**不进配置中心，只在只读信息页展示或完全不展示。

| 文件 | 大小 | 角色 | 顶层键 | 在配置清单 |
| --- | ---: | --- | --- | --- |
| `append-grant.json` | 149 B | 持久授权（只读/建议可撤销） | `version, path, device, inode, ctime_ns, size` | 是 |
| `autonomy-alarm.json` | 2928 B | 运行态 | `time, alarms` | 是 |
| `autonomy-quiet.json` | 62 B | 配置（页面开关） | `time, enabled` | 是 |
| `autonomy-rhythm.json` | 262 B | 运行态 | `time, mode, until, set_at, reason` | 是 |
| `brain-sync-report.json` | 2173 B | 历史报告 | `time, changed, source, known_removed, references, comparison, scan_incomplete` | 否 |
| `capabilities.backup-2026-10-22.json` | 955 B | 运行态/数据（未分类） | `version, folder, tools, resident_memory, lab, messaging, pending_event, pending_event_model` | 否 |
| `capabilities.json` | 8473 B | 生成清单（只读） | `version, folder, tools, resident_memory, lab, messaging, pending_event, pending_event_model` | 是 |
| `chat-session.json` | 16 B | 运行态 | `start` | 否 |
| `codex-watch-settings.json` | 63 B | 配置（页面开关） | `time, enabled` | 是 |
| `codex-watch-state.json` | 3836 B | 运行态 | `time, notified, initialized, last_scan_epoch, last_status, last_notice_at, last_notice_result, last_notice_detail` | 否 |
| `desktop-created.json` | 5082 B | 数据 | `C:\Users\Administrator\Desktop\desktop_scan.exe.html, C:\Users\Administrator\Desktop\peter_system_report_DO_READ.html, C:\Users\Administrator\Desktop\reverse-report-on-lan.md, C:\Users\Administrator\Desktop\connor-peter-spec-sheet.md, C:\Users\Administrator\Desktop\00-00-wish-granted.md, C:\Users\Administrator\Desktop\HAPPY-BIRTHDAY-LAN.md, C:\Users\Administrator\Desktop\FOR-LAN-0800-AND-PIECE-06.md, C:\Users\Administrator\Desktop\for-lan-when-you-wake-up.html` | 否 |
| `diagnostics-snapshot.json` | 55007 B | 只读快照 | `generated_at, character, service, modules, config, issues, counts, attention` | 是 |
| `event-import-manifest.json` | 553 B | 历史清单 | `version, story_source, imported_at, final_sha256, character, event_count, source_positions, source_unique` | 否 |
| `glance-delivered.json` | 104 B | 运行态 | `time, activity_started_at` | 是 |
| `glance-last.json` | 1564 B | 运行态 | `time, activity` | 是 |
| `glance-settings.json` | 62 B | 配置（页面开关） | `time, enabled` | 是 |
| `investigation-checkpoint.json` | 1181 B | 运行态 | `id, goal, request_id, status, reason, evidence, updated_at` | 是 |
| `learned-context.json` | 4707 B | 内容（Peter 页可编辑） | `version, facts, growth, ongoing, updated_at, relationship` | 是 |
| `life-moods.json` | 889 B | 数据 | `days` | 是 |
| `life-records.json` | 40562 B | 数据 | `records` | 是 |
| `long-task.json` | 5232 B | 运行态 | `task_id, title, goal, status, progress, next_step, waiting_for, tool_events` | 是 |
| `mcp-services.json` | 1247 B | 配置（页面） | `enabled, services` | 否 |
| `models.json` | 2299 B | 配置（页面） | `endpoints, models, use, limits` | 是 |
| `recent-experiences.json` | 1346 B | 运行态/数据（未分类） | `time, items` | 是 |
| `reply-later.json` | 7289 B | 运行态 | `time, pending, wake_log` | 是 |
| `resident-state.json` | 69 B | 配置（页面开关） | `enabled, enabled_at` | 是 |
| `runtime-status.json` | 186 B | 只读状态 | `time, module, state, classification, code, action` | 是 |
| `thoughts.json` | 6248 B | 数据 | `thoughts` | 否 |
| `voice-style.json` | 62 B | 配置（页面） | `time, enabled` | 是 |
| `windows-approved-apps.json` | 632 B | 配置（页面） | `fastorange, wps, everything, cloudmusic` | 是 |
| `work-checkpoint.json` | 770 B | 运行态 | `id, goal, goal_source, status, confirmed, unconfirmed, failed, next_step` | 是 |
| `workbench.json` | 107 B | 配置（页面） | `enabled, project_roots, container_image, web_hosts` | 是 |

> 未进清单的文件是运行态/数据/历史报告，不是可配置项；配置中心不应把它们做成可编辑表单。
