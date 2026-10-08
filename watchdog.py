# -*- coding: utf-8 -*-
"""zusage 看门狗：计划任务每 10 分钟运行一次，检查 asar 注入行是否被 ZCode 更新抹掉。

行为（幂等）：
    注入在   → 记录日志；若此前通知过则复位防抖状态
    注入缺失 → 调 patch_install 重打（自检 + 原子替换）：
               ZCode 运行中 → 修复成功后弹一次系统通知「重启 ZCode 生效」（防抖）
               ZCode 未运行 → 静默修复（下次启动直接生效）
    任何异常 → 写日志后静默退出，绝不弹窗干扰、不留残留进程

由 install.py 注册计划任务（zusage-watchdog）运行；手动调试：
    python watchdog.py [--verbose]
"""
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))


def _pre_log(text: str) -> None:
    """最低配日志：import 失败时 log() 尚未定义，用它留痕。"""
    try:
        with (HERE / "watchdog.log").open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%m-%d %H:%M:%S')}] {text}\n")
    except OSError:
        pass


try:
    import install as installer        # noqa: E402  同目录，复用 asar 探测与数据目录
    import patch_install as pi         # noqa: E402
except Exception as exc:
    # 标准安装下 RUNTIME_FILES 含 install.py/patch_install.py；缺失说明安装不完整，
    # 留痕后静默退出（计划任务下周期会随安装修复而恢复）
    _pre_log(f"依赖模块导入失败（{exc!r}），本轮看门狗跳过")
    sys.exit(0)

LOG = HERE / "watchdog.log"
STATE = HERE / "watchdog-state.json"
LOG_MAX = 64 * 1024
NOTIFY_CMD_TIMEOUT = 20


def log(text: str) -> None:
    """带轮转的追加日志：超 64KB 保留尾部 16KB，防止无限增长。"""
    try:
        if LOG.exists() and LOG.stat().st_size > LOG_MAX:
            old = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
            LOG.write_text("\n".join(old[-60:]) + "\n", encoding="utf-8")
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%m-%d %H:%M:%S')}] {text}\n")
    except OSError:
        pass


def state_load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def state_save(data: dict) -> None:
    try:
        STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def zcode_running() -> bool:
    """ZCode 主进程是否在运行（tasklist/pgrep 尽力而为；查不到按未运行处理）。"""
    try:
        if os.name == "nt":
            r = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq ZCode.exe", "/FO", "CSV", "/NH"],
                capture_output=True, timeout=15)
            return b"zcode.exe" in r.stdout.lower()
        r = subprocess.run(["pgrep", "-x", "ZCode"], capture_output=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def notify(title: str, text: str) -> bool:
    """Windows 气泡通知（无需第三方模块）；失败静默返回 False。"""
    if os.name != "nt":
        return False
    try:
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "Add-Type -AssemblyName System.Drawing;"
            "$n = New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon = [System.Drawing.SystemIcons]::Information;"
            "$n.Visible = $true;"
            "$n.ShowBalloonTip(10000, '" + title.replace("'", "''") + "', '"
            + text.replace("'", "''") + "', [System.Drawing.ToolTipIcon]::Info);"
            "Start-Sleep -Seconds 6;"
            "$n.Dispose()"
        )
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           capture_output=True, timeout=NOTIFY_CMD_TIMEOUT)
        return r.returncode == 0
    except Exception:
        return False


def main(verbose: bool) -> int:
    installer_chatter = []
    if not verbose:
        # 安装器函数会 print 过程信息；计划任务无人看，收进日志目录的缓冲
        class _Tee:
            def write(self, s):
                installer_chatter.append(s)
                return len(s)
            def flush(self):
                pass
        sys.stdout = _Tee()

    asar = installer.find_installed_asar(None)
    if not asar or not asar.is_file():
        log("未定位到 app.asar（ZCode 未安装或换了位置），本轮跳过")
        return 0
    pi.set_target(asar)
    try:
        pi.set_runtime(installer.DATA_DIR)
    except Exception:
        pass   # --dev 安装没有数据目录副本，set_runtime 缺省即可

    entry = pi.entry_bytes_of(asar)
    exact = pi.INJECT_LINE.encode() in entry   # 精确匹配当前目录注入行（宽松 RE 会放过指向死路径的旧行）
    st = state_load()
    if exact:
        # 注入在位：复位防抖/暂存状态（下次缺失可再次通知），记录一次健康检查
        if st.get("notified_reason") or st.get("staged_key"):
            log("注入已恢复，复位通知/暂存状态")
            state_save({})
        log("注入正常，无需修复")
        return 0

    if pi.ZUSAGE_LINE_RE.search(entry):
        log("注入行存在但未指向当前运行目录（dev→标准迁移或目录变更），重打包重定向")

    stamp = f"staged@{int(asar.stat().st_mtime)}"
    if st.get("staged_key") == stamp:
        # 暂存收尾（P0-3）：ZCode 已退出即可原子替换；.tmp 被清走则复位，下周期重新打包
        if pi.TMP.is_file() and not zcode_running():
            try:
                os.replace(pi.TMP, pi.ASAR)
                log("ZCode 已退出，暂存修复收尾完成")
                state_save({})
            except OSError as e:
                log(f"收尾失败（ZCode 仍占用？）：{e}")
        elif not pi.TMP.is_file():
            log(".tmp 消失（被外部清理？），复位状态")
            state_save({})
        else:
            log("ZCode 仍在运行，暂存修复继续等待")
        return 0
    log("检测到注入行缺失（多半是 ZCode 升级覆盖了 asar），自动重打")
    running = zcode_running()
    ok = pi.install(quiet=True)   # 无人值守（P0-2）：不弹监控窗，通知由看门狗负责
    log(f"重打结果 ok={ok}（ZCode {'运行中' if running else '未运行'}）")
    if not ok:
        # 暂存场景也要让用户知道：否则修复躺在 tmp 里没人重启就永远不生效
        log(".tmp 已暂存，等待 ZCode 退出后下个周期收尾")
        st = state_load()
        st["staged_key"] = stamp
        if running and st.get("notified_key") != stamp:
            if notify("ZCode 状态条修复已暂存",
                      "完全退出 ZCode 后将自动完成替换，随后启动 ZCode 即可。"):
                st["notified_key"] = stamp
                st["notified_at"] = time.strftime("%m-%d %H:%M:%S")
                st["notified_reason"] = "staged"
                log("已弹通知（staged），提醒用户退出 ZCode 以完成替换")
            else:
                log("通知发送失败（不影响暂存修复）")
        state_save(st)
        return 0

    st = state_load()
    if running:
        key = f"missing@{int(asar.stat().st_mtime)}"
        if st.get("notified_key") != key:
            sent = notify("ZCode 状态条已自动修复",
                          "ZCode 更新抹掉了状态条注入，已自动补回。重启 ZCode 后生效。")
            st.update(notified_key=key, notified_at=time.strftime("%m-%d %H:%M:%S"),
                      notified_reason="injection-missing")
            state_save(st)
            log(f"已弹通知（sent={sent}），提醒用户重启 ZCode")
        else:
            log("同一问题已通知过，跳过重复通知")
    else:
        log("ZCode 未运行，静默修复完成，下次启动即生效")
    return 0


if __name__ == "__main__":
    try:
        verbose = "--verbose" in sys.argv
        sys.exit(main(verbose))
    except Exception:
        # 检查器自身故障：留痕后静默退出，绝无弹窗与残留
        try:
            log("watchdog 异常退出：\n" + traceback.format_exc()[-500:])
        except Exception:
            pass
        sys.exit(0)
