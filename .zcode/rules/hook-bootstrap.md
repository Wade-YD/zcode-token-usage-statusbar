# 插件钩子与市场形态自举

适用：`plugin/hooks/session-start.py` `plugin/hooks/hooks.json` `plugin/.zcode-plugin/`

## 三种形态分流（顺序不可颠倒）

1. **仓库形态**：插件目录旁有仓库根（`install.py` 可导入）→ 按 `RUNTIME_FILES`
   同步到数据目录，字节比较内容不变不写；改了冷文件记重启意图。
2. **市场形态 · 无运行时**：数据目录缺 `watchdog.py` → 从官方仓库 main tarball
   两阶段引导安装（先落 `install.py` 取权威清单，再同步全套）。
3. **市场形态 · 已有运行时**：到期（24h；失败退避 1h）在线刷新，同一受控通道。

## 下载与解包硬约束（MUST）

- 仅 https + 固定域名白名单（`codeload.github.com` / `github.com` /
  `objects.githubusercontent.com`）；拒绝 IP 字面量主机。
- 重定向逐跳走 `_url_guard`（默认 urllib 会无校验跟随到任意主机）。
- 压缩包体积 ≤ 20MB；解压面三重上限：成员数 5000 / 单成员 8MB / 累计 64MB。
- 按**实际读取长度**校验，不信任 tar header 声明的 size（gzip 炸弹可膨胀千倍）。
- 成员名拒绝绝对路径与 `..`；只取白名单成员，绝不 `tarfile.extract()` 落盘。

## 输出协议

- 钩子唯一 stdout 输出是 JSON（`systemMessage` + `hookSpecificOutput.additionalContext`）；
  Windows 必须先 `sys.stdout.reconfigure(encoding="utf-8")`，否则宿主按 UTF-8 解码乱码。
- 除重启提醒外不输出任何内容；异常一律吞掉并留痕 `hooks/session-start.err.log`。
- 重启提醒只读标记；钩子自己刚写的标记给 15 秒宽限（客户端回执可能仍在途）。

## 状态文件

- `hooks/bootstrap-state.json`：`last_attempt`（6h 冷却）、`last_refresh`（24h）、
  `refresh_error`（退避 1h）。写失败只降级为冷却失效，不得阻断会话。
