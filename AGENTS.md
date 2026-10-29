# AGENTS.md — 维护者与 AI 协作约定

本文件是**约束**，不是教程。改代码前先读对应路径的细则；细则与代码冲突时以代码为准，
并回头修细则（细则漂移本身就是 bug）。

## 0. 路径 → 规则路由

| 要改的东西 | 先读 |
|---|---|
| `install.py` `patch_install.py` `install_monitor.py` `watchdog.py` `runtime_lock.py` | `.zcode/rules/injection.md` |
| `plugin/hooks/session-start.py` `plugin/hooks/hooks.json` `plugin/.zcode-plugin/` | `.zcode/rules/hook-bootstrap.md` |
| `zquota.py` | `.zcode/rules/quota.md` |
| `inject-main.cjs` `overlay.js` `zusage.py` `usage_mcp.py` | `.zcode/rules/runtime-sync.md` |
| `tests/` | `.zcode/rules/tests.md` |
| `README*` `docs/` `marketplace.json` 版本号 | `.zcode/rules/docs-release.md` |

## 1. 全局不变量（任何改动都不得破坏）

1. **单源真相**：仓库是唯一运行时源；`install.py` 的 `RUNTIME_FILES` 是唯一文件清单。
   新增运行时文件 = 加进 `RUNTIME_FILES`；任何"另抄一份清单"都是 bug。
2. **副本自洽**：数据目录副本必须能脱离仓库独立运行（`__dirname` / `__file__` 自定位）。
   禁止在运行时代码里写死仓库路径。
3. **失败只降级不阻断**：泵 / 钩子 / 看门狗的任何异常只留痕，不得阻断 ZCode 会话或窗口。
4. **只动自己的注入行**：asar 只增删 `ZUSAGE_LINE_RE` 匹配的那一行；不制作也不依赖 `.bak`。
5. **凭据零落盘**：API Key 只从 `~/.zcode` 配置读，不进日志、不进界面、不进仓库。
6. **出网白名单**：所有服务端请求仅 https + 固定域名；拒绝环回 / 私有 / 保留地址；
   拒绝跟随跨域重定向（`Authorization` 头会外带）。
7. **不猜状态**：文件已换新 ≠ 已加载。跨进程状态用 `runtime_lock` 的标记 + 哈希确认
   表达，不要用"进程是否存在""文件 mtime 变了"代替加载确认。

## 2. 改完必做

```bash
python -m unittest discover -s tests     # 必须全绿
python zquota.py --selftest              # 动了 zquota 才需要
node --check inject-main.cjs             # 动了 loader 才需要
```

- 改了运行时文件：`python install.py`（标准形态），或让插件钩子下次会话自动同步。
- **改了 `inject-main.cjs` 必须完全退出并重开 ZCode**（冷文件；重启标记会让钩子提醒）。
- 改了 `overlay.js` / `zusage.py` / `zquota.py`：泵 2 秒内热更新，无需重启。
- 动了 `openspec/`：按 openspec 流程归档，不把提案残留在 `changes/`。

## 3. 双副本与"正在运行的文件"

- 仓库 → 数据目录（`~/.zcode/zcode-token-usage-statusbar`）由 `install.py` / 插件钩子
  同步，字节比较内容不变不写（无谓复制会让泵按 mtime 空重启）。
- 排查线上问题先确认数据目录里是哪一版：`python patch_install.py check`，再看数据目录
  的 `config.json` / `watchdog.log` / `diag-*.json` / `.plugin-restart-required`。
- 不要在数据目录里手改代码：下次同步会被覆盖。

## 4. 行为契约（不可回退的口径）

- **统计口径**：见 `tests/test_speed_window.py`——Claude 系未上报思考时按含思考窗口计时。
- **额度**：`zquota` 永不抛出；失败不覆盖上次成功值（`stale` + `age_s`）；退避分级见
  `.zcode/rules/quota.md`。
- **安装**：asar 替换失败时保留 TMP + 监控窗/看门狗收尾，绝不半途覆盖。
- **重启提醒**：只有 `.plugin-restart-required` 存在时提醒；客户端加载新代码后自动消失。
  任何"提醒永不消失"的回归都算 P0（通常是发布盖章缺失，见 injection.md）。

## 5. 文档纪律

- 易变事实（提交号、用例数、日期）不入文档；写机制与判定条件。
- `README.md`（英文）与 `README.zh-CN.md`（中文）是同源翻译，改一边必须改另一边。
