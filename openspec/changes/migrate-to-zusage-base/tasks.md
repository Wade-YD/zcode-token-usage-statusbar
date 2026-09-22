# migrate-to-zusage-base — 任务

顺序：仓库与试装 → 额度移植 → 速度改进 → 迁移收尾。安装/卸载 asar 均需 ZCode 关闭，任务 1.3 与 4.1 需用户配合重启。

## 1. 仓库与试装

- [x] 1.1 `git fetch --unshallow` 补全上游历史（61 提交）；`origin`→`upstream` 改名；本 change 工件已提交，验证：`git log --oneline | wc -l`=61、`git remote -v` 含 upstream
- [x] 1.2 新建 GitHub 私有仓库并推送（`Wade-YD/zcode-token-usage-statusbar`，private=true，全量历史；API 建仓——gh CLI 未安装、curl 中文描述曾致 JSON 解析失败，改 ASCII 描述解决）
- [ ] 1.3 试装新基座：**需用户在自有终端执行**（harness 安全钩子拦截 asar 写入的自动化执行）：`cd D:\桌面\私人图书馆\7-工具箱\1-project\zcode-token-usage-statusbar && python install.py` → 若 asar 被运行中的 ZCode 占用将弹监控窗、完全退出 ZCode 后自动完成替换 → 重启 ZCode 验证状态条与 diag 回写
- [x] 1.4 旧插件基线已记录：`patch-zcode.py --check` = 已注入、index.html 26614 字节 @ 316719209、backup 存在、asar 可写
- [x] 1.5 上游安全告警加固（基座引入时随修）：`install_monitor.py` 两处 `os.system` 改无 shell 写法（ANSI 清屏 / `SetConsoleTitleW`）、`patch_install.py` 的 asar 重建写入限同目录、`zusage.py` SSH 远端 script/python 参数白名单；验证：Mimosa 项目扫描通过、`zusage.py now` 冒烟正常

## 2. 额度移植

- [ ] 2.1 移植额度模块：三级 provider 解析（`_provider_entry`）+ 三家查询（DeepSeek /user/balance、智谱 /api/monitor/usage/quota/limit、OpenRouter /api/v1/credits）+ 自定义 `balanceUrl`/`balanceKind`，验证：命令行直调返回 BigModel 双窗口与实测数字一致
- [ ] 2.2 移植并测试防护闸：https 限定、公网解析校验、fake-IP `198.18.0.0/15` 豁免、拒绝跟随重定向；断言 `evil-deepseek.example` 不命中、`https://192.168.1.10/x` 被拒，验证：单测/手工断言全过
- [ ] 2.3 `zusage.py serve` 协议新增额度快照消息：300s 缓存、失败保留上次成功 + `age` 字段、并发去重，验证：连续请求只触发一次出网（日志可见）、断网后仍返回带 age 的旧数据
- [ ] 2.4 `overlay.js` 新增额度条目（独立项 + 设置开关 + 中英文案）：套餐窗口显示剩余%/重置时间，余额显示多币种；失败显示「x 分钟前」标注或分类原因，验证：状态条实测显示 5 小时/周双窗口，关闭开关后条目消失
- [ ] 2.5 `config.example.json` 增补服务商与端点位（不含任何真实 Key）；README 增补「额度功能与出网说明」，验证：示例文件无凭据字面量、README 明示唯一出网行为

## 3. 速度口径改进

- [ ] 3.1 速度相关 SQL 统一加 `status='completed'` 过滤（与 ccusage 口径一致），验证：构造/检索 cancelled 与 error 样本不再出现在速度统计与曲线
- [ ] 3.2 推理拆分：`reasoning_tokens > 0` 时并列「生成速度 / 可见文本速度」；=0 时不显示第二列，验证：deepseek 样本两值显著不同、GLM 样本只显示一项
- [ ] 3.3 TTFT 缺失处理：ttft 空值仍计入生成速度；覆盖率 <80% 回退端到端口径并加标注，验证：造一段 ttft 缺失样本，展示出现口径标注
- [ ] 3.4 多出口一致性：CLI / 状态条 / MCP 共用同一计算函数，验证：同一时刻三处查询最近一次调用速度数值等价

## 4. 迁移收尾

- [ ] 4.1 卸载旧插件：`patch-zcode.py --restore` + 从 `~/.zcode/cli/config.json` 的 `plugins.dirs` 移除 + 停用其 SessionStart 钩子；重启 ZCode，验证：只剩一套状态条、旧插件目录与 backup/ 完整保留备查
- [ ] 4.2 全量回归：状态条 9 项全部正常、额度数字与 BigModel 桌面端一致、速度三口径生效、多窗口会话隔离不受影响
- [ ] 4.3 旧仓归档标注：在 zcode-quota 的 `optimization-batch-1` 标注「架构迁移后作废/部分迁入 migrate-to-zusage-base」，避免后续误开工
- [ ] 4.4 README 增补迁移说明（含「两套注入不得跨版本升级并存」的规则与单步重打命令），验证：文档含重打命令与升级处置规则
