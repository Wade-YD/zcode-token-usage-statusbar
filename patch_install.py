# -*- coding: utf-8 -*-
"""ZCode app.asar 注入/卸载工具（token 用量悬浮条）。

用法：
  python patch_install.py install   # 安装/重定向（幂等；自动替换旧注入行，含指向旧目录的历史行；
                                    # 客户端运行中则生成 .tmp 待退出后替换）
  python patch_install.py install --finalize  # 客户端退出后完成替换
  python patch_install.py install --finalize --asar <app.asar 路径> --runtime <运行时目录>
                                    # standalone（不经 install.py / watchdog 拉起）须显式指定
                                    # 非默认安装位置的目标，否则按平台默认猜测，自定义路径下
                                    # 会猜错；也可各设一次环境变量 ZCODE_ASAR / ZCODE_RUNTIME
                                    # 代替（命令行参数优先）。--runtime 指注入行指向的运行时
                                    # 目录（标准安装即数据目录）；remove/check 不需要它。
  python patch_install.py remove    # 卸载（从 asar 剥离本工具注入行，不依赖备份）
  python patch_install.py check     # 检查当前注入状态与入口语法

原理：asar 主入口 out/main/index.js 尾部追加一行 dynamic import(loader)（ESM 入口，实测见下）。
Electron fuses: EmbeddedAsarIntegrityValidation=0（已实测），改动 asar 可正常加载。

安装/卸载均只增删本工具自己的注入行（ZUSAGE_LINE_RE 识别，不限目录的历史行一并匹配），
不制作也不依赖 asar 备份：卸载不会连带移除其它工具后装的注入行；ZCode 官方升级覆盖 asar
之后再卸载，也不会把客户端降级回安装时的旧版本。历史版本遗留的 app.asar.zusage.bak
会在成功安装/卸载后自动清理。

安装成功后自动弹出常驻监控窗口（install_monitor.py，每 10 秒检测一次）：提醒重启
ZCode、确认注入加载后自动退出；运行中替换失败（.tmp 待替换）时还会在 ZCode 退出后
自动完成替换 —— 该收尾职责已由监控窗口承担，旧的 schtasks 计划任务方案废弃。
"""
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path

from install import run_text   # 同级兄弟（RUNTIME_FILES 恒同目录）：控制台输出本地代码页安全解码

try:
    import runtime_lock          # 重启意图标记（RUNTIME_FILES 一员；老安装缺它时退化为无标记）
except ImportError:              # pragma: no cover - 只服务历史安装目录
    runtime_lock = None

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

if IS_WIN:
    # 现代安装器默认布局是 %LOCALAPPDATA%\Programs\ZCode；旧默认 D:\ZCode 仅在
    # 自定义根目录安装时命中，standalone 猜错会指向不存在的文件（standalone 可用
    # --asar / 环境变量 ZCODE_ASAR 显式指定；install.py/watchdog 拉起时经
    # set_target() 纠正，不受默认值影响）。
    _local = os.environ.get("LOCALAPPDATA")
    if _local:
        ASAR_DEFAULT = Path(_local) / "Programs" / "ZCode" / "resources" / "app.asar"
        ZCODE_EXE_DEFAULT = ASAR_DEFAULT.parent.parent / "ZCode.exe"
    else:
        ASAR_DEFAULT = Path(r"D:\ZCode\resources\app.asar")
        ZCODE_EXE_DEFAULT = Path(r"D:\ZCode\ZCode.exe")
elif IS_MAC:
    ASAR_DEFAULT = Path("/Applications/ZCode.app/Contents/Resources/app.asar")
    ZCODE_EXE_DEFAULT = Path("/Applications/ZCode.app/Contents/MacOS/ZCode")
else:
    ASAR_DEFAULT = Path("app.asar")   # 其它平台无默认位置，用 --asar / install.py 指定
    ZCODE_EXE_DEFAULT = None
ASAR = ASAR_DEFAULT          # 可被 set_target() 改指其它安装位置（install.py 探测后调用）
BAK = ASAR.with_name("app.asar.zusage.bak")
TMP = ASAR.with_name("app.asar.zusage.tmp")
# 待收尾包的来源身份（JSON）：记录 repack 时的源 asar 指纹与 TMP 指纹。收尾前校验，
# 防止 ZCode 再次升级后拿旧包覆盖新版客户端（那种回滚比"没状态条"更糟）。
TMP_META = ASAR.with_name("app.asar.zusage.tmp.json")
MONITOR_LOCK_NAME = "app.asar.zusage.monitor.lock"   # 监控实例锁（同目录，随 ASAR 走）
RUNTIME_EXPLICIT = False     # set_runtime() 显式指定过运行时（install.py/watchdog 拉起时为 True；
                             # standalone __main__ 仅在用户传了 --runtime/ZCODE_RUNTIME 时为 True）
