# add-plugin-shell — 任务

## 1. 插件壳

- [x] 1.1 `plugin/.zcode-plugin/plugin.json` 清单 + 根 `marketplace.json`（本地市场，source=./plugin）
- [x] 1.2 `plugin/hooks/hooks.json`：SessionStart matcher `startup|resume` → `python session-start.py`（timeoutMs 60000）
- [x] 1.3 `session-start.py`：RUNTIME_FILES 字节比较同步（仓库→数据目录，清单以仓库 install.py 为唯一权威防漂移）+ usage.command.md→~/.zcode/commands/usage.md + 数据目录 `watchdog.main(False)`；异常吞掉 exit 0、err.log 留痕（32KB 截断）
- [x] 1.4 干跑验证：注入正常环境跑一次（预期 watchdog 日志"注入正常"，同步 0 文件）；repo 侧改一个字节再跑（预期同步 1 文件且泵热重载）

## 2. 速度口径

- [x] 2.1 `hides_reasoning()` + `rate_of()` 行元组扩 started_at/model_id、含思考窗口前移；`speed_stats`/`_session_snapshot` 查询列随动（追加列尾，原索引不变）
- [x] 2.2 `tests/test_speed_window.py`：claude 首字 13661ms/总 15087ms/输出 1074 → 71.2 而非 78.6 误口径；deepseek reasoning>0 同前移且可见速度保持首字窗口；普通模型口径不变；claude 缺 started_at 时优雅退化
- [x] 2.3 真库对照：`speed_stats`（MCP speed）跑通，GLM 主力（reasoning 恒 0）数值口径不变

## 3. patch_install standalone

- [x] 3.1 `__main__` 解析 `--asar/--runtime/--finalize`（含 `=` 形式、缺值不误吞）+ env `ZCODE_ASAR`/`ZCODE_RUNTIME` 兜底；Windows 默认优先 `%LOCALAPPDATA%\Programs\ZCode`
- [x] 3.2 运行中替换失败提示语带 `--asar/--runtime` 全路径；`check` 的 TMP 提示同步；文档字符串补 standalone 用法
- [x] 3.3 验证：`check --asar <真实 asar>` 正常；`install --finalize` 断言 finalize=True 透传（mock，不落盘）

## 4. 收尾

- [ ] 4.1 Oracle 审查 → 修 findings
- [ ] 4.2 注册插件（config.json plugins.dirs，改前备份）→ 新会话实跑钩子验证（watchdog.log 留痕）——注册已完成，实跑验证随下次会话启动自然发生
- [ ] 4.3 提交推送；归档 change
