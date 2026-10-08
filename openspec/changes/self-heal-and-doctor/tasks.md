# self-heal-and-doctor — 任务

顺序：立即恢复（迁移遗留）→ 看门狗 → doctor → 观测增强 → 收尾。

## 1. 恢复与看门狗

- [ ] 1.1 恢复当前注入（迁移遗留：09-29 更新抹掉后未重打）：用户终端跑 `python install.py` + 重启 ZCode，验证：状态条重现、asar 注入标记 = 1
- [ ] 1.2 新增 `watchdog.py`：定位 asar（复用 install.py 的探测）→ `safe_paths` 校验 → 检查 `ZUSAGE_LINE_RE` → 缺失时调 `patch_install` 重打；动作写 `watchdog.log`（带轮转），验证：手工剥离注入行后运行检查器，注入行恢复且日志留痕
- [ ] 1.3 通知与防抖：重打成功且 ZCode 运行中 → PowerShell 气泡「重启 ZCode 生效」一次；`watchdog-state.json` 防重复；注入恢复后复位，验证：连续运行两次检查器只弹一次通知
- [ ] 1.4 `install.py` 注册/注销计划任务 `zusage-watchdog`（每 10 分钟，用户级；`--no-watchdog` 跳过；`--remove` 删除），`RUNTIME_FILES` 加 `watchdog.py`，验证：`schtasks /Query` 可查、`/Run` 触发正常、`--remove` 后任务消失
- [ ] 1.5 检查器容错：异常写日志静默退出（无弹窗无残留进程），验证：人为注入异常（临时指向不存在的 asar）后进程退出、日志有记录

## 2. doctor 自检

- [ ] 2.1 `EXPECTED_MODEL_USAGE_COLUMNS` 常量 + `python zusage.py doctor` 骨架：① Python 版本 ② db 存在性 ③ 列完整性（PRAGMA 比对，缺列报 ✗ 不崩溃）④ 运行时文件 ⑤ asar 注入状态（只读）⑥ diag 新鲜度与错误 ⑦ 泵进程（尽力而为，查不到标 `?`），验证：健康环境全 ✓ 退出码 0
- [ ] 2.2 故障场景验证：`ZCODE_HOME` 指向空目录 → db ✗；临时改名运行时 `overlay.js` → 文件检查 ✗；缺列副本 → 列完整性 ✗ 且列出缺失列，全都不崩溃
- [ ] 2.3 `--net` 附加额度连通性检查（默认不出网），验证：带 `--net` 时额外输出额度查询结果，不带时零出网

## 3. 观测增强

- [ ] 3.1 `inject-main.cjs` handlePayload 在 diag 附带 `quota: {ok, reason, age_s, last_error}`，验证：额度正常与失败两种场景下 diag 字段可读
- [ ] 3.2 overlay ⚙ 面板「复制诊断」按钮：版本 / 显示项状态 / updateErr / trackErr / 额度状态 / diag 关键字段 → 剪贴板，写入结果有可见反馈，验证：点击后粘贴内容齐全
- [ ] 3.3 README 双语：看门狗章节（含关闭与恢复命令）、doctor 用法、「报障请附复制诊断内容」指引

## 4. 收尾

- [ ] 4.1 全量回归：状态条 9 项 + 额度 + 开关 + doctor 全 ✓；计划任务在跑且日志正常
- [ ] 4.2 端到端自愈演练：删注入行 → 10 分钟内看门狗自动补回 → 按通知重启 → 状态条恢复，全流程留痕核对
- [ ] 4.3 归档 `openspec archive self-heal-and-doctor`
