# migrate-to-zusage-base — 任务

顺序：仓库与试装 → 额度移植 → 速度改进 → 迁移收尾。安装/卸载 asar 均需 ZCode 关闭，任务 1.3 与 4.1 需用户配合重启。

## 1. 仓库与试装

- [x] 1.1 `git fetch --unshallow` 补全上游历史（61 提交）；`origin`→`upstream` 改名；本 change 工件已提交，验证：`git log --oneline | wc -l`=61、`git remote -v` 含 upstream
- [x] 1.2 新建 GitHub 私有仓库并推送（`Wade-YD/zcode-token-usage-statusbar`，private=true，全量历史；API 建仓——gh CLI 未安装、curl 中文描述曾致 JSON 解析失败，改 ASCII 描述解决）
- [x] 1.3 试装完成（用户自有终端执行 + 重启验证）：asar 注入标记命中；运行时目录就位（config/diag/inject-main/overlay/zusage）；diag 心跳持续更新（23:06:06→23:08:22）；`#zusage-bar` 已渲染（838×32 @ y=871，below-card 模式）；泵常驻 v10 且当前会话已绑定（activeMap/wants=sess_bf225fce）；MCP 注册（zcode-token-usage-statusbar）与 /usage 命令就位；旧 api-quota 状态条共存中
- [x] 1.4 旧插件基线已记录：`patch-zcode.py --check` = 已注入、index.html 26614 字节 @ 316719209、backup 存在、asar 可写
- [x] 1.5 上游安全告警加固（基座引入时随修）：`install_monitor.py` 两处 `os.system` 改无 shell 写法（ANSI 清屏 / `SetConsoleTitleW`）、`patch_install.py` 的 asar 重建写入限同目录、`zusage.py` SSH 远端 script/python 参数白名单；验证：Mimosa 项目扫描通过、`zusage.py now` 冒烟正常

## 2. 额度移植

- [x] 2.1 额度模块 `zquota.py` 完成：三级 provider 解析 + 三家查询 + 自定义 `balanceUrl`/`balanceKind` + 5 分钟缓存（失败保留旧值 + age_s）+ 短超时（5s）+ 后台单飞刷新 + `cached_payload` 只读路径；验证：实机查询 BigModel 返回双窗口（5h 100% / 周 24%，与钩子数字吻合）、CREDIT_LIMIT 标签已映射为「5 小时/每周」
- [x] 2.2 防护闸完成并自检 11/11 通过：https 限定、公网解析校验、fake-IP `198.18.0.0/15` 豁免、拒绝跟随重定向；断言 `evil-deepseek.example` 不命中、`192.168.1.10`/`127.0.0.1`/`localhost` 被拒
- [x] 2.3 serve 协议接线完成（不新增消息、零泵改动）：`snapshot(include_quota=True)` 仅 serve 路径挂 `quota` 顶层键；CLI 新增 `quota` 子命令；`json`/SSH 远端路径不携带；验证：serve 首帧 `warming` 不阻塞、Goat 场景 `unsupported` 语义正确、json 路径无 quota 键
- [x] 2.4 `overlay.js` 新增额度条目（`show.quota` 开关 + 设置面板项 + 中英双语 + tooltip + VERSION v62）：套餐显示各窗口剩余%（三档变色）+ 重置时间，余额显示币种金额；失败/过期保留上次数据并标注「x 分钟前」，Goat 类走「暂不支持」语义；`node --check` 通过。待运行时同步后目视验证（跑一次 install.py）
- [x] 2.5 `config.example.json` 增补 `quota.ttl_s` 示例（无凭据）；双语 README 更新唯一出网声明、新增 ⑧ 额度 章节（设置面板顺延 ⑨）、英文 CLI 列表加 `quota`

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
