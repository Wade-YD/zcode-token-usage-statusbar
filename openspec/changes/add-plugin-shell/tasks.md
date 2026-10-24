# add-plugin-shell — 任务

## 1. 插件壳

- [x] 1.1 `plugin/.zcode-plugin/plugin.json` 清单 + 根 `marketplace.json`（本地市场，source=./plugin）
- [x] 1.2 `plugin/hooks/hooks.json`：SessionStart matcher `startup|resume` → `python session-start.py`（timeoutMs 60000）
- [x] 1.3 `session-start.py`：RUNTIME_FILES 字节比较同步（仓库→数据目录，清单以仓库 install.py 为唯一权威防漂移）+ usage.command.md→~/.zcode/commands/usage.md + 数据目录 `watchdog.main(False)`；异常吞掉 exit 0、err.log 留痕（32KB 截断）
- [x] 1.4 干跑验证：注入正常环境跑一次（预期 watchdog 日志"注入正常"，同步 0 文件）；repo 侧改一个字节再跑（预期同步 1 文件且泵热重载）
- [x] 1.5 市场形态引导安装：`bootstrap_runtime`（codeload main tarball；https + 域名白名单 + IP 字面量拒绝 + 20MB 上限 + 重定向逐跳校验；两阶段先落地 install.py 取权威清单再全套同步 + /usage 命令模板 + 最小 config.json 仅缺失时；失败 6h 冷却 bootstrap-state.json，冷却期内不碰网络）
- [x] 1.6 引导安装验证：模拟市场形态（plugin/ 拷至无仓库根位置）实跑钩子——数据目录从无到有、运行时 8 文件 + usage.md + config.json 就位、watchdog 注入自检走通；冷却期内二次运行不发起下载
- [x] 1.7 市场形态运行时刷新：`refresh_runtime`（≥24h 检查间隔，失败置 refresh_error 退避 1h；与引导安装同一受控下载通道与 `_apply_tarball` 两阶段落地；字节比较内容不变不写；不碰 config.json/MCP；引导成功播种 last_refresh 防当场二次拉取；仓库形态不在线刷新）
- [x] 1.8 刷新验证（隔离 HOME 三态）：检查期内返回 None 且零网络；到期真实网络刷新 synced=[] 且状态落档；下载失败（monkeypatch）→ 返回 [] + watchdog.log 留痕 + 退避期内二次调用零网络

## 2. 速度口径

- [x] 2.1 `hides_reasoning()` + `rate_of()` 行元组扩 started_at/model_id、含思考窗口前移；`speed_stats`/`_session_snapshot` 查询列随动（追加列尾，原索引不变）
- [x] 2.2 `tests/test_speed_window.py`：claude 首字 13661ms/总 15087ms/输出 1074 → 71.2 而非 78.6 误口径；deepseek reasoning>0 同前移且可见速度保持首字窗口；普通模型口径不变；claude 缺 started_at 时优雅退化
- [x] 2.3 真库对照：`speed_stats`（MCP speed）跑通，GLM 主力（reasoning 恒 0）数值口径不变

## 3. patch_install standalone

- [x] 3.1 `__main__` 解析 `--asar/--runtime/--finalize`（含 `=` 形式、缺值不误吞）+ env `ZCODE_ASAR`/`ZCODE_RUNTIME` 兜底；Windows 默认优先 `%LOCALAPPDATA%\Programs\ZCode`
- [x] 3.2 运行中替换失败提示语带 `--asar/--runtime` 全路径；`check` 的 TMP 提示同步；文档字符串补 standalone 用法
- [x] 3.3 验证：`check --asar <真实 asar>` 正常；`install --finalize` 断言 finalize=True 透传（mock，不落盘）

## 4. 收尾

- [x] 4.1 代码审查 → 修 findings（oracle 派发撞并发限流，主对话四维自审替代：安全——URL 守卫/tar 白名单/下载执行信任链与仓库形态一致；正确性——原子写/冷却竞态/两阶段计数/BaseException 收口；规格 MUST 逐条核对；3.7-3.14 兼容。无 P0/P1；P2 记录：市场形态已安装运行时无自动更新路径，属既有设计限制，升级仍走 install.py）
- [x] 4.2 注册插件 → 实跑钩子验证：模拟市场位置 + 真实 HOME 两次运行（首次引导安装 9 文件 + 注入自检走暂存+通知路径；二次跳过引导仅自检，均 exit 0、err.log 零新增）；随后主安装补齐 MCP 注册/计划任务/asar 暂存注入，watchdog.log 留痕齐全
- [x] 4.3 提交推送；归档 change（归档暂缓——引导安装的暂存收尾待 ZCode 完全退出后实机确认一轮再进行）

## 5. 遗留修复（1.2.0）

- [x] 5.1 控制台输出解码崩溃：中文 Windows + Python 3.14/UTF-8 模式下 `text=True` 默认按 UTF-8 解码 schtasks/tasklist/powershell 的 GBK 输出，读线程抛 UnicodeDecodeError 丢掉整个结果（任务实际已注册成功）——install.py 增 `run_text`（字节捕获 + `_decode_console` mbcs→utf-8→replace 兜底；参数列表类型校验 + 显式 shell=False），调用点全切换：install.py register_watchdog/remove_watchdog、patch_install.py client_running（替换硬编码 encoding="gbk"）/语法检查/_delete_watchdog_task、zusage.py doctor 泵进程检测；install_monitor.py 原有 decode("gbk","replace") 输出纯 ASCII CSV 字段、不会崩，保持不动
- [x] 5.2 解码验证：`_decode_console` GBK 字节单测（b"\xd6\xd0\xce\xc4"→"中文"）+ run_text 拒绝非列表 cmd + 真实 schtasks 查询走通（本机 zh-CN）+ 重跑主安装 stderr 无 UnicodeDecodeError
- [x] 5.3 暂存收尾门槛修复（看门狗 + 安装监控窗）：收尾条件从"tasklist 无 ZCode 进程"改为"直接尝试原子替换、文件锁失败留下周期重试"——Electron 退出后常残留 crashpad/tray 存根进程（不锁 asar），按进程表等会永远等不下去（实测 21 个残留进程卡收尾 10+ 小时，用户退出重开仍无状态条的根因）；self-heal 规格"文件锁时留待下一周期收尾"语义不受影响（本就更贴合）。验证：ZCode 全程运行中（真实进程存活）假 asar 暂存替换成功（state 复位、log"asar 已解锁"）；真机钩子重跑后 log 如期"收尾暂不可行（asar 仍被占用）"，exit 0、err.log 零新增
- [x] 5.4 安装监控脚本补入 RUNTIME_FILES（原清单缺它 → 标准安装的数据目录没有此文件 → 拉起处 `if not monitor.exists(): return` 静默跳过，10 秒轮询收尾器在标准安装从未生效，安装器"已弹出监控窗口"输出名不副实）——清单 8→9 文件；仓库形态钩子同步 10 文件验证落位、逐字节一致
