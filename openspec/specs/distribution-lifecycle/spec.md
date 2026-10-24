# distribution-lifecycle Specification

## Purpose
TBD - created by archiving change migrate-to-zusage-base. Update Purpose after archive.
## Requirements
### Requirement: 私有仓库与上游署名
自有仓库 MUST 为 GitHub 私有仓库（公开仓库不可私密 fork，采用新建私有仓库推送代码的方式）；MUST 保留上游 MIT LICENSE、README 中的来源链接与版权声明；自有改动用 conventional commits 记录，且不得移除上游作者署名。

#### Scenario: 仓库初始化
- **WHEN** 把本地克隆推送到新建私有仓库
- **THEN** LICENSE 与 README 上游来源信息完整保留，提交历史包含上游作者提交（完整历史而非浅克隆压平）

#### Scenario: 后续自有改动
- **WHEN** 新增额度移植等提交
- **THEN** 提交信息可辨识自有改动范围，README 增补「基于上游 xhwxt/…」说明

### Requirement: 安装与卸载契约
安装 MUST 在 ZCode 完全退出后以原子替换方式写入 asar（TMP → asar），并提供注入生效确认（diag 回写）；卸载 MUST 通过剥离注入行完成，MUST NOT 依赖安装时的 asar 备份，且 MUST NOT 移除其它工具写入的注入内容。

#### Scenario: 在客户端运行时发起安装
- **WHEN** ZCode 正在运行时执行安装
- **THEN** 安装暂存待替换文件，等待客户端退出后由监控完成替换，并提示重启以生效

#### Scenario: 卸载
- **WHEN** 执行卸载且 asar 中还包含其它工具的注入内容
- **THEN** 仅剥离本工具的注入行，其余内容不受影响

### Requirement: 与旧插件的互斥与下线
迁移 MUST 遵循「先安装新基座并验证 → 再卸载旧 api-quota 插件（`patch-zcode.py --restore`、移出 `plugins.dirs`、停用其 SessionStart 钩子）」的顺序；两套 asar 注入并存期 MUST 尽量短，且 MUST NOT 跨 ZCode 版本升级保留并存状态。

#### Scenario: ZCode 在并存期升级
- **WHEN** 两套注入同时在位时 ZCode 升级覆盖了 asar
- **THEN** 先完整卸载其中一套再重打另一套，不得让两套自愈逻辑各自重打

#### Scenario: 迁移完成
- **WHEN** 新基座验收通过并卸载旧插件后
- **THEN** ZCode 内只剩一套状态条，旧插件的 `backup/` 备份与还原能力保留在原目录备查

### Requirement: 升级后重打为单步操作
ZCode 更新覆盖 asar 后，重打注入 MUST 是单步可执行且可自检的操作（无需重新走过完整安装向导），并能在重打后通过 diag/状态条目视确认生效。

#### Scenario: 客户端更新后恢复
- **WHEN** ZCode 升级导致注入丢失、状态条消失
- **THEN** 执行单步重打命令 + 重启后状态条恢复，diag 记录注入链路已加载

### Requirement: patch_install standalone 显式目标
`patch_install.py` 独立运行（不经 install.py / watchdog 拉起）时 MUST 支持 `--asar <路径>` 与环境变量 `ZCODE_ASAR` 显式指定目标 asar（优先级：命令行参数 > 环境变量 > 平台默认探测；Windows 默认优先 `%LOCALAPPDATA%\Programs\ZCode`），支持 `--runtime <目录>` 与 `ZCODE_RUNTIME` 指定运行时目录，支持 `--finalize` 旗标透传给 install；由 install.py / watchdog 拉起时的 `set_target()` / `set_runtime()` 行为 MUST 不变。standalone 且未显式声明 `--runtime` 时若检测到旧注入行指向其它运行时目录，MUST 拒绝重打包并引导（防静默改指）。运行中替换失败的提示语 MUST 带可直接复制的显式路径命令。

#### Scenario: 自定义安装路径 standalone 收尾
- **WHEN** 用户按提示带 `--asar`/`--runtime` 参数运行 `install --finalize`
- **THEN** 替换作用于显式指定的 asar 与运行时目录，不依赖平台默认猜测

#### Scenario: finalize 透传
- **WHEN** 运行 `python patch_install.py install --finalize`
- **THEN** install 以 finalize=True 执行（不再忽略该旗标）

#### Scenario: 环境变量兜底
- **WHEN** 未传参数但设置了 ZCODE_ASAR
- **THEN** standalone 命令作用于该 asar

#### Scenario: 防静默改指运行时
- **WHEN** standalone 运行 install 且 asar 旧注入行指向其它运行时目录、未显式声明 `--runtime`
- **THEN** 拒绝重打包并给出带路径的引导命令，不产生 .tmp

