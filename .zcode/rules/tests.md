# 测试

运行：`python -m unittest discover -s tests`

## 硬约束

- 测试**不联网**、**不碰真实 `~/.zcode`**：一切数据目录/标记/临时文件都在
  `tempfile.TemporaryDirectory()` 里造。
- 跨进程/跨时序机制必须测**判定条件**，不只测 happy path。参照
  `tests/test_runtime_lock.py`：哈希一致才清、旧进程不清新标记、未发布不清除。
- 修 bug 先补一个会失败的用例，再修。
- 测试里不写死绝对路径（用 `Path(__file__).resolve().parent.parent` 定位仓库根）。

## 现有覆盖

| 文件 | 覆盖 |
|---|---|
| `test_speed_window.py` | 速度窗口口径（Claude 系含思考窗口、可见速度隐藏、聚合口径） |
| `test_runtime_lock.py` | 重启标记语义 + 跨进程文件锁互斥 |
| `test_session_hook.py` | tarball 解压面上限/路径逃逸、冷文件重启意图、提醒宽限 |

## 手工验收（改注入相关才需要）

1. `python patch_install.py check`：注入状态、TMP 残留。
2. `python install.py --dry-run`：打印动作不落盘。
3. 改 `inject-main.cjs` 后：完全退出 ZCode → 重开 → 数据目录
   `.plugin-restart-required` 应消失，`diag-*.json` 应更新。
