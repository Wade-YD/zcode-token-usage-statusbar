# self-heal-and-doctor

## Why

ZCode 的自动更新会覆盖 `app.asar`，静默抹掉本工具的注入行——已实测发生两次（09-22、09-29），第二次隔 9 天才被发现，且发现与修复全靠人眼。同时额度/速度链路缺少统一排障入口：每次故障都要手工跑多条命令、翻 diag 与日志才能定位到哪一环。本 change 建立「自愈 + 诊断」闭环：注入缺失后 10 分钟内自动修复并主动通知；`doctor` 一条命令输出全链路健康度。

## What Changes

- **看门狗（计划任务）**：安装时注册用户级计划任务 `zusage-watchdog`（默认每 10 分钟，`--no-watchdog` 可不注册，`--remove` 删除）：检查 asar 注入行，缺失 → 自动重打 + Windows 通知「重启 ZCode 生效」；幂等运行，状态文件防重复通知
- **doctor 自检命令**：`python zusage.py doctor [--net]`——Python 版本、db 存在性与关键列完整性（PRAGMA 对比）、运行时文件齐全性、asar 注入状态（只读）、diag 新鲜度与错误、泵进程、额度模块状态；每项 ✓/✗ 附修复提示，有 ✗ 时退出码非零
- **diag 增强**：loader 在 diag 中附带额度状态（ok / reason / age_s / last_error），额度故障可从 diag 直接读出
- **面板「复制诊断」**：设置面板一键把版本、状态、最近错误、payload 摘要写入剪贴板，报障时提供全量上下文
- 文档同步：双语 README 增补看门狗与 doctor 说明

## Capabilities

### New Capabilities

- `self-heal`: 注入自愈——计划任务检查器、自动重打、用户通知、通知防抖、注册/注销契约
- `diagnostics`: 排障诊断——doctor 自检命令、diag 额度状态、一键复制诊断

### Modified Capabilities

（无——`openspec/specs/` 现有 3 个规格的行为不变）

## Impact

- **新文件**：`watchdog.py`（检查器，约 120 行，进 RUNTIME_FILES）
- **改动**：`zusage.py`（doctor 子命令与检查函数，约 +150 行）、`inject-main.cjs`（diag 附带 quota 状态，约 +5 行）、`overlay.js`（面板复制诊断按钮，约 +30 行，VERSION 递增）、`install.py`（计划任务注册/注销，约 +30 行）、`patch_install.py`（暴露注入检查给看门狗复用，约 +10 行）
- **系统侧**：新增用户级计划任务 `zusage-watchdog`（无需管理员）；卸载时删除
- **不变**：注入点、serve 协议、出网行为（doctor 默认不联网，`--net` 才探测额度连通性）
