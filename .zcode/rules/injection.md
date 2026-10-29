# 注入与重启标记（asar 路径）

适用：`install.py` `patch_install.py` `install_monitor.py` `watchdog.py` `runtime_lock.py`

## 唯一注入行

- 注入 = 在 asar 入口 `out/main/index.js` 尾部追加一行 dynamic `import()`；
  入口是 ESM，loader 必须是 `.cjs`，路径用绝对 `file://` URL。
- 卸载 = 用 `ZUSAGE_LINE_RE` 剥离任意目录的历史注入行，不依赖 `.bak`，
  不影响其它工具后装的注入行。
- 禁止：制作/恢复 `app.asar.zusage.bak`（历史机制已退役，遗留文件会被自动清理）。

## 替换时序（MUST）

1. `repack()` 写入同目录 `app.asar.zusage.tmp`（mkstemp + 原子改名）。
2. 结构自检 + 语法自检（用 ZCode 自身 `ELECTRON_RUN_AS_NODE --check`）通过才继续。
3. 记录重启意图（`_begin_install_intent`）→ 再 `os.replace` 发布。
4. 运行中替换失败（asar 被锁）→ **保留 TMP**，由监控窗 / 看门狗在客户端退出后收尾；
   绝不"删除 TMP 当失败"。

## 重启标记生命周期（P0 语义）

```
同步/注入改冷文件 → record_restart(hashes, state) → 客户端启动 → inject-main 上报
实际加载哈希 → ack_restart 只清除哈希一致的条目 → 标记消失
```

- **任何成功 rename TMP→ASAR 的路径都必须盖章**：`install()` 用
  `publish_install(...)` 上下文，收尾路径（`watchdog.py` / `install_monitor.py`
  直接 rename）必须调用 `patch_install.publish_staged()`。
  漏盖章的后果是"重启提醒永远消不掉"——这是 P0 回归。
- `pending-install` 意图没有 `publishedAt` 时，`ack_restart` 必须拒绝清除
  （替换可能还躺在 TMP 里，重启也加载不到新代码）。
- 进程 `_loadedAt` 早于标记 `createdAt` 时不得清除（旧进程不能替新标记背书）。
- 锁文件 `plugin-restart.lock` **永不 unlink**；所有标记读写必须持锁。

## 冷文件 vs 热文件

- 冷文件：`inject-main.cjs`——只在 asar 加载时读一次，换新必须重启。
- 热文件：`overlay.js`（泵 mtime 热注入）、`zusage.py` + `zquota.py`（常驻进程热重启）。
- 只对冷文件记重启意图；给热文件记提醒是噪音。
