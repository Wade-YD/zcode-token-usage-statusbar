# diagnostics Specification

## Purpose
TBD - created by archiving change self-heal-and-doctor. Update Purpose after archive.
## Requirements
### Requirement: doctor 自检命令
`python zusage.py doctor [--net]` MUST 按固定清单输出检查结果，每项带 ✓/✗ 与失败时的修复提示，存在 ✗ 时退出码非零。检查清单 MUST 包含：Python 版本、db 存在性、`model_usage` 关键列完整性、运行时目录文件齐全性、asar 注入状态（只读检查）、diag 新鲜度与错误字段。`--net` 时附加额度接口连通性检查；默认 MUST NOT 出网。

#### Scenario: 健康环境
- **WHEN** 全链路正常时运行 `python zusage.py doctor`
- **THEN** 各项 ✓、退出码 0，并输出注入状态与 diag 最后更新时间

#### Scenario: 更新抹掉注入
- **WHEN** ZCode 更新覆盖 asar 后运行 doctor
- **THEN** 注入检查项 ✗ 并给出修复命令提示（`python install.py`），退出码非零

### Requirement: 列完整性探测
doctor MUST 用 `PRAGMA table_info` 将 `model_usage` 实际列与工具依赖的期望列清单比对：期望列缺失时报 ✗ 并列出缺失列名，而 MUST NOT 因缺列抛出异常或崩溃；现有查询在缺列库上的降级行为 MUST 在输出中说明。

#### Scenario: ZCode 升级改变表结构
- **WHEN** 未来版本 ZCode 移除或重命名了工具依赖的列（如 `time_to_first_token_ms`）
- **THEN** doctor 报 ✗ 并列出缺失列与受影响的功能，不崩溃、给出降级说明

### Requirement: diag 附带额度状态
主进程泵回写 diag 时 MUST 附带额度查询状态（ok / reason / age_s / last_error），使额度故障无需访问 python 侧即可从 diag 直接判读。

#### Scenario: 额度刷新连续失败
- **WHEN** 额度接口连续失败、状态条显示「N 分钟前」
- **THEN** diag 中可读到最近失败原因与数据年龄，与 tooltip 文案一致

### Requirement: 面板一键复制诊断
设置面板 MUST 提供「复制诊断」操作：点击后把版本号、显示项状态、最近错误（updateErr/trackErr）、额度状态、diag 关键字段组装为纯文本写入剪贴板，写入成功与否给用户可见反馈。

#### Scenario: 报障时提供上下文
- **WHEN** 用户点击「复制诊断」
- **THEN** 剪贴板中包含版本、开关状态、最近错误与数据新鲜度，可直接粘贴给维护者定位问题

