# 运行时与热更新（泵 / overlay / 常驻 python）

适用：`inject-main.cjs` `overlay.js` `zusage.py` `usage_mcp.py`

## 泵的职责边界

- 触发式拉取：`fs.watch` 监听 db 目录（`db.sqlite` / `-wal` / `-shm`），去抖 300ms；
  限频 `activity_min_ms`（默认 1500ms）到点自动补拉；空闲时零进程零轮询，
  仅兜底心跳 `heartbeat_ms`（默认 30s，fs.watch 事件不保证送达）。
- 常驻 python（`zusage.py serve`，stdin/stdout 行协议）：意外退出自动重启；
  30 秒内连续 3 次判不稳定 → 回退一次性 spawn；单次查询 15 秒超时。
- 多窗口 / 分屏：每窗口焦点会话经 IPC `zcode:sync-active-task-session` 记录，
  推送时按窗口注入 `mine`；`__zusageWantSids` 收集每个 pane 的会话。

## 热更新面（MUST 同步维护）

| 文件 | 生效方式 |
|---|---|
| `overlay.js` | 泵 stat mtime → 移除旧条 + 重新 `executeJavaScript`（载入前 `new Function` 语法校验） |
| `zusage.py` / `zquota.py` | 常驻进程 mtime 变化 → kill 重启（`PY_HOT` 列表） |
| `inject-main.cjs` | **冷文件**：必须重启客户端（重启标记会让钩子提醒） |

- 新增"被常驻 python import 的模块"必须加进 `PY_HOT`，否则改代码后行为不变（静默失效）。
- 新增被页面注入的脚本：先校验语法再注入；注入顺序有依赖时在注释里写明。

## 启动回执

- loader 启动时计算冷文件 sha256 + `_loadedAt`（进程启动时刻），调
  `runtime_lock.py --ack-restart`。这是重启标记唯一的清除入口。
- 回执失败只打日志，绝不影响状态条。

## 客户端边界

- 只读本地 SQLite（`mode=ro`）；除额度查询外不出网。
- 任何失败只 `console.error` 留痕，不弹窗、不阻断窗口。
- `diag-<n>.json` 每 ~15 次拉取写一次（多窗口互不覆盖），是"注入已加载"的证据链。
