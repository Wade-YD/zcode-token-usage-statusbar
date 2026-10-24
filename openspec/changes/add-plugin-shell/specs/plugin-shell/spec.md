# plugin-shell Specification (Delta)

## ADDED Requirements

### Requirement: 插件包结构
仓库 MUST 提供 ZCode 插件包 `plugin/`（`.zcode-plugin/plugin.json` 清单 + `hooks/hooks.json`）与根 `marketplace.json`（本地市场，source 指向 `./plugin`），使状态条自检可经 plugins.dirs 或插件市场挂载。插件包 MUST NOT 携带运行时副本或 `.mcp.json`——运行时以数据目录为家、MCP 沿用 install.py 的全局注册，避免双份运行时与重复工具注册。

#### Scenario: plugins.dirs 注册
- **WHEN** 用户把 `plugin/` 目录加入 ZCode 的 plugins.dirs 并启动会话
- **THEN** SessionStart 钩子被执行，既有状态条、额度与 MCP 功能不受影响

#### Scenario: 市场安装形态
- **WHEN** 插件经市场安装到无仓库根的位置，且数据目录已有运行时
- **THEN** 运行时同步静默跳过，注入自检仍走数据目录副本；数据目录缺运行时则触发引导安装（见下）

### Requirement: 市场形态引导安装
市场形态下 SessionStart 钩子检测到数据目录缺 watchdog.py 时 MUST 自动引导安装：从官方仓库 GitHub main 分支 tarball 下载（仅 https + 固定域名白名单、拒绝 IP 字面量主机、体积上限 20MB、重定向逐跳校验），两阶段落地——先取 install.py 解析权威 DATA_DIR/RUNTIME_FILES，再同步全套运行时与 /usage 命令模板到数据目录，并仅当缺失时补写最小 config.json（只含 python_path）。引导安装 MUST NOT 注册 MCP 或计划任务（仍属 install.py 主安装），MUST NOT 触碰已存在的用户 config.json。引导失败 MUST 留痕 err.log 并进入 6 小时冷却（冷却期内的会话启动不得发起网络请求）；仓库形态或数据目录已有 watchdog.py 时不触发下载。

#### Scenario: 市场安装后首次会话
- **WHEN** 用户仅经插件市场安装（从未运行 install.py）且网络可达，启动会话
- **THEN** 钩子下载运行时到数据目录、/usage 命令模板与最小 config.json 就位，注入自检照常执行（ZCode 运行中则暂存+通知，与计划任务语义一致）

#### Scenario: 离线环境
- **WHEN** 网络不可达时启动会话
- **THEN** 本次引导失败仅留痕 err.log，会话照常启动，且 6 小时内的后续会话启动不再重复发起下载

### Requirement: 会话启动同步与自检
SessionStart（startup|resume）钩子 MUST：① 按 RUNTIME_FILES 清单（以仓库 install.py 为唯一权威定义）把仓库运行时同步到数据目录，字节比较、内容不变不写；② 同步 /usage 命令模板到全局命令目录；③ 以数据目录 watchdog（与计划任务同一实现）执行一次注入自检，修复行为（重打/暂存/通知防抖）与计划任务完全一致。

#### Scenario: 更新仓库后启动会话
- **WHEN** 仓库 overlay.js 等运行时文件有内容变更后用户启动新会话
- **THEN** 数据目录副本在钩子中被刷新（泵按 mtime 热重载），无需手动 install.py / Copy-Item

#### Scenario: 注入被更新抹除后启动会话
- **WHEN** ZCode 更新抹掉注入行后用户启动新会话
- **THEN** 钩子内自检发现缺失并重打（客户端运行中则暂存+通知），早于 10 分钟计划任务周期

#### Scenario: 副本缺新文件
- **WHEN** 数据目录缺少清单中的某个运行时文件（历史 RUNTIME_FILES 半更新场景）
- **THEN** 钩子同步自动补齐，不出现"看门狗 import 死亡"类故障

### Requirement: 钩子绝不阻断会话
钩子脚本 MUST 捕获全部异常并以退出码 0 结束；失败 MUST 留痕 `hooks/session-start.err.log`（带时间戳，超 32KB 截断尾部）；同步动作 MUST 在数据目录 watchdog.log 留痕（仅当实际发生复制）。

#### Scenario: 依赖损坏
- **WHEN** 数据目录运行时缺失或 watchdog 导入失败
- **THEN** 会话照常启动，err.log 留痕，无弹窗无残留进程