_STANDALONE = False          # __main__ 直接运行时置 True（被导入时恒 False，守卫不生效）
HERE = Path(__file__).parent.resolve()
# 运行时目录：install.py 标准安装时指向数据目录（~/.zcode/zcode-token-usage-statusbar，
# 经 set_runtime() 切换）；独立运行本脚本或 --dev 安装时保持仓库目录（向后兼容）。
RUNTIME = HERE
LOADER = RUNTIME / "inject-main.cjs"
ENTRY = "out/main/index.js"
# 入口是 ESM（package.json type:module，实测 2026-09-04）：必须用 dynamic import（CJS/ESM 通用），
# 指向 .cjs loader（在 type:module 包里仍按 CJS 加载，require 可用）；asar 内相对路径不可用，用绝对 file URL。
INJECT_LINE_TMPL = (
    '\n;import("{url}").then(() => null, (e) => console.error("[zusage] load failed", e));'
)
INJECT_LINE = INJECT_LINE_TMPL.format(url=(RUNTIME / "inject-main.cjs").as_uri())
# 匹配任何 zusage 注入行（不限当前目录）——迁移/重装/标准↔dev 互切时剥离旧行用
ZUSAGE_LINE_RE = re.compile(
    rb'\n;import\("[^"]*inject-main\.cjs"\)\.then\(\(\) => null, '
    rb'\(e\) => console\.error\("\[zusage\] load failed", e\)\);'
)
ZCODE_EXE = ZCODE_EXE_DEFAULT
ALIGN = 4
BLOCK = 4194304

# 输出语言：惰性读 config.json 的 "lang"（运行时目录优先，其次数据目录；缺省 zh）。
# install.py 标准安装流程里 set_runtime() 先于 install()，此时序下读到的就是本次安装写入的语言。
_LANG = None


def L(zh, en):
    global _LANG
    if _LANG is None:
        _LANG = "zh"
        # sudo 运行时（Linux 系统级安装）config 在真实用户的家目录，Path.home() 会指到 root
        sudo_home = None
        if sys.platform != "win32" and os.environ.get("SUDO_USER"):
            try:
                if os.geteuid() == 0:
                    import pwd
                    sudo_home = Path(pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir)
            except (KeyError, OSError, AttributeError):
                pass
        bases = [RUNTIME,
                 Path.home() / ".zcode" / "zcode-token-usage-statusbar"]
        if sudo_home:
            bases.append(sudo_home / ".zcode" / "zcode-token-usage-statusbar")
        for base in bases:
            try:
                lang = json.loads((base / "config.json").read_text(encoding="utf-8")).get("lang")
                if lang in ("zh", "en"):
                    _LANG = lang
                    break
            except (OSError, ValueError):
                pass
    return en if _LANG == "en" else zh


def set_runtime(path):
    """改运行时目录（LOADER/INJECT_LINE 随动）。ZCode 端泵的全套路径都基于 __dirname，
    运行时副本目录即自洽运行，无需改运行时代码。"""
    global RUNTIME, LOADER, INJECT_LINE, RUNTIME_EXPLICIT
    RUNTIME = Path(path)
    LOADER = RUNTIME / "inject-main.cjs"
    INJECT_LINE = INJECT_LINE_TMPL.format(url=LOADER.as_uri())
    RUNTIME_EXPLICIT = True


def zcode_exe_for(asar_path):
    """从 asar 位置推 ZCode 可执行文件（语法自检用）：Windows 布局 <root>/ZCode.exe，
    macOS bundle 布局 <root>/MacOS/ZCode，Linux 布局 <root>/zcode（deb/rpm 官方包，/opt/ZCode）；
    非 ZCode 布局（如 %LOCALAPPDATA%\\Programs 下其它 Electron 应用）返回 None——
    客户端进程名随之失去语义（客户端是别的应用），语法检查自动跳过。"""
    root = asar_path.parent.parent
    if IS_WIN:
        exe = root / "ZCode.exe"
        return exe if exe.is_file() else None
    if IS_MAC:
        return root / "MacOS" / "ZCode"
    if sys.platform.startswith("linux"):
        return root / "zcode"
    return None


