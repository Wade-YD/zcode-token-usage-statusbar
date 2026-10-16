# add-plugin-shell

## Why

兄弟仓 ByteRax/zcode-token-usage-statusbar（同上游 xhwxt，10-13 建、25★、v1.4.3）把 ZCode 插件化走得很好：插件市场安装、SessionStart 钩子自动同步运行时并自检修复状态条。对照我们的链路，有三个真实痛点可以借这一机制解决：

1. **运行时同步靠手工**：仓库改完 overlay/zusage 后，同步到数据目录要么手动 Copy-Item（Mimosa 拦 bash 写），要么重跑 install.py——每次更新两步手工。
2. **自检周期最坏 10 分钟**：看门狗是 10 分钟计划任务，注入被抹后最多空窗 10 分钟才发现；会话启动钩子可在每次启动时立刻查一次。
3. **"副本缺文件"类故障的复发面**：09-30 曾因数据目录缺新文件（RUNTIME_FILES 半更新）看门狗 import 死亡 10 天；启动时按清单自动同步根治此类漂移。

同时 cherry-pick 兄弟仓两个已实证修复：① Claude 系列不单独上报思考 token 时的速度虚高（上游 10-15 真库裁决：claude-opus-5-5 按首字窗口 35% 请求超 300 tok/s；我们连上游 10-14 的"含思考段窗口前移"也未持有，一并补上）；② patch_install standalone 默认路径过时（我们仓库 ASAR_DEFAULT 仍指 D:\ZCode，本机实际装在 %LOCALAPPDATA%\Programs\ZCode）。

## What Changes

- **插件壳**（新增 `plugin/` + 根 `marketplace.json`）：`.zcode-plugin/plugin.json` 清单 + SessionStart(startup|resume) 钩子 `session-start.py`：按 RUNTIME_FILES 清单把仓库运行时同步到数据目录（字节比较，内容不变不写）+ 同步 /usage 命令模板到全局命令目录 → 调数据目录 `watchdog.main()` 做一次注入自检（与计划任务同一实现：精确匹配/暂存收尾/通知防抖）。任何异常吞掉后以 0 退出，失败留痕 `hooks/session-start.err.log`。插件包不复制运行时副本（运行时以数据目录为家）。
- **/usage 命令模板增补**（`usage.command.md`）：补 `speed[:N]` scope 与 MCP 不可用时的 CLI 回退说明
- **速度口径修复**（`zusage.py`）：新增 `hides_reasoning()`；`rate_of()` 行元组扩至含 `started_at`/`model_id`，生成速度在含思考（reasoning>0 或 Claude 系列）时窗口前移到 started_at；`speed_stats`/`_session_snapshot` 查询列随动；可见文本速度保持首字窗口口径不变
- **patch_install standalone 修复**：`__main__` 解析 `--asar/--runtime/--finalize`（env `ZCODE_ASAR`/`ZCODE_RUNTIME` 兜底，参数优先）；Windows 默认路径优先 `%LOCALAPPDATA%\Programs\ZCode`；运行中替换失败的提示语带显式路径；`install --finalize` 真正透传 `finalize=True`（此前文档写了、CLI 忽略）
- **新增回归测试** `tests/test_speed_window.py`：Claude/含思考/普通模型三档窗口口径 + speed_stats 聚合

## Capabilities

### New Capabilities

- `plugin-shell`: 插件打包与 SessionStart 钩子——运行时同步、注入自检、失败静默

### Modified Capabilities

- `speed-metrics`: 生成速度窗口口径——含思考与不单独上报思考的模型前移到 started_at
- `distribution-lifecycle`: patch_install standalone 显式目标（--asar/--runtime/env）与 finalize 透传

## Impact

- **新文件**：`plugin/`（plugin.json / hooks.json / session-start.py，约 120 行）、`marketplace.json`、`tests/test_speed_window.py`
- **改动**：`zusage.py`（约 +22 行）、`patch_install.py`（约 +45 行）、`usage.command.md`（约 +4 行）
- **不变**：注入点、泵协议、zquota 额度模块、watchdog 计划任务（插件钩子是第二道防线，不替代）、MCP 全局注册（插件不携带 .mcp.json，避免双注册）
- **系统侧**：注册后 `~/.zcode/cli/config.json` 的 `plugins.dirs` 增加插件目录一项（可逆，改前备份）
