# quota-display Specification

## Purpose
TBD - created by archiving change migrate-to-zusage-base. Update Purpose after archive.
## Requirements
### Requirement: 服务商识别与三级解析
额度查询 MUST 依据最后一条调用的 `provider_id` 解析出服务商条目，解析顺序为：`~/.zcode/v2/config.json` 的 provider 字典精确匹配 → `~/.zcode/v2/provider_config.json` 的 `providerConfigRules.providerRules`（取 providerName / access.apiKey / api.baseUrl）→ `account:<family>-<plan>` 形态回落到同 family、已启用且带 Key 的 `builtin:*` 条目。三者皆空时展示「未找到服务商配置」，不发起任何请求。

#### Scenario: 套餐形态 id
- **WHEN** 最后一条调用的 provider_id 为 `account:bigmodel-individual-coding-plan`
- **THEN** 回落到同 family 的 builtin 条目并成功查询智谱套餐窗口

#### Scenario: 自定义服务商
- **WHEN** provider_id 为 providerRules 中的自定义服务商（如 `new-provider`）
- **THEN** 从其规则中取 apiKey/baseURL 进行查询或给出对应提示

### Requirement: 额度与余额的展示
套餐类服务商 MUST 逐窗口展示剩余百分比、重置时间与已用/总量；余额类服务商 MUST 展示多币种余额（充值/赠送拆分）。百分比与金额格式化 MUST NOT 产生误导性舍入（不足 100% 不得显示为 100%）。

#### Scenario: 双窗口套餐
- **WHEN** 智谱 Coding Plan 返回 5 小时与每周两个窗口
- **THEN** 两个窗口各自显示剩余百分比与重置时间，样式与状态条既有条目一致

#### Scenario: 多币种余额
- **WHEN** DeepSeek 返回多币种余额
- **THEN** 按金额排序展示各币种余额及充值/赠送拆分

### Requirement: 查询缓存与失败降级
额度查询 MUST 带缓存（默认 5 分钟，可配置），同一时刻并发请求 MUST 只触发一次查询。查询失败时 MUST 保留最近一次成功数据并标注其时间「x 分钟前」，而非以错误文案替换数字；无历史成功数据时才显示失败原因。

#### Scenario: 网络瞬断
- **WHEN** 额度接口请求超时但此前有过成功结果
- **THEN** 状态条继续显示上次数据并标注数据年龄，恢复后自动刷新

#### Scenario: 首次即失败
- **WHEN** 首次查询即失败且无缓存
- **THEN** 显示可读的失败原因（网络/鉴权/超时分类）

### Requirement: 出站防护与自定义端点
额度查询 MUST 复用统一防护闸：仅 https、host 解析结果必须为公网地址（`198.18.0.0/15` fake-IP 段豁免）、拒绝跟随重定向。服务商配置提供 `balanceUrl`（可选 `balanceKind`）时 MUST 按配置查询该端点，并同样经过防护闸。API Key MUST 只从 `~/.zcode` 配置读取，MUST NOT 写入仓库文件、日志或界面文案。

#### Scenario: 仿冒域名与内网目标
- **WHEN** 配置的 baseURL 为 `evil-<厂商>.example` 或解析到环回/私网地址
- **THEN** 请求被防护闸拒绝，Key 不发出，界面显示失败原因

#### Scenario: 自定义端点接入
- **WHEN** 中转站在 providerRules 中配置了可用的 `balanceUrl`
- **THEN** 按其 `balanceKind`（balance/quota）解析并展示，效果与内置服务商一致

### Requirement: 不支持与失败语义区分
提示信息 MUST 区分三类情形：无匹配分支（暂不支持自动查询）、配置缺 Key/baseURL、查询失败（网络/鉴权/协议）。三类文案互不混用，便于用户判断是能力缺失还是临时故障。

#### Scenario: 中转站无余额接口
- **WHEN** 当前服务商无内置分支且未配置 balanceUrl
- **THEN** 显示「暂不支持自动查询」类提示，而非「查询失败」

### Requirement: 额度条目开关即出网开关
额度条目的显示开关 MUST 同时作为联网开关：取消勾选「额度」条目时 MUST 立即停止一切额度相关的出站请求与后台刷新并隐藏条目；重新勾选后恢复查询与显示。开关状态 MUST 持久化，并经泵查询通道实时下发（无需重启客户端）。config.json 的 `quota.enabled: false` MUST 提供等效的文件级关闭（条目勾选但配置禁用时显示禁用提示、不出网）。

#### Scenario: 面板关闭条目
- **WHEN** 用户在设置面板取消勾选「额度」
- **THEN** 后续快照不再携带额度数据、无任何出站请求，条目同时消失；重新勾选后额度自动恢复（缓存冷启动时先显示预热态）

#### Scenario: 文件级关闭
- **WHEN** config.json 设置 `quota.enabled: false`
- **THEN** 额度查询模块对所有调用返回 disabled，不发起网络请求

