# migrate-to-zusage-base — 设计

## Context

- 新基座为上游 `xhwxt/zcode-token-usage-statusbar`（MIT，68★，v61）：注入 `app.asar` 主入口一行 dynamic import；`inject-main.cjs` 在主进程 `fs.watch` db 目录并 spawn `zusage.py serve` 常驻子进程（stdio JSON 行协议，崩溃自动重启、mtime 热重载、stdin EOF 自清理）；`overlay.js` 按窗口注入并热更新。
- 旧项目 zcode-quota 的可用资产：额度查询三实现、`_provider_entry()` 三级解析、`_assert_public_host()` + 拒重定向防护（fake-IP 豁免）、速度口径三项改进。度量事实（实测）：`reasoningTokens ⊆ outputTokens`（`totalTokens = input + output` 恒等）。
- 上游现状缺口：无额度查询；速度统计未按 `status` 过滤；无推理拆分/口径标注；无多出口一致性约束。

## Goals / Non-Goals

**Goals:**
- 把额度/余额能力以原生气质移植进新基座（无新进程、无端口、走既有协议与热重载）
- 速度统计加三项口径改进（completed 过滤、推理拆分、TTFT 回退）
- 建立私有仓库并完成旧插件的安全下线
- 全程保留上游署名与剥离式安装/卸载契约

**Non-Goals:**
- 不改上游注入点与安装流程本身（保持「客户端关闭时原子替换」）
- 不做成本换算/峰谷定价/额度预测（后续独立 change）
- 不改品牌名（用户未定；本斑块只加来源说明）
- 不恢复旧项目的 8788 服务与鉴权（架构上已不需要）

## Decisions

**D1 额度实现形态：zusage 内的新模块 + 既有 stdio 协议新消息**
权限决策：额度查询写成独立 python 模块（由 `zusage.py` 导入），`serve` 循环新增 `quota` 快照消息；overlay 新增第 9 项渲染。理由：复用上游进程模型与热重载，不引入第二个进程/端口；备选「独立 CLI 子命令」被否——无法推动状态条实时更新。
**D2 额度缓存与推送节流：服务端 5 分钟缓存 + 失败保留上次成功**
额度是慢变量：python 侧持缓存（默认 300s 可配），仅有变化/年龄更新时推送；失败时返回上次成功数据 + `age`，由 overlay 标注「x 分钟前」；无历史数据才显示分类失败原因。备选「每次 db 事件顺手查」被否——会把唯一出网行为放大到每轮对话。
**D3 解析与防护：原样移植旧仓逻辑**
`_provider_entry()` 三级解析（config.json → providerRules → `account:<family>` 回落 builtin）与 `_http_json` 防护闸（https + 公网解析 + fake-IP `198.18.0.0/15` 豁免 + 拒重定向）逐字移植并保留单测点。理由：为 ZCode 3.14 实测修复；测试断言 `evil-deepseek.example` 不命中、内网 IP 被拒。
**D4 速度改进落点：加过滤、加拆分、加标注——不改公式**
上游公式已与实测口径一致，改动是三项增强：①所有速度相关 SQL 加 `status='completed'`；②`reasoning_tokens > 0` 时并列「生成速度 / 可见文本速度」；③`ttft` 覆盖率 <80% 时回退端到端口径并标注。多出口（CLI/statusBar/MCP）共用同一函数，禁止各出口自行取整。
**D5 共存与互斥：先装新、验收、再卸旧**
两套注入物理可并存（旧件改 renderer index.html、新基座追加主入口），但共存期定为「迁移窗口限定」：ZCode 升级后禁止两套自愈各自重打——升级发生时先卸一套再重打另一套。卸载旧件 = `patch-zcode.py --restore` + 移出 `plugins.dirs` + 停用其 SessionStart 钩子；旧目录（含 backup/）保留备查。
**D6 私有仓库：新建而非 fork**
GitHub 不支持公开仓库的私密 fork。做法：`git fetch --unshallow` 保留完整上游历史 → `gh repo create <name> --private --source=. --push`。保留 LICENSE 与 README 来源链接。上游仓库另设 `upstream` remote 以便后续 cherry-pick。
**D7 旧仓库降级为素材库**
zcode-quota 不再部署；其 `optimization-batch-1` 标注归档（② 安全收紧组随架构作废；① 中的口径改进并入本 change；③ 余额机制并入配额显示；④ 运维项大部分作废，仅保留「只统计 completed」迁入）。

## Risks / Trade-offs

- [「全程不联网」承诺被打破：额度查询是唯一出网行为] → README 与设计明示；设置面板提供总开关（默认开）；出网仅 https 公网 + 防护闸；凭据只读 `~/.zcode` 配置
- [上游持续更新，fork 落后] → 保留 `upstream` remote，里程碑处 cherry-pick；本 change 改动集中于新模块与少量既有函数，冲突面小
- [迁移期额度显示依赖旧插件] → 窗口尽量短：先装新基座（旧件仍在）→ 移植验收 → 立即卸载
- [两套注入并存期遇 ZCode 升级] → D5 的升级规则写进 tasks 与 README 的迁移说明
- [额度接口变更/中转站无接口] → 失败降级（保留上次数据 + 年龄）与自定义 `balanceUrl` 扩展点兜底
- [上游 overlay 条目布局被新项挤压] → 第 9 项做成可开关（沿用上游设置面板机制），窄窗时按上游既有折叠策略隐藏

## Migration Plan

1. 建私有仓库（unshallow → gh repo create --private --push），提交本 change 的 openspec 工件
2. 试装新基座：运行安装（ZCode 关闭时完成替换）→ 重启 → 以 diag 回写与状态条出现确认生效
3. 移植额度：模块 + 协议消息 + overlay 第 9 项 + config 例样 → 命令行与状态条双验证（与 BigModel 桌面端数字对照）
4. 速度三项改进 → 验证 CLI/状态条/MCP 三处口径一致、取消调用不再入统计
5. 卸载旧插件（restore + plugins.dirs 移除 + 钩子停用）→ 全量回归；旧目录保留备查
6. 旧仓 `optimization-batch-1` 归档标注，迁出清单生效

**回滚**：卸载新基座 = 剥离注入行（不依赖备份）；旧插件在卸载前始终可用，额度显示无真空；如额度模块引入问题，设置面板总开关可即时关闭出网。

## Open Questions

- 状态条第 9 项形态：独立项 vs 并入「本轮」明细——倾向前者（可开关），落地时按实测观感定
- 额度默认刷新节奏：5 分钟 vs 更长（按 BigModel 窗口粒度 5 小时看，5 分钟足够）
- 品牌命名与仓库名（用户未定，先以 `zcode-token-usage-statusbar` 私有仓库占位）