def set_target(asar_path):
    """改指目标安装位置（BAK/TMP/TMP_META/ZCODE_EXE 随动）。ZCODE_EXE 只用于语法自检，缺了自动跳过。"""
    global ASAR, BAK, TMP, TMP_META, ZCODE_EXE
    asar_path = Path(asar_path)
    ASAR = asar_path
    BAK = asar_path.with_name("app.asar.zusage.bak")
    TMP = asar_path.with_name("app.asar.zusage.tmp")
    TMP_META = asar_path.with_name("app.asar.zusage.tmp.json")
    ZCODE_EXE = zcode_exe_for(asar_path)


def client_running():
    try:
        if IS_WIN:
            out = run_text(
                ["tasklist", "/FI", "IMAGENAME eq ZCode.exe"],
            ).stdout
            return "ZCode.exe" in out
        # macOS/Linux：pgrep 精确匹配主进程名；误判"未运行"在 POSIX 上也无害
        # （运行中替换本就允许：rename 语义，旧进程继续读旧 inode）
        return subprocess.run(["pgrep", "-x", "ZCode"], capture_output=True).returncode == 0
    except Exception:
        return True  # 查不到时按在跑处理，走安全路径


def launch_monitor(epoch):
    """开新控制台窗口跑安装监控（提醒重启 + 确认生效 + .tmp 收尾）；
    macOS/Linux 无控制台概念，后台静默跑（收尾与自动退出照常）。设 ZUSAGE_NO_MONITOR=1 可禁用。"""
    if os.environ.get("ZUSAGE_NO_MONITOR"):
        return None
    _spawn_monitor(quiet=False, epoch=epoch)
    return True


# ---------- asar 读写 ----------

def read_header(f):
    f.seek(0)
    a, b, c, d = struct.unpack("<4I", f.read(16))
    assert a == 4, f"unexpected pickle prefix {a}"
    header = json.loads(f.read(d))
    return header, 8 + b


def iter_files(node, path=""):
    """yield (path_in_asar, node_dict)。node_dict 为文件（含 size）。"""
    for name, ch in node.get("files", {}).items():
        p = f"{path}/{name}"
        if "files" in ch:
            yield from iter_files(ch, p)
        else:
            yield p, ch


def compute_integrity(data: bytes):
    return {
        "algorithm": "SHA256",
        "hash": hashlib.sha256(data).hexdigest(),
        "blockSize": BLOCK,
        "blocks": [hashlib.sha256(data[i:i + BLOCK]).hexdigest() for i in range(0, len(data), BLOCK)],
    }


def safe_paths(asar_file: Path):
    """路径校验：返回 (源 asar, 临时写入路径)，写入目标为源同目录的常量文件名。

    三层防护：① 显式拒绝含 ".." 的输入；② 规范化（resolve）；③ 只接受名为
    app.asar 的现存文件，写入目标限定在其所在目录内（commonpath 包含性校验）。
    """
    raw = Path(asar_file)
    if ".." in raw.parts:
        raise ValueError(f"refusing path containing '..': {raw}")
    src = raw.resolve()
    if src.name != "app.asar" or not src.is_file():
        raise ValueError(f"refusing non-asar target: {src}")
    dst = src.parent / "app.asar.zusage.tmp"
    if ".." in dst.parts or os.path.commonpath([str(src.parent), str(dst)]) != str(src.parent):
        raise ValueError(f"write target escapes allowed directory: {dst}")
    return src, dst


