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
3. 写待收尾包身份 `app.asar.zusage.tmp.json`：源 asar 与 TMP 的 size/mtime_ns/sha256。
4. 记录重启意图（`_begin_install_intent`）→ 再 `os.replace` 发布。
5. 运行中替换失败（asar 被锁）→ **保留 TMP + sidecar**，并立刻 `ensure_monitor(quiet=True)`
   拉起静默监控；绝不"删除 TMP 当失败"，也不等 10 分钟周期。

## 待收尾包信任链（MUST）

- 收尾（watchdog / install_monitor / install --finalize）一律走
  `finalize_pending()`：先 `verify_pending()` 再 replace。**禁止裸 `os.replace(TMP, ASAR)`**。
- `source-changed` = ZCode 又升级过：拒绝替换（旧包覆盖新版比"没状态条"更糟），
  清掉 sidecar，让下一轮按当前版本重打。
- `tmp-changed` / `no-meta` 同理拒绝；sidecar 里还校验 asar 路径与 runtime 目录，
  防止跨安装位置误用。
- `remove()` 重打包后必须 `clear_pending_meta()`：TMP 已是 remove 包，旧 install 身份作废。

## 收尾进程（install_monitor）

- **等退出用事件，不用轮询**：Windows `OpenProcess(SYNCHRONIZE)` +
  `WaitForMultipleObjects`；实测退出窗口只有几秒，10 秒轮询经常整段错过。
  非 Windows / 句柄不可用才退化为 sleep。
- 静默模式（`ZUSAGE_MONITOR_QUIET=1`）由钩子/看门狗拉起；**WMI 通道不继承环境
  变量，静默开关必须走 argv**（`install_monitor.py` 解析 `--quiet`）。
- 拉起链（quiet）：**WMI `Win32_Process.Create` 优先**（父进程 WmiPrvSE，天然不在
  调用方进程树/Job 里——宿主 Job 常拒绝 breakaway，树内监控会被整树杀，实测只活
  几分钟）→ `CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB`（被拒留 stderr 线索）
  → 普通 `CREATE_NO_WINDOW`。WMI 解释器必须是同目录 `pythonw.exe`（缺失即跳过
  WMI，否则闪控制台窗）。
- 单实例：`app.asar.zusage.monitor.lock` 排他锁（`runtime_lock.FileLock`），
  抢不到就静默退出；拉起前先试锁，已有实例则不再 spawn。
- 监控是独立进程，**必须自己 `set_target(ASAR)` / `set_runtime(RUNTIME)`**，
  不继承拉起者的全局量（不同步会表现为"永远 missing、空转"）。
- `ensure_monitor` 前置两道：`os.access(ASAR, W_OK)`（受保护目录拉监控是空转）+
  `_sweep_stale_stages()`。

## 无人值守宿主约束（MUST）

- 本工具常在无控制台宿主里跑（pythonw 计划任务、ZCode 拉起的钩子、静默监控）：
  Windows 会给 tasklist/powershell/schtasks/ssh 这类控制台程序分配**可见终端**。
  所有探测/通知类 subprocess 一律 `CREATE_NO_WINDOW`（`install.py` 的 `run_text`
  已统一，新调用点必须自带）。
- repack/元数据的 mkstemp 残留靠 `_sweep_stale_stages()` 按 600s mtime 阈值清扫
  （进程被 kill 时 finally 不执行）；只扫 `.app.asar.zusage.*` / `.zusage-meta.*`
  两个自有点前缀，600s 阈值保证不碰活跃文件。
- `install()` 在 repack 与写身份 sidecar 之间有秒级窗口，监控对 `untrusted` 宽限
  一轮再判（`install_monitor` 主循环），不要见 untrusted 就退出。

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
