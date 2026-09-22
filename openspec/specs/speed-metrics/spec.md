# speed-metrics Specification

## Purpose
TBD - created by archiving change migrate-to-zusage-base. Update Purpose after archive.
## Requirements
### Requirement: 只统计 completed 调用
速度指标（单次速度、本轮/会话聚合、今日合计中与速度相关的部分）MUST 只纳入 `status='completed'` 的调用；`cancelled`、`error`、`running` 状态的记录 MUST NOT 计入任何速度指标。

#### Scenario: 取消的调用
- **WHEN** 某次调用被用户取消（status=cancelled）且已产生部分输出
- **THEN** 该调用不出现在速度指标与速度曲线中

#### Scenario: 失败的调用
- **WHEN** 某次调用以 status=error 结束
- **THEN** 该调用不影响任何速度统计

### Requirement: 推理 token 拆分出可见文本速度
当 `reasoning_tokens > 0` 时，展示 MUST 并列给出「生成速度」（output ÷（completed−first_token），含推理）与「可见文本速度」（(output − reasoning) ÷（completed−first_token））；`reasoning_tokens = 0` 或数据源无该字段时 MUST NOT 展示可见文本速度。

#### Scenario: 高推理占比模型
- **WHEN** 某模型推理 token 占输出 70%
- **THEN** 两个速度并列展示且数值显著不同，用户可区分「吞吐」与「可见文本」

#### Scenario: 未上报推理拆分
- **WHEN** 服务商不拆分推理 token（reasoning_tokens 恒 0）
- **THEN** 仅展示生成速度，且不把 0 呈现为「无推理」结论

### Requirement: TTFT 缺失处理
`time_to_first_token_ms` 为 NULL 的调用 MUST 仍计入生成速度（output ÷ duration）；当同一视图内可用解码样本少于总样本 80% 时，速度展示 MUST 回退为端到端口径并标注口径名称，不得静默混用两种口径。

#### Scenario: 大量缺失 TTFT
- **WHEN** 某窗口 10 条完成调用中 4 条 TTFT 为空
- **THEN** 以 6 条齐全样本计算纯生成速度；若齐全样本 <8 条则整体回退总速度口径并显示标注

### Requirement: 多出口口径一致
CLI（`zusage.py`）、MCP 工具与状态条展示的速度数值 MUST 来自同一计算实现，不得在各出口重复实现或各自取整；同一时刻同一范围查询的三处数值 MUST 等价。

#### Scenario: CLI 与状态条对照
- **WHEN** 用户运行 CLI 查询最近一次调用速度并与状态条第①项对比
- **THEN** 两处数值一致（允许展示精度不同，不允许口径不同）