def repack(src_path: Path, modify: dict):
    """重建 asar。modify: {asar内路径: 新内容bytes}。流式拷贝未改动文件。"""
    src_file, dst_path = safe_paths(src_path)
    with open(src_file, "rb") as src:
        header, base = read_header(src)
        header = json.loads(json.dumps(header))  # 深拷贝
        nodes = dict(iter_files(header))
        missing = set(modify) - set(nodes)
        assert not missing, f"paths not in asar: {missing}"

        data_parts = []
        offset = 0
        for p, node in nodes.items():
            if node.get("unpacked"):
                node["offset"] = "0"
                continue
            if p in modify:
                data = modify[p]
                node["size"] = len(data)
                node["integrity"] = compute_integrity(data)
            else:
                src.seek(base + int(node["offset"]))
                data = src.read(node["size"])
            node["offset"] = str(offset)
            data_parts.append(data)
            offset += len(data)

        payload = b"".join(data_parts)
        header_bytes = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        d = len(header_bytes)
        c = (d + 4 + ALIGN - 1) // ALIGN * ALIGN
        b_total = c + 4
        # 写入不经外部路径：mkstemp 在目标目录内创建临时文件（路径由标准库生成），
        # 保留源文件权限，写满后原子改名为固定临时名供收尾流程使用
        fd, tmp_name = tempfile.mkstemp(dir=str(src_file.parent), prefix=".app.asar.zusage.")
        try:
            os.chmod(tmp_name, src_file.stat().st_mode & 0o777)
        except OSError:
            pass
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(struct.pack("<4I", 4, b_total, c, d))
                out.write(header_bytes)
                out.write(b"\0" * (c - d - 4))  # pickle 对齐：数据区从 8+b = 12+c 处开始
                assert out.tell() == 8 + b_total, f"header pad mismatch: {out.tell()} vs {8 + b_total}"
                out.write(payload)
        except BaseException:
            os.unlink(tmp_name)
            raise
        os.replace(tmp_name, dst_path)
    return dst_path


def entry_bytes_of(asar_path: Path) -> bytes:
    with open(asar_path, "rb") as f:
        header, base = read_header(f)
        nodes = dict(iter_files(header))
        node = nodes["/" + ENTRY]
        f.seek(base + int(node["offset"]))
        return f.read(node["size"])


def self_check(asar_path: Path, expect=True):
    """结构自检：header 可解析、文件数、入口注入行符合预期（install 期望有，remove 期望无）。"""
    with open(asar_path, "rb") as f:
        header, base = read_header(f)
        n = sum(1 for _ in iter_files(header))
    has = INJECT_LINE.encode() in entry_bytes_of(asar_path)
    ok = has == expect
    print(L(f"  [check] header ok, 文件数={n}, 注入行{'符合' if ok else '不符合'}预期（期望{'有' if expect else '无'}）",
            f"  [check] header ok, files={n}, injection line {'as expected' if ok else 'UNEXPECTED'} (expected {'present' if expect else 'absent'})"))
    return ok


def syntax_check(asar_path: Path):
    """用 ZCode 自身当 node（ELECTRON_RUN_AS_NODE）对入口做语法检查；
    找不到/无法运行 ZCode 可执行文件时跳过（自检是锦上添花，不应挡安装）。"""
    exe = ZCODE_EXE
    if not exe or not exe.is_file():
        print(L("  [check] 未找到 ZCode 可执行文件，跳过语法检查",
                "  [check] ZCode executable not found; syntax check skipped"))
        return True
    src = entry_bytes_of(asar_path)
    tmp_js = HERE / ".entry-check.js"
    tmp_js.write_bytes(src)
    env = dict(os.environ, ELECTRON_RUN_AS_NODE="1")
    try:
        r = run_text([str(exe), "--check", str(tmp_js)], env=env)
    except OSError as e:
        r = None
        run_err = str(e)
    finally:
        tmp_js.unlink(missing_ok=True)
    if r is None:
        print(L(f"  [check] 语法检查无法执行（{run_err}），跳过",
                f"  [check] syntax check failed to run ({run_err}); skipped"))
        return True
    print(L("  [check] 语法检查 exit=", "  [check] node --check exit=") + str(r.returncode) + " " + r.stderr.strip()[:200])
    return r.returncode == 0


def _delete_watchdog_task():
    """删除本工具注册的计划任务（zusage-watchdog）。用户显式卸载后，看门狗不得再自动重打注入。"""
    try:
        run_text(["schtasks", "/Delete", "/F", "/TN", "zusage-watchdog"])
    except OSError:
        pass


def cleanup_legacy_bak():
    """备份机制已退役（安装/卸载均改为剥离注入行）：清掉历史版本遗留的 .bak，省约 300MB 磁盘。"""
    if not BAK.exists():
        return
    try:
        BAK.unlink()
        print(L(f"已清理历史遗留备份 {BAK}", f"Removed legacy backup {BAK}"))
    except OSError as e:
        print(L(f"（遗留备份清理失败，可手动删除 {BAK}：{e}）",
                f"(failed to remove legacy backup {BAK}; delete it manually: {e})"))


def _cold_hashes():
    """客户端启动时才会读取的冷文件哈希（只有 inject-main.cjs 需要重启换新）。"""
    try:
        data = (RUNTIME / "inject-main.cjs").read_bytes()
    except OSError:
        return {}
    return {"inject-main.cjs": hashlib.sha256(data).hexdigest()}


