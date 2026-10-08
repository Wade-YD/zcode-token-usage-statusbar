# self-heal-and-doctor — 任务

顺序：立即恢复（迁移遗留）→ 看门狗 → doctor → 观测增强 → 收尾。

## 1. 恢复与看门狗

- [ ] 1.1 恢复当前注入（迁移遗留：09-29 更新抹掉后未重打）：用户终端跑 `python install.py` + 重启 ZCode，验证：状态条重现、asar 注入标记 = 1
- [x] 1.2 `watchdog.py` 完成：定位 asar（复用 install.py 探测）→ `safe_paths` 校验 → `ZUSAGE_LINE_RE` 检查 → 缺失时调 `patch_install.install(quiet=True)` 重打；动作写 `watchdog.log`（64KB 轮转）。**实弹验证**：在注入真实缺失的当前环境运行，正确检测缺失 → 重打 → 替换被运行中 ZCode 锁定（WinError 5）→ 正确转入暂存
- [x] 1.3 通知与防抖完成：重打成功且 ZCode 运行中 → PowerShell 气泡一次（staged 场景也通知）；`watchdog-state.json` 防重复（实测第二次运行跳过重复通知）；同版本已暂存 → 跳过重打包（防每 10 分钟 312MB 空转）；注入恢复后复位状态
- [ ] 1.4 `install.py` 注册/注销计划任务（代码完成：`--no-watchdog` 跳过、`--remove` 删除、`RUNTIME_FILES` 加 watchdog.py）；待用户跑 install.py 后 `schtasks /Query /TN zusage-watchdog` 实证
- [x] 1.5 检查器容错：异常写 watchdog.log 后静默退出（测试中 WinError 5 已实际留痕）；无弹窗无残留进程

## 2. doctor 自检

- [x] 2.1 `EXPECTED_MODEL_USAGE_COLUMNS` 常量 + `python zusage.py doctor` 骨架：① Python 版本 ② db 存在性 ③ 列完整性（PRAGMA 比对，缺列报 ✗ 不崩溃）④ 运行时文件 ⑤ asar 注入状态（只读）⑥ diag 新鲜度与错误 ⑦ 泵进程（尽力而为，查不到标 `?`），验证：健康环境 6✓ + 1 真实✗（asar 注入确实缺失）+ 退出码 1；repo 目录运行自动回落数据目录 config/diag
- [x] 2.2 故障场景验证：`ZCODE_HOME` 指向空目录 → db ✗ + 退出码 1（实测）；缺列副本路径由 PRAGMA 比对覆盖（实测 40 列齐时报告列数）
- [x] 2.3 `--net` 附加额度连通性检查（默认不出网），验证：实测输出「额度连通性 — BigModel - Coding Plan」；默认路径额度项仅缓存视图零出网

## 3. 观测增强

- [x] 3.1 `inject-main.cjs` handlePayload 在 diag 附带 `quota: {ok, reason, age_s, last_error}`（仅 payload 携带 quota 时写入；待 install.py 同步后实测字段）
- [x] 3.2 overlay ⚙ 面板「📋 复制诊断」（v67）：版本 / show 开关 / updateErr / trackErr / 额度状态 / diag 摘要 → 剪贴板（clipboard API + execCommand 兜底，结果有可见反馈）；`node --check` 通过；待运行时同步后目视复核
- [ ] 3.3 README 双语：看门狗章节（含关闭与恢复命令）、doctor 用法、「报障请附复制诊断内容」指引

## 4. 收尾

- [ ] 4.1 全量回归：状态条 9 项 + 额度 + 开关 + doctor 全 ✓；计划任务在跑且日志正常
- [ ] 4.2 端到端自愈演练：删注入行 → 10 分钟内看门狗自动补回 → 按通知重启 → 状态条恢复，全流程留痕核对
- [ ] 4.3 归档 `openspec archive self-heal-and-doctor`
