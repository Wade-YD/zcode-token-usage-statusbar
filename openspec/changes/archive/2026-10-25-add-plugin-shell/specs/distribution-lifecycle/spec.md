# distribution-lifecycle Specification (Delta)

## ADDED Requirements

### Requirement: patch_install standalone 显式目标
`patch_install.py` 独立运行（不经 install.py / watchdog 拉起）时 MUST 支持 `--asar <路径>` 与环境变量 `ZCODE_ASAR` 显式指定目标 asar（优先级：命令行参数 > 环境变量 > 平台默认探测；Windows 默认优先 `%LOCALAPPDATA%\Programs\ZCode`），支持 `--runtime <目录>` 与 `ZCODE_RUNTIME` 指定运行时目录，支持 `--finalize` 旗标透传给 install；由 install.py / watchdog 拉起时的 `set_target()` / `set_runtime()` 行为 MUST 不变。运行中替换失败的提示语 MUST 带可直接复制的显式路径命令。

#### Scenario: 自定义安装路径 standalone 收尾
- **WHEN** 用户按提示带 `--asar`/`--runtime` 参数运行 `install --finalize`
- **THEN** 替换作用于显式指定的 asar 与运行时目录，不依赖平台默认猜测

#### Scenario: finalize 透传
- **WHEN** 运行 `python patch_install.py install --finalize`
- **THEN** install 以 finalize=True 执行（不再忽略该旗标）

#### Scenario: 环境变量兜底
- **WHEN** 未传参数但设置了 ZCODE_ASAR
- **THEN** standalone 命令作用于该 asar
