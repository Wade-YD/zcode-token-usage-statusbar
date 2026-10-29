# 额度查询（zquota.py）

## 契约（不可回退）

- `get_quota()` / `cached_payload()` **永不抛出**；任何异常都变成
  `{ok: False, reason: ..., message: ...}`。
- 失败**不覆盖**上次成功数据：过期返回旧值 + `stale=True` + `age_s`。
- 快照高频路径只读缓存（`cached_payload`），不得同步触网——泵单次预算 15 秒。
- 后台刷新单飞（`_inflight`）；同时只有一个线程在查。

## 退避分级（防把瞬时故障放大成长时间锁死）

| 失败类型 | 退避 | 说明 |
|---|---|---|
| 网络（超时/连接失败/5xx） | 30s 起 ×2，封顶 300s | 瞬时故障，值得快速重试 |
| 限流 429/503 | 尊重 `Retry-After`，钳制 30s–600s | 服务端说多久就等多久 |
| 解析（坏 JSON / 形态不对） | 固定 1800s | 重试不会自愈 |
| 鉴权 401/403 | 固定 1800s | 坏 Key 重试只会被拉黑 |
| 配置（非 https / 非公网 / 无 baseURL） | 固定 1800s | 改配置才能好 |

- 分类由 `_fetch` 写入 `_last_error_meta`，`_background_refresh` 读取；新增错误类型
  必须落到上表之一，不要新增"默认快速重试"分支。
- 结构性失败（`unsupported` / `no_key`）走 60 秒负缓存，不进退避表。

## 安全闸（MUST）

- 仅 https；host 解析结果必须 `is_global`（fake-IP 198.18.0.0/15 豁免）。
- 拒绝跟随重定向（`_NoRedirect`）——跨域跳转会带着 `Authorization` 头。
- 服务商识别只允许精确后缀匹配（`_match_provider`），禁止子串包含。
- 新服务商优先走 `balanceUrl` / `balanceKind` 自定义端点扩展点，不轻易加厂商分支。

## 自检

`python zquota.py --selftest` 必须全绿（无网络）。新增安全闸或解析器时同步加断言。
