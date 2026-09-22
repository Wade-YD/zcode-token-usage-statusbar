# migrate-to-zusage-base

## Why

旧方案（api-quota / zcode-quota）三日实战暴露架构性问题：独立常驻服务反复暴毙（WMI 控制台窗、PATH 解释器彩票、20 秒自杀逻辑）、asar 等长改写脆弱、「改界面要重启 ZCode」。上游 `xhwxt/zcode-token-usage-statusbar`（68★，MIT，本赛道最成熟）以「主进程注入 + 常驻 python 子进程（stdio 协议）+ db 文件事件驱动」的架构把这类故障整类删除，且其生成速度口径 `output ÷（首 token→完成）` 与我们的实测口径完全一致。唯一缺口是它不做套餐额度/余额查询——这是当前每天使用的功能。本 change 以它为自有基座，把额度能力与速度口径改进移植过去，随后干净下线旧插件。

## What Changes

- **基座切换**：以 xhwxt/zcode-token-usage-statusbar 为自有仓库基座；GitHub 不支持公开仓库的私密 fork，做法是**新建私有仓库推送代码**，保留 MIT 署名与上游链接
- **额度移植**：新增额度查询模块（DeepSeek 余额 / 智谱套餐窗口 / OpenRouter 点数 + 三级 provider 解析 + 完整 SSRF 防护），作为状态条新增条目显示 5 小时/周窗口剩余与重置时间；配置化自定义余额端点保留为扩展点
- **速度口径改进**：只统计 `status='completed'`（过滤 cancelled/error）、`reasoning_tokens` 拆出「可见文本速度」、TTFT 缺失样本回退总速度并标注
- **安装与互斥**：安装新基座 → 验证通过后干净卸载旧插件（`patch-zcode.py --restore` + 移出 `plugins.dirs`）；明确共存期与卸载顺序，避免两套 asar 注入长期并存
- **旧仓库降级**：zcode-quota 留作素材库；其 `optimization-batch-1` 中仍有效的部分以清单迁入本 change，旧 change 归档标注

## Capabilities

### New Capabilities

- `quota-display`: 状态条中的套餐额度/余额展示——服务商识别与三级解析、窗口与余额的渲染、刷新节流、失败与不支持语义、自定义端点扩展
- `speed-metrics`: 生成速度统计口径——completed 过滤、推理 token 拆分与可见文本速度、TTFT 缺失回退与口径标注
- `distribution-lifecycle`: 自有仓库与发布流程——私有仓库推送（保留 MIT 署名）、安装/卸载/升级重打的操作契约、与旧 api-quota 插件的互斥与下线

### Modified Capabilities

（无——本仓 `openspec/specs/` 尚无已归档规格）

## Impact

- **代码**：`zusage.py`（新增额度查询模块与协议消息、`serve` 常驻循环扩展）、`overlay.js`（新条目 + 设置开关 + i18n 文案）、`inject-main.cjs`（额度推送与节流）、`config.example.json`（服务商与端点位）、`README*`（署名与来源）
- **出网**：仅额度接口（https + 公网解析校验 + fake-IP 豁免 + 拒重定向）；API Key 从 `~/.zcode` 配置读取，不落仓库、不出现在示例与测试
- **端口/进程**：不新增监听端口；进程模型不变（python 仍是 ZCode 主进程子进程）
- **asar 写入**：安装/重打仍走「客户端关闭时原子替换」；本 change 不改变注入点
- **迁移期风险**：旧插件提供额度显示直至移植验收；两套注入共存期尽量短，ZCode 更新后两套自愈逻辑不并行
