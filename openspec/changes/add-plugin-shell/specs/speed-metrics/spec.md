# speed-metrics Specification (Delta)

## ADDED Requirements

### Requirement: 未上报思考的模型按含思考窗口计时
模型不单独上报思考 token（Claude 系列，按 `model_id` 含 "claude" 判定，`reasoning_tokens` 恒 0 而 output 已含思考）时，其生成速度窗口 MUST 与"含思考段"同规则前移到 `started_at`（completed − started）；`reasoning_tokens > 0` 的请求同样适用；普通请求窗口不变（completed − first_token）。可见文本速度的窗口保持首字窗口不变。`started_at` 缺失时优雅退化为不前移。

#### Scenario: Claude 速度虚高修正
- **WHEN** 某次 claude 请求输出 1074、首字耗时 13661ms、总耗时 15087ms
- **THEN** 生成速度 ≈ 71.2 tok/s（1074 ÷ 15.087s），不再出现按首字窗口的虚高值

#### Scenario: 含思考请求窗口前移
- **WHEN** 某请求 reasoning_tokens > 0 且 started_at 可用
- **THEN** 生成速度窗口为 completed − started；可见文本速度仍为（output − reasoning）÷（completed − first_token）

#### Scenario: 普通模型不受影响
- **WHEN** 非 Claude 且 reasoning_tokens = 0 的请求
- **THEN** 生成速度与可见速度口径与历史一致
