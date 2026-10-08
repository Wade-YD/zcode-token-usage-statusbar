# self-heal-and-doctor — 设计

## Context

- 两次实测：ZCode 自动更新（09-22 18:47、09-29 11:21）覆盖 asar 抹掉注入行，第二次隔 9 天才被发现。上游 ZCode 的更新行为是常态，不会停止。
- 现有资产：`patch_install.py` 已具备幂等注入/剥离与结构自检（`ZUSAGE_LINE_RE`、`self_check`、原子替换）；diag 由 loader 从 `window.__zusageDiag` 回写；额度故障语义已在 `zquota.py` 结构化（reason/age_s/last_error）。
- 零常驻哲学：上游没有任何周期性后台进程，本 change 引入的周期性必须落在**操作系统计划任务**（不运行时零进程），不得新建守护进程。

## Goals / Non-Goals

**Goals:**
- 注入缺失 → 10 分钟内自动修复 + 一次通知；doctor 一条命令输出全链路健康度
- 通知防抖（同问题只弹一次）、检查器异常静默留痕
- 全部复用现有组件（patch_install / diag / zquota），不新造轮子

**Non-Goals:**
- 不做常驻监控进程；不做跨平台计划任务（先 Windows，schtasks）
- doctor 不做 `--fix` 自动修复（修复入口收敛：看门狗自动修、手动走 install.py）
- 不改注入机制、serve 协议、额度查询逻辑

## Decisions

**D1 看门狗载体：Windows 计划任务 + `watchdog.py`**
`schtasks /Create /TN zusage-watchdog /SC MINUTE /MO 10 /TR "<python> <runtime>/watchdog.py"`，用户级、无需管理员。理由：注入自愈必须独立于 ZCode 进程（被诊断对象不能依赖自身）；常驻进程违背零常驻哲学。`watchdog.py` 进 RUNTIME_FILES 随安装分发。
备选「常驻 python 看门狗」被否：与上游零常驻设计冲突；「WMI 永久订阅」过重。

**D2 修复时机：运行中也原子替换，收尾交给自身下周期**
ZCode 运行中 `os.replace` 原子替换通常成功（运行中进程仍读旧数据）；失败（文件锁）则留下 `.tmp`，看门狗下个周期（ZCode 已退出时）重试收尾——**不弹 install_monitor 窗口**（无人值守场景弹窗是骚扰）。每次动作结果写通知/日志。

**D3 通知：PowerShell Forms 气泡 + 状态文件防抖**
通知用 `powershell -NoProfile` 的 `System.Windows.Forms.NotifyIcon` 气泡（无需第三方模块）；防抖靠运行时目录 `watchdog-state.json`：记录 `{notified_at, reason}`，同状态不重复通知；注入恢复（diag 确认加载）后状态复位。

**D4 doctor 检查清单（七项，全部只读）**
① Python ≥3.10；② db 存在 + `PRAGMA table_info(model_usage)` 与期望列清单（`status/output_tokens/reasoning_tokens/duration_ms/first_token_at/completed_at/…`）比对；③ 运行时目录五件套齐全；④ asar 主入口含注入行（`ZUSAGE_LINE_RE` 只读检查，复用 patch_install 的解析函数）；⑤ diag mtime 新鲜度（<10 分钟为健康，空闲期为弱信号不判 ✗）与 updateErr/trackErr；⑥ 泵进程存在（`Get-CimInstance`/`pgrep` 尽力而为，查不到标 `?` 不判 ✗）；⑦ 额度模块配置状态（`--net` 才发一次真实查询）。缺列时的降级说明直接引用 speed-metrics 规范的容错语义。

**D5 期望列清单的维护位置**：`zusage.py` 顶部常量 `EXPECTED_MODEL_USAGE_COLUMNS`，doctor 与未来查询容错共用同一清单，避免两处漂移。

**D6 通知与日志的落点**：所有看门狗动作写运行时目录 `watchdog.log`（带轮转，与既有日志一致的风格）；通知是「用户可见」通道，日志是「可回查」通道，二者并行。

## Risks / Trade-offs

- [计划任务被用户/安全软件禁用 → 自愈失效] → doctor 第④项独立可查；README 说明恢复命令 `schtasks /Create ...`
- [PowerShell 气泡在部分环境被策略拦截] → 通知失败降级为仅写 watchdog.log，不影响修复本身
- [运行中原子替换偶发失败留下 .tmp] → 看门狗下周期收尾（设计如此），且 .tmp 不影响运行中的 ZCode
- [诊断自身依赖注入链路] → 检查器不 import 运行时组件、不读 diag 之外的运行时状态；doctor 对每一项独立容错
- [每 10 分钟跑一次 python 的启动开销] → 检查器 <50ms（读 asar 头 + 状态文件），且完全空闲期同样运行（它是唯一不看 db 的周期任务）；可 `--no-watchdog` 退出

## Migration Plan

1. 实现看门狗 + 注册/注销 → 手动 `schtasks /Run` 触发验证（含「删注入行→自动补回」的正反用例）
2. 实现 doctor → 健康环境全 ✓；`ZCODE_HOME` 指向空目录模拟缺 db / 改造副本 asar 模拟缺注入
3. diag 增强 + 复制诊断 → 目视 + diag 字段核对
4. 全量回归（额度/速度/开关不回归）→ 归档

**回滚**：`--no-watchdog` 重装（不注册任务）或 `schtasks /Delete`；doctor 为纯增量代码，回滚即删除子命令。

## Open Questions

- 通知文案是否需要「一键打开 install 目录」的按钮形态（Forms 气泡支持点击事件，但需常驻隐蔽图标，倾向不做）
- 看门狗周期 10 分钟是否合适（更短无意义——修复后也要重启才可见；更长则修复延迟变高）
