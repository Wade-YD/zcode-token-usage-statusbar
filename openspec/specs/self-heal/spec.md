# self-heal Specification

## Purpose
TBD - created by archiving change self-heal-and-doctor. Update Purpose after archive.
## Requirements
### Requirement: 计划任务看门狗
安装 MUST 注册用户级计划任务 `zusage-watchdog`（默认每 10 分钟运行一次检查器，无需管理员权限）；卸载 MUST 删除该任务；`--no-watchdog` 安装时 MUST 不注册。检查器 MUST 幂等：重复运行不产生副作用。

#### Scenario: 安装即注册
- **WHEN** 执行 `python install.py`
- **THEN** `schtasks /Query /TN zusage-watchdog` 可查到任务，且 `schtasks /Run /TN zusage-watchdog` 可手动触发检查器

#### Scenario: 卸载即删除
- **WHEN** 执行 `python install.py --remove`
- **THEN** 计划任务被删除，不再有周期性检查

### Requirement: 注入缺失自动修复
检查器发现 asar 主入口缺失本工具注入行时 MUST 自动重打（复用 patch_install 的校验与原子替换）；重打成功后 MUST 通过 Windows 通知告知用户「重启 ZCode 生效」（注入发生在客户端运行中时）；客户端未运行时 MUST 静默完成修复、不发通知。运行中原子替换失败（文件锁）时 MUST 留待下一周期收尾，不得弹窗阻塞。

#### Scenario: 升级抹掉注入、客户端运行中
- **WHEN** ZCode 更新覆盖 asar 后，用户继续使用客户端（未重启），检查器运行
- **THEN** 注入行被自动补回，用户收到一次「重启 ZCode 生效」的通知，重启后状态条恢复

#### Scenario: 客户端未运行
- **WHEN** ZCode 未运行时检查器发现注入缺失
- **THEN** 静默完成修复，下次启动 ZCode 状态条直接出现，无任何弹窗

### Requirement: 通知防抖
检查器 MUST 维护状态文件（运行时目录 `watchdog-state.json`）：同一问题（同一 asar 状态）MUST 只通知一次；修复确认后清除状态。通知后若用户长期未重启，MUST NOT 周期性重复弹通知。

#### Scenario: 同一问题不重复轰炸
- **WHEN** 注入缺失且已通知过用户、用户连续 9 天未重启 ZCode
- **THEN** 检查器继续自动重打但不再重复弹通知

#### Scenario: 问题消除后复位
- **WHEN** 用户重启 ZCode、diag 确认注入已加载
- **THEN** 状态文件复位，未来新的注入缺失可再次通知

### Requirement: 检查器自包含与容错
检查器 MUST 不依赖 ZCode 进程或注入链路自身（诊断自己不能依赖被诊断对象）；自身异常 MUST 静默退出并留痕（运行时目录日志），MUST NOT 弹窗干扰用户、MUST NOT 影响 ZCode 运行。

#### Scenario: 检查器自身故障
- **WHEN** 检查器运行中发生异常
- **THEN** 异常写入运行时目录日志后静默退出，无弹窗、无残留进程、计划任务照常下周期运行