def _begin_install_intent(entry: bytes):
    """重打包前记录「注入已改、待重启」意图；返回 (installation, 发布上下文)。

    installation 是本次注入的身份（asar 路径 + 入口哈希）：publish_install 只在
    rename 成功后盖章 publishedAt；启动早于盖章的客户端进程不会被误认作已确认。"""
    if runtime_lock is None:
        return None, nullcontext()
    installation = {"asar": str(ASAR.resolve()),
                    "entryHash": hashlib.sha256(entry).hexdigest()}
    try:
        runtime_lock.record_restart(RUNTIME, {
            "state": "pending-install",
            "hashes": _cold_hashes(),
            "installation": installation,
            "source": "patch_install",
        })
    except (OSError, TimeoutError) as e:
        print(L(f"（重启标记写入失败，不影响安装：{e}）",
                f"(restart marker write failed; installation is unaffected: {e})"))
        return None, nullcontext()
    return installation, runtime_lock.publish_install(RUNTIME, installation)


def _fingerprint(path, deep=False):
    """文件身份指纹：size + mtime_ns（便宜）+ 可选 sha256（374MB 约 2 秒）。"""
    st = Path(path).stat()
    fp = {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
    if deep:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        fp["sha256"] = digest.hexdigest()
    return fp


def _same_fingerprint(before, now, deep=False):
    if not isinstance(before, dict) or not isinstance(now, dict):
        return False
    if before.get("size") != now.get("size") or before.get("mtime_ns") != now.get("mtime_ns"):
        return False
    if deep:
        return bool(before.get("sha256")) and before.get("sha256") == now.get("sha256")
    return True


def _write_pending_meta(source_fp, tmp_fp):
    """原子写待收尾包的来源身份（同目录 mkstemp + replace）。"""
    data = {
        "version": 1, "operation": "install", "validated": True,
        "asar": str(ASAR.resolve()), "runtime": str(RUNTIME.resolve()),
        "source": source_fp, "tmp": tmp_fp,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    fd, name = tempfile.mkstemp(prefix=".zusage-meta.", dir=str(ASAR.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, TMP_META)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def _read_pending_meta():
    try:
        data = json.loads(TMP_META.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != 1:
        return None
    if data.get("operation") != "install" or data.get("validated") is not True:
        return None
    if data.get("asar") != str(ASAR.resolve()) or data.get("runtime") != str(RUNTIME.resolve()):
        return None
    return data


def verify_pending(deep=False):
    """校验暂存包仍由「当前 asar」生成且 TMP 未被改动。返回 (ok, reason)。

    deep=True 额外比 sha256：只在监控启动/首次收尾前做一次（374MB 哈希约 2 秒），
    之后的重试用 stat（size+mtime_ns）快速判定。"""
    if not TMP.is_file():
        return False, "no-tmp"
    meta = _read_pending_meta()
    if meta is None:
        return False, "no-meta"
    try:
        source_now = _fingerprint(ASAR, deep=deep)
        tmp_now = _fingerprint(TMP, deep=deep)
    except OSError as e:
        return False, f"stat-failed: {e}"
    if not _same_fingerprint(meta.get("source"), source_now, deep):
        return False, "source-changed"    # ZCode 又升级过：旧包不能覆盖新版
    if not _same_fingerprint(meta.get("tmp"), tmp_now, deep):
        return False, "tmp-changed"       # TMP 被外部改动/半写
    return True, "ok"


def clear_pending_meta():
    try:
        TMP_META.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def finalize_pending(deep=False):
    """校验并发布待收尾包（替换 + 重启意图盖章）。返回：

    published  已替换并盖章
    missing    没有待收尾包
    untrusted  来源/内容校验失败，拒绝替换（文件保留供排查）
    busy       asar 仍被占用
    """
    ok, reason = verify_pending(deep=deep)
    if not ok:
        return "missing" if reason == "no-tmp" else "untrusted"
    installation = None
    marker = RUNTIME / runtime_lock.MARKER_NAME if runtime_lock else None
    if marker is not None and marker.is_file():
        try:
            installation = json.loads(marker.read_text(encoding="utf-8")).get("installation")
        except (OSError, ValueError):
            installation = None
    try:
        if runtime_lock is not None and isinstance(installation, dict):
            with runtime_lock.publish_install(RUNTIME, installation):
                os.replace(TMP, ASAR)
        else:
            os.replace(TMP, ASAR)
    except (OSError, TimeoutError):
        return "busy"
    clear_pending_meta()
    return "published"


def monitor_lock_path():
    return ASAR.with_name(MONITOR_LOCK_NAME)


def ensure_monitor(quiet=True):
    """TMP 待收尾时拉起监控（幂等：已有实例持锁则跳过）。返回是否新拉起。

    钩子/看门狗无人值守路径用 quiet=True：CREATE_NO_WINDOW + 脱离父 Job，
    这样 ZCode 退出时监控还活着，能在退出窗口内完成替换。"""
    if not TMP.is_file() or runtime_lock is None:
        return False
    probe = runtime_lock.FileLock(monitor_lock_path(), timeout=0)
    if not probe.acquire():
        return False          # 已有监控实例
    probe.release()           # 交给子进程持锁；子进程抢不到会自行退出
    return _spawn_monitor(quiet=quiet)


def _spawn_monitor(quiet=True, epoch=None):
    """拉起安装监控（参数列表 + shell=False，不经 shell）。"""
    monitor = HERE / "install_monitor.py"
    if not monitor.is_file():
        return False
    cmd = [str(sys.executable), str(monitor), "%.3f" % (epoch or time.time()),
           str(ASAR), str(RUNTIME)]
    env = dict(os.environ)
    if quiet:
        env["ZUSAGE_MONITOR_QUIET"] = "1"
    devnull = subprocess.DEVNULL
    if not IS_WIN:
        try:
            subprocess.Popen(cmd, shell=False, cwd=str(HERE), env=env,
                             stdin=devnull, stdout=devnull, stderr=devnull)
            return True
        except OSError as e:
            print(L(f"（监控进程拉起失败，不影响安装：{e}）",
                    f"(monitor failed to launch; installation is unaffected: {e})"))
            return False
    base = subprocess.CREATE_NO_WINDOW if quiet else subprocess.CREATE_NEW_CONSOLE
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    for extra in (breakaway, 0):   # Job 不允许 breakaway 时退回普通方式
        try:
            subprocess.Popen(cmd, shell=False, cwd=str(HERE), env=env,
                             creationflags=base | extra, stdin=devnull,
                             stdout=devnull if quiet else None,
                             stderr=devnull if quiet else None)
            return True
        except OSError:
            continue
    print(L("（监控进程拉起失败，不影响安装）",
            "(monitor failed to launch; installation is unaffected)"))
    return False


# ---------- 安装 / 卸载 ----------

def install(finalize=False, quiet=False):
    """返回 True=已安装/已指向当前目录，False=失败或待收尾。
    quiet=True：计划任务等无人值守路径使用——不弹监控窗口（通知由调用方负责）。"""
    assert LOADER.exists(), L(f"loader 缺失：{LOADER}", f"loader missing: {LOADER}")
    if not os.access(ASAR, os.W_OK) or not os.access(ASAR.parent, os.W_OK):
        print(L(f"[权限] app.asar 或所在目录不可写（Linux 上 /opt 等系统位置需要 root）：{ASAR}",
                f"[permission] app.asar or its directory is not writable (Linux system locations like /opt need root): {ASAR}"))
        print(L("请用 sudo 重跑（推荐走一键安装，会自动处理目标定位与数据目录归属）：",
                "Please re-run with sudo (the one-shot installer is recommended; it resolves the target and data-dir ownership automatically):"))
        print(L("  sudo python install.py            # 自动探测（含 /opt/ZCode）",
                "  sudo python install.py            # auto-detects (including /opt/ZCode)"))
        return False
    entry = entry_bytes_of(ASAR)
    stripped = ZUSAGE_LINE_RE.sub(b"", entry)   # 剥离任何旧注入行（含指向旧目录的）
    if stripped + INJECT_LINE.encode() == entry:
        print(L("已安装，注入行已指向当前目录。如需重装先 remove。",
                "Already installed; the injection line points at the current directory. Run remove first to reinstall."))
        cleanup_legacy_bak()
        return True

    new_entry = stripped + INJECT_LINE.encode()
    if stripped != entry:
        if _STANDALONE and not RUNTIME_EXPLICIT:
            # standalone 场景下旧注入行指向其它运行时目录：静默重打包会把注入改指本脚本
            # 所在目录（repo ≠ 数据目录，破坏"副本自洽"不变量）。须显式 --runtime 才放行。
            print(L("检测到旧注入行指向其它运行时目录，而本次未显式声明 --runtime，拒绝重打包。",
                    "Old injection line points at a different runtime dir and no --runtime was given; repack refused."))
            print(L(f'确要改指请加参数重跑：python patch_install.py install --runtime "{RUNTIME}"',
                    f'To re-point intentionally, re-run: python patch_install.py install --runtime "{RUNTIME}"'))
            print(L("常规修复/收尾请直接跑 install.py（自动探测并指向数据目录）。",
                    "For routine repair/finalize just run install.py (auto-detects and targets the data dir)."))
            return False
        print(L("检测到旧注入行（可能指向旧目录），将替换为新路径。",
                "Old injection line detected (possibly pointing at an old directory); it will be replaced with the new path."))
    print(L("重打包（307MB，约需十几秒）...", "repacking (307MB, takes ~10-20 seconds)..."))
    source_fp = _fingerprint(ASAR, deep=True)   # repack 只读 ASAR：先记下源身份
    repack(ASAR, {"/" + ENTRY: new_entry})
    print(L("结构自检：", "self-check:"))
    if not (self_check(TMP) and syntax_check(TMP)):
        print(L("自检失败，未替换。TMP 保留供排查:", "self-check failed; not replaced. TMP kept for inspection:"), TMP)
        return False
    # 记录待收尾包身份：收尾前若源 asar 已被再次更新，拒绝用旧包覆盖新版
    _write_pending_meta(source_fp, _fingerprint(TMP, deep=True))
    # 注入已改 → 记待重启意图（真被新进程加载后由 inject-main 回执清除）
    _installation, publication = _begin_install_intent(new_entry)
    if client_running() and not finalize:
        try:
            with publication:
                os.replace(TMP, ASAR)
            clear_pending_meta()
            print(L("\n客户端运行中，但 asar 原子替换成功（运行中进程仍读旧数据）。",
                    "\nZCode is running, but the asar was replaced atomically (running processes keep reading the old data)."))
            print(L("重启 ZCode 后悬浮条生效；已弹出监控窗口，确认生效后自动关闭。",
                    "The floating bar activates after restarting ZCode; a monitor window has opened and will close itself once the load is confirmed."))
            cleanup_legacy_bak()
            if not quiet: launch_monitor(time.time())
            return True
        except OSError as e:
            if quiet:
                print(L(f"\n运行中替换失败（{e}）。.tmp 已暂存：已拉起静默监控，完全退出 ZCode 后自动完成替换。",
                        f"\nReplacement while running failed ({e}). The .tmp is staged; a silent monitor will finish the swap once ZCode quits completely."))
                ensure_monitor(quiet=True)
            else:
                print(L(f"\n运行中替换失败（{e}）。已弹出监控窗口：完全退出 ZCode 后会自动完成替换，",
                        f"\nReplacement while running failed ({e}). A monitor window has opened: it finishes the replacement once ZCode quits completely,"))
                print(L(f'或之后手动执行：python patch_install.py install --finalize --asar "{ASAR}" --runtime "{RUNTIME}"',
                        f'or run manually afterwards: python patch_install.py install --finalize --asar "{ASAR}" --runtime "{RUNTIME}"'))
            if not quiet: launch_monitor(time.time())
            return False
    try:
        with publication:
            os.replace(TMP, ASAR)
    except OSError as e:
        print(L(f"\n替换失败（{e}）。", f"\nReplacement failed ({e})."))
        print(L(f'待替换包保留在 {TMP}；完全退出 ZCode 后重跑 install --finalize，或由看门狗自动收尾。',
                f'Pending package kept at {TMP}; quit ZCode completely and re-run install --finalize, or let the watchdog finish it.'))
        if quiet:
            ensure_monitor(quiet=True)
        return False
    clear_pending_meta()
    print(L("完成。启动 ZCode 即可在窗口底部看到悬浮条。", "Done. Start ZCode and the floating bar appears at the bottom of the window."))
    cleanup_legacy_bak()
    if not quiet: launch_monitor(time.time())
    return True


def remove():
    """卸载：从当前 asar 剥离本工具注入行（不依赖备份，其它工具的注入行不受影响）。
    返回 True=已卸载/本就未注入，False=需人工处理（替换失败待收尾）。"""
    _delete_watchdog_task()   # 用户显式卸载：看门狗一并退出，避免自动「复活」注入（P1-5）
    old = entry_bytes_of(ASAR)
    stripped = ZUSAGE_LINE_RE.sub(b"", old)
    if stripped == old:
        print(L("当前 asar 未注入。", "The current asar is not injected."))
        cleanup_legacy_bak()
        return True
    print(L("重打包（剥离注入行，约需十几秒）...", "repacking (stripping the injection line, takes ~10-20 seconds)..."))
    repack(ASAR, {"/" + ENTRY: stripped})
    clear_pending_meta()   # TMP 现在是 remove 包：任何旧 install 身份立即作废
    print(L("结构自检：", "self-check:"))
    if not (self_check(TMP, expect=False) and syntax_check(TMP)):
        print(L("自检失败，未替换。TMP 保留供排查:", "self-check failed; not replaced. TMP kept for inspection:"), TMP)
        return False
    running = client_running()
    try:
        os.replace(TMP, ASAR)
    except OSError as e:
        print(L(f"\n替换失败（{e}）。请完全退出 ZCode 后重跑 python patch_install.py remove，",
                f"\nReplacement failed ({e}). Quit ZCode completely and re-run python patch_install.py remove, "))
        hint = f"move /y {TMP} {ASAR}" if IS_WIN else f"mv -f '{TMP}' '{ASAR}'"
        print(L(f"或手动替换：{hint}", f"or replace manually: {hint}"))
        return False
    print(L("已剥离注入行" + ("（客户端运行中，已原子替换，运行中进程仍读旧数据）" if running else "") + "。重启 ZCode 后生效。",
            "Injection line stripped" + (" (ZCode was running; replaced atomically, the running process keeps the old data)" if running else "") + ". Takes effect after restarting ZCode."))
    cleanup_legacy_bak()
    return True


def check():
    injected = bool(ZUSAGE_LINE_RE.search(entry_bytes_of(ASAR)))   # 宽松匹配任意目录的历史注入行（独立运行时 INJECT_LINE 指向仓库路径，精确匹配会误报未注入）
    print("asar:", ASAR, ASAR.stat().st_size, "bytes")
    print(L("注入状态:", "Injection:"), L("已注入", "injected") if injected else L("未注入", "not injected"))
    print(L("遗留备份（机制已退役，成功安装/卸载后自动清理）:", "Legacy backup (retired; auto-removed on successful install/remove):"), BAK.exists())
    if injected:
        syntax_check(ASAR)
    if TMP.exists():
        ok, reason = verify_pending()
        print(L("存在待替换 TMP:", "Pending replacement TMP exists:"), TMP,
              L(f'（校验：{"通过" if ok else reason}）', f'(verify: {"ok" if ok else reason})'))
        print(L(f'（退出 ZCode 后跑 install --finalize --asar "{ASAR}" --runtime "{RUNTIME}"）',
                f'(quit ZCode, then run install --finalize --asar "{ASAR}" --runtime "{RUNTIME}")'))


def _consume_flag(argv, name):
    """从 argv 提取 --name VALUE / --name=VALUE（多次出现取最后一次），返回 (值, 余下列表)。
    缺值或紧随另一 flag（下一个 token 以 -- 开头）时按缺值处理，原样保留不误吞。"""
    val, out, i = None, [], 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--" + name:
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                val, i = argv[i + 1], i + 2
                continue
            out.append(tok)
            i += 1
        elif tok.startswith("--" + name + "="):
            val, i = tok.split("=", 1)[1], i + 1
        else:
            out.append(tok)
            i += 1
    return val, out


if __name__ == "__main__":
    _STANDALONE = True
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "check"
    argv = argv[1:]
    # standalone 目标解析：--asar/--runtime 参数 > 环境变量 ZCODE_ASAR/ZCODE_RUNTIME >
    # 平台默认。install.py / watchdog 拉起时经 set_target()/set_runtime() 已设好，此处
    # 只服务 standalone 场景（默认路径猜错的自定义安装布局，见 ASAR_DEFAULT 注释）。
    asar_arg, argv = _consume_flag(argv, "asar")
    if not asar_arg:
        asar_arg = os.environ.get("ZCODE_ASAR")
    runtime_arg, argv = _consume_flag(argv, "runtime")
    if not runtime_arg:
        runtime_arg = os.environ.get("ZCODE_RUNTIME")
    finalize = "--finalize" in argv
    argv = [t for t in argv if t != "--finalize"]
    if asar_arg:
        set_target(Path(os.path.expandvars(asar_arg)).expanduser())
    if runtime_arg:
        set_runtime(Path(os.path.expandvars(runtime_arg)).expanduser())
    ok = {"install": lambda: install(finalize=finalize), "remove": remove, "check": check}.get(cmd, check)()
    sys.exit(0 if ok is not False else 1)
