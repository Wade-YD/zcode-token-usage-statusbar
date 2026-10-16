---
description: 显示 ZCode token 用量（当前会话/今日/近几天/按会话/按工作区/速度/指定会话明细）
---
调用 MCP 工具 `mcp__zcode-token-usage-statusbar__token_usage`（server 名 zcode-token-usage-statusbar）查询 token 用量，把返回的报表原样整理后回复；用户要详细表格时用完整数值（带千分位），不要自行缩写。

MCP 工具不可用（未启用/未注册）时，回退用 Bash 直接跑 CLI（数据目录副本）：
`python "%USERPROFILE%\.zcode\zcode-token-usage-statusbar\zusage.py" <子命令>`
（CLI 子命令与 scope 的冒号语法不同：`days:N` → `days N`、`speed:N` → `speed N`、`current` → `now`。）

参数规则（$ARGUMENTS）：
- 为空或 "now" → scope=current
- "today" → scope=today
- "week" → scope=week；"近N天" → scope=days:N（N=天数；用户没给 N 就用 week）
- "sessions" → scope=sessions；"最近N个会话" → scope=sessions:N
- "models" → scope=models；"按模型近N天" → scope=models:N（N=天数；没给 N 就用 models）
- "speed" 或 "多快/速度" → scope=speed（最近 10 次调用速度：口径/覆盖率/中位速度/首字延迟）；"speed:N" → 最近 N 次
- "session:<会话id前缀>" → 原样传入（返回该会话总量+按模型+子代理明细）
- 统计某个工作区/项目（如"XX 工作区/项目用了多少 token"）→ scope=workspace:<目录名子串>。用户用中文或简称称呼工作区时，先从需求推断实际目录名的特征子串（缩写式称呼可直接传，工具支持单词首字母缩写匹配）；未命中时工具会列出候选目录，照着换更准的子串重试
- 其他输入 → 原样作为 scope 传入
