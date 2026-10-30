# -*- coding: utf-8 -*-
"""「ZCode Token 用量状态栏」安装监控 / 收尾进程。

职责（全程只读 + 唯一写操作 = 校验通过后的 TMP 替换）：
1. 存在待替换 TMP：等 ZCode 完全退出后自动完成 asar 替换（Windows 运行中 asar 被
   锁，os.replace 必被拒，所以必须等退出）。等待用进程句柄事件（WaitForMultipleObjects）
   而不是固定轮询——实测退出窗口只有几秒，10 秒轮询经常整段错过。
2. 替换前校验 TMP 的来源身份（源 asar 指纹 + TMP 指纹，见 patch_install.verify_pending）：
   ZCode 又升级过就拒绝用旧包覆盖新版，保留文件退出。
3. 可见模式：持续提醒重启 ZCode，替换成功后提示启动并自动退出。
   静默模式（ZUSAGE_MONITOR_QUIET=1，钩子/看门狗拉起）：无窗口，只写 watchdog.log。

单实例：用 app.asar.zusage.monitor.lock 排他锁去重，抢不到锁的实例静默退出。
最长存活 24 小时，避免遗留进程。

手动运行：python install_monitor.py [install_epoch 秒] [app.asar 路径] [运行时目录]
退出：替换完成/校验失败/超时后自动退出；或 Ctrl+C / 直接关闭窗口。

输出语言：运行时目录 config.json 的 "lang" 字段（zh/en，缺省 zh）——与安装器/CLI 同一来源。
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent.resolve()
sys.path.insert(0, str(HERE))

try:
    import patch_install as pi
    import runtime_lock
except Exception:      # 老安装目录缺模块：退化为直接 os.replace（无校验、无锁）
    pi = None
    runtime_lock = None

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
# 缺省仅兜底（patch_install.py 拉起时必传 argv[2]）：按平台取常见安装位置
ASAR = Path(sys.argv[2]) if len(sys.argv) > 2 else (
    Path("/Applications/ZCode.app/Contents/Resources/app.asar") if IS_MAC
    else Path("/opt/ZCode/resources/app.asar") if IS_LINUX
    else Path(r"D:\ZCode\resources\app.asar"))
RUNTIME_DIR = Path(sys.argv[3]) if len(sys.argv) > 3 else HERE
TMP = ASAR.with_name("app.asar.zusage.tmp")
POLL = 10            # 兜底轮询/事件等待的单次上限（秒）
BYE = 8              # 成功提示停留秒数
MAX_LIFETIME = 86400 # 最长存活 24 小时（防遗留进程）
QUIET = os.environ.get("ZUSAGE_MONITOR_QUIET") == "1"

# 关键：把 argv 的目标/运行时同步给 patch_install 的模块全局量。
# 监控是独立进程，不继承拉起者的 set_target()；不同步的话 finalize_pending 会去
# 操作平台默认路径（实测表现为"永远 missing、空转不退出"）。
if pi is not None:
    try:
        pi.set_target(ASAR)
        pi.set_runtime(RUNTIME_DIR)
    except Exception:
        pi = None


def load_lang():
    try:
        lang = json.loads((RUNTIME_DIR / "config.json").read_text(encoding="utf-8")).get("lang")
        return lang if lang in ("zh", "en") else "zh"
    except (OSError, ValueError):
        return "zh"


LANG = load_lang()


def L(zh, en):
    """双语输出：按 LANG 返回对应文案（与 install.py 同款约定）。"""
    return en if LANG == "en" else zh


def log(text):
    """静默模式的留痕（与看门狗共用 watchdog.log）。"""
    try:
        with (RUNTIME_DIR / "watchdog.log").open("a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%m-%d %H:%M:%S')}] 监控：{text}\n")
    except OSError:
        pass


def zcode_pids():
    """ZCode 主进程 PID 集合（Windows=tasklist，macOS/Linux=pgrep）；探测命令不可用时返回 None。"""
    if IS_WIN:
        try:
            r = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq ZCode.exe", "/FO", "CSV", "/NH"],
                capture_output=True, shell=False)
        except OSError:
            return None
        pids = set()
        for row in r.stdout.decode("gbk", "replace").splitlines():
            cells = [c.strip('"') for c in row.split('","')]
            if len(cells) >= 2 and cells[0].strip('"').lower() == "zcode.exe":
                pids.add(cells[1].strip('"'))
        return pids
    try:
        r = subprocess.run(["pgrep", "-x", "ZCode"], capture_output=True, shell=False)
    except OSError:
        return None
    if r.returncode == 1:
        return set()   # pgrep：无匹配进程
    if r.returncode != 0:
        return None    # pgrep 自身出错
    return {t.strip() for t in r.stdout.decode().split() if t.strip()}


def wait_any_exit(pids, timeout):
    """等 pids 中任一进程退出（Windows 句柄事件）；返回 True=有进程退出。
    非 Windows / 句柄不可用 / 进程数过多时退化为 sleep(timeout)。"""
    if not IS_WIN or not pids:
        time.sleep(timeout)
        return False
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.WaitForMultipleObjects.restype = wintypes.DWORD
        k32.WaitForMultipleObjects.argtypes = [
            wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 258
        deadline = time.monotonic() + max(0.0, timeout)
        remaining = [str(p) for p in pids]
        while remaining:
            group, remaining = remaining[:63], remaining[63:]
            handles = []
            for pid in group:
                try:
                    h = k32.OpenProcess(SYNCHRONIZE, False, int(pid))
                except (ValueError, OSError):
                    h = None
                if h:
                    handles.append(h)
            if not handles:
                continue
            ms = max(0, int((deadline - time.monotonic()) * 1000))
            arr = (wintypes.HANDLE * len(handles))(*handles)
            rc = k32.WaitForMultipleObjects(len(handles), arr, False, ms)
            for h in handles:
                k32.CloseHandle(h)
            if rc != WAIT_TIMEOUT:
                return True
        return False
    except Exception:
        time.sleep(timeout)
        return False


def acquire_instance_lock():
    """单实例锁：返回 (ok, lock)。lock 需在进程生命周期内持有。"""
    if pi is None or runtime_lock is None:
        return True, None
    lock = runtime_lock.FileLock(pi.monitor_lock_path(), timeout=0)
    if lock.acquire():
        return True, lock
    return False, None


def finalize_tmp():
    """校验并替换待收尾包。返回 published / busy / untrusted / missing。"""
    if pi is None:      # 老目录：无校验能力，退回直接替换
        try:
            os.replace(TMP, ASAR)
            return "published"
        except OSError:
            return "busy"
    return pi.finalize_pending()


def draw(lines):
    if QUIET:
        return
    # ANSI 清屏：不经过 shell（Windows 上 Python 3.6+ 已为控制台开启 VT 处理）
    print("\x1b[2J\x1b[H", end="")
    print("\n".join(lines), flush=True)


def main():
    epoch = float(sys.argv[1]) if len(sys.argv) > 1 else time.time()
    ok, _lock = acquire_instance_lock()
    if not ok:
        if not QUIET:
            print(L("另一个监控实例已在运行，本实例退出。",
                    "Another monitor instance is already running; exiting."))
        return
    # 启动时做一次深度校验（sha256）：之后的重试只用 stat，退出窗口内不做重活。
    if TMP.exists() and pi is not None:
        good, reason = pi.verify_pending(deep=True)
        if not good:
            log(f"待收尾包预校验失败（{reason}），取消收尾并保留文件")
            if not QUIET:
                print(L(f"待收尾包校验失败（{reason}），文件保留供排查。",
                        f"Pending package failed verification ({reason}); files kept for inspection."))
            return
    if IS_WIN and not QUIET:
        try:  # 不经过 shell 设置窗口标题
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(
                L("ZCode Token 用量状态栏 - 安装监控", "ZCode Token Usage Status Bar - Install Monitor"))
        except Exception:
            pass
    pids0 = zcode_pids() or set()   # 启动采集 ≈ 安装时刻的进程集合
    t0 = time.time()
    while True:
        if time.time() - t0 > MAX_LIFETIME:
            log("超过 24 小时仍未完成收尾，退出（待收尾包保留）")
            return
        pids = zcode_pids()
        now = time.time()
        if TMP.exists():
            result = finalize_tmp()
            if result == "published":
                log("暂存替换完成（来源/内容校验通过）")
                if QUIET:
                    return
                draw([L("[OK] asar 已解锁，替换完成。", "[OK] app.asar is unlocked; replacement finished."),
                      "", L("请启动 ZCode，悬浮条即可显示。",
                            "Start ZCode and the floating bar will appear.")])
                time.sleep(BYE)
                return
            if result == "untrusted":
                log("暂存包校验失败（源 asar 已变或 TMP 被改动），取消收尾并保留文件")
                if QUIET:
                    return
                draw([L("[!] 暂存包校验失败：ZCode 可能又更新过，旧包不能覆盖新版。",
                        "[!] Pending package failed verification: ZCode may have been updated again; the stale package will not overwrite the new build."),
                      L("文件已保留供排查；下次会话启动会按当前版本重新生成。",
                        "Files are kept for inspection; the next session start will rebuild from the current build.")])
                time.sleep(BYE)
                return
            if result == "missing":
                if TMP.exists():
                    # 模块 TMP 存在但 patch_install 看不到 = 目标路径不同步，别空转
                    log("待收尾包与目标路径不一致（内部状态异常），退出")
                    return
                continue      # 竞态：TMP 已被其它进程收尾，重新评估
            # busy：等进程退出事件（退出窗口只有几秒，轮询容易错过）
            if QUIET:
                wait_any_exit(pids, POLL)
                continue
            lines = [L("[等待] asar 仍被运行中的 ZCode 占用。",
                       "[waiting] app.asar is still held by the running ZCode."),
                     L("完全退出 ZCode 后本窗口自动完成替换（残留小进程不影响）。",
                       "Quit ZCode completely and this window finishes the replacement automatically (leftover stub processes are fine)."),
                     "", L("当前 ZCode 进程数：%d" % len(pids or ()),
                           "Current ZCode process count: %d" % len(pids or ()))]
        elif QUIET:
            log("待收尾包已不存在（可能由其它进程完成），退出")
            return
        elif pids is None:
            lines = [L("[!] 无法检测 ZCode 进程（探测命令不可用）。",
                       "[!] Cannot detect ZCode processes (detection command unavailable).")]
        elif not pids:
            lines = [L("ZCode 未运行。启动 ZCode 后悬浮条即生效。",
                       "ZCode is not running. The floating bar activates once you start ZCode.")]
        elif pids & pids0:
            lines = [L(">>>>>  请 重 启 Z Code  <<<<<", ">>>>>  PLEASE RESTART ZCODE  <<<<<"),
                     "",
                     L("安装已完成，但当前还是旧实例（%d 个进程），" % len(pids),
                       "Installation finished, but the old instance is still running (%d processes)," % len(pids)),
                     L("请完全退出 ZCode（所有窗口）后重新打开。",
                       "please quit ZCode completely (all windows) and reopen it.")]
        else:
            lines = [L("ZCode 已重启（新实例）。悬浮条应已出现；本窗口即将关闭。",
                       "ZCode restarted (new instance). The bar should be visible; this window will close.")]
            draw(lines)
            time.sleep(BYE)
            return
        el = int(now - t0)
        lines += ["", "-" * 52,
                  L("已等待 %02d:%02d | 每 %d 秒检测一次 | 完成或超时后自动退出" % (el // 60, el % 60, POLL),
                    "waited %02d:%02d | checking every %ds | exits on completion or timeout" % (el // 60, el % 60, POLL)),
                  L("本窗口可随时关闭；关闭后下次会话启动会重新拉起。",
                    "You may close this window; the next session start will relaunch it.")]
        draw(lines)
        if pids:
            wait_any_exit(pids, POLL)
        else:
            time.sleep(POLL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
