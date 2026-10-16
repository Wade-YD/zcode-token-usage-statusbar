# -*- coding: utf-8 -*-
"""ZCode SessionStart 钩子：运行时同步 + 注入自检（zusage 插件壳，零依赖）。

每次会话启动（startup|resume）由宿主拉起，依次：
  1) 运行时同步：按 RUNTIME_FILES 清单把仓库运行时复制到数据目录（字节比较，
     内容不变不写——泵以 mtime 热重载，无谓复制会让泵空重启；复制走临时文件 +
     os.replace 原子落盘，防钩子被杀留下半文件喂给泵/看门狗）；/usage 命令模板
     同步到全局命令目录。更新仓库后无需手动重跑 install.py；清单里新增的文件
     也会自动补齐（09-30"数据目录缺新文件→看门狗 import 死亡 10 天"的根治）。
     清单以仓库 install.py 为唯一权威定义处，此处不重复维护文件列表。
     仓库不可用（市场安装形态）时同步静默跳过，自检仍走数据目录副本；
     数据目录不存在时本钩子即引导安装（同步全套运行时并自动注入）。
  2) 注入自检：调数据目录 watchdog.main(False)——与计划任务同一实现（精确
     匹配、暂存收尾、通知防抖），会话启动即查一次，早于 10 分钟周期。

安全边界：任何异常（含 watchdog 导入失败时的 SystemExit）吞掉后以 0 退出
（绝不阻断会话启动），失败留痕 hooks/session-start.err.log（带时间戳，
超 32KB 截断尾部）。
"""
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.parent.resolve()   # .../plugin
REPO = PLUGIN_ROOT.parent                              # 仓库根（运行时源）
ERR_LOG = PLUGIN_ROOT / "hooks" / "session-start.err.log"
ERR_LOG_MAX = 32 * 1024
COMMAND_NAME = "usage.command.md"


def _paths():
    """仓库 install.py 是 RUNTIME_FILES / DATA_DIR 的唯一权威定义处（防清单漂移）。
    仓库不可用（市场安装形态，插件包内无运行时源）时回退默认数据目录 + 空清单：
    同步静默跳过，自检照跑。"""
    try:
        sys.path.insert(0, str(REPO))
        import install
        return Path(install.DATA_DIR), list(install.RUNTIME_FILES)
    except Exception:
        return Path.home() / ".zcode" / "zcode-token-usage-statusbar", []


def _copy_if_changed(src, dst):
    """字节比较后原子覆盖（mkstemp + os.replace），半文件不会暴露给泵/看门狗。"""
    if dst.is_file() and src.read_bytes() == dst.read_bytes():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(dst.parent), prefix="." + dst.name + ".")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(src.read_bytes())
        os.replace(tmp_name, dst)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return True


def sync_runtime(data_dir, runtime_files):
    """仓库 → 数据目录 / 全局命令目录；返回实际发生复制的文件名列表。"""
    synced = []
    for name in runtime_files:
        src = REPO / name
        if src.is_file() and _copy_if_changed(src, data_dir / name):
            synced.append(name)
    cmd_src = REPO / COMMAND_NAME
    cmd_dst = Path.home() / ".zcode" / "commands" / "usage.md"
    if cmd_src.is_file() and _copy_if_changed(cmd_src, cmd_dst):
        synced.append(COMMAND_NAME)
    return synced


def _log_err():
    """失败留痕：追加带时间戳的堆栈尾部，超限截断到尾部 8KB。"""
    try:
        if ERR_LOG.exists() and ERR_LOG.stat().st_size > ERR_LOG_MAX:
            ERR_LOG.write_text(
                ERR_LOG.read_text(encoding="utf-8", errors="replace")[-8000:],
                encoding="utf-8")
        with ERR_LOG.open("a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%m-%d %H:%M:%S"),
                                   traceback.format_exc()[-1500:]))
    except Exception:
        pass


def main():
    try:
        data_dir, runtime_files = _paths()
        synced = sync_runtime(data_dir, runtime_files)
        if synced:
            try:
                with (data_dir / "watchdog.log").open("a", encoding="utf-8") as f:
                    f.write("[%s] 插件钩子同步运行时 %d 个文件：%s\n"
                            % (time.strftime("%m-%d %H:%M:%S"),
                               len(synced), ", ".join(synced)))
            except OSError:
                pass
        sys.path.insert(0, str(data_dir))
        import watchdog
        watchdog.main(False)   # 注入自检：与计划任务同一实现（verbose=False 收输出）
    except BaseException:
        # SystemExit 也必须接住：watchdog 在依赖导入失败时模块级 sys.exit(0)，
        # 若放行则钩子带非零码/traceback 结束且无留痕（违反 spec 的 MUST）。
        _log_err()
    return 0


if __name__ == "__main__":
    sys.exit(main())
