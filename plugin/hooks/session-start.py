# -*- coding: utf-8 -*-
"""ZCode SessionStart 钩子：运行时同步 + 引导安装 + 注入自检（zusage 插件壳，零依赖）。

每次会话启动（startup|resume）由宿主拉起，按安装形态分流：
  1) 仓库形态（插件目录上有仓库根，install.py 可导入）：按 RUNTIME_FILES 清单把
     仓库运行时复制到数据目录（字节比较，内容不变不写——泵以 mtime 热重载，无谓
     复制会让泵空重启；复制走临时文件 + os.replace 原子落盘，防钩子被杀留下半
     文件喂给泵/看门狗）；/usage 命令模板同步到全局命令目录。更新仓库后无需手动
     重跑 install.py；清单里新增的文件也会自动补齐（09-30"数据目录缺新文件→
     看门狗 import 死亡 10 天"的根治）。清单以仓库 install.py 为唯一权威定义处，
     此处不重复维护文件列表。
  2) 市场形态（插件包内无运行时源，spec 禁止携带副本）：数据目录缺 watchdog.py
     即引导安装——从 GitHub 官方仓库拉 main 分支 tarball，两阶段落地：先取
     install.py 解析权威 DATA_DIR/RUNTIME_FILES，再同步全套运行时 + /usage
     命令模板 + 最小 config.json（仅缺失时补 python_path）。MCP 注册与计划任务
     仍属 install.py 主安装。下载硬约束：仅 https + 固定域名白名单（拒绝 IP
     字面量，环回/私有/保留段全覆盖）、体积上限 20MB、重定向逐跳校验、解压面
     三重上限（成员数 5000 / 单成员 8MB / 累计 64MB，按实际读取长度校验防 gzip
     炸弹）；失败留痕
     并进入 6 小时冷却（防离线环境下每次会话启动都空等网络超时）。数据目录已有
     运行时则以 ≥24 小时间隔在线刷新（同一受控通道；字节比较内容不变不写；失败
     退避 1 小时，留痕数据目录 watchdog.log）。
  3) 注入自检：调数据目录 watchdog.main(False)——与计划任务同一实现（精确
     匹配、暂存收尾、通知防抖），会话启动即查一次，早于 10 分钟周期。
  4) 重启提醒：同步/注入改了冷文件（inject-main.cjs）时写重启意图标记
     （.plugin-restart-required，见 runtime_lock.py），客户端下次启动按「实际
     加载哈希」回执清除；待确认期间每次会话启动在 systemMessage 里提醒重启
     （hook 自己刚写的标记给 15 秒宽限，避开客户端回执在途的窗口）。

安全边界：任何异常（含 watchdog 导入失败时的 SystemExit）吞掉后以 0 退出
（绝不阻断会话启动），失败留痕 hooks/session-start.err.log（带时间戳，
超 32KB 截断尾部）。
"""
import hashlib
import importlib.util
import io
import ipaddress
import json
import os
import sys
import tarfile
import tempfile
import time
import traceback
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

PLUGIN_ROOT = Path(__file__).parent.parent.resolve()   # .../plugin
REPO = PLUGIN_ROOT.parent                              # 仓库根（运行时源；市场形态下不存在）
ERR_LOG = PLUGIN_ROOT / "hooks" / "session-start.err.log"
ERR_LOG_MAX = 32 * 1024
COMMAND_NAME = "usage.command.md"

# 市场形态引导安装：源固定为官方仓库 main 分支 tarball（与 git pull 同源的最新运行时）
DOWNLOAD_URL = "https://codeload.github.com/Wade-YD/zcode-token-usage-statusbar/tar.gz/refs/heads/main"
ALLOWED_HOSTS = ("codeload.github.com", "github.com", "objects.githubusercontent.com")
MAX_DOWNLOAD = 20 * 1024 * 1024    # tarball 体积上限（仓库实际 ~1MB；防异常响应撑爆内存）
DOWNLOAD_TIMEOUT = 20              # 秒；钩子总预算见 hooks.json timeoutMs（60s）
BOOT_STATE = PLUGIN_ROOT / "hooks" / "bootstrap-state.json"
BOOT_RETRY_SECS = 6 * 3600         # 引导失败冷却：离线/被墙时别让每次会话启动都等满超时
BOOT_REFRESH_SECS = 24 * 3600      # 市场形态运行时刷新检查间隔（拉远端做字节比较）
BOOT_REFRESH_RETRY_SECS = 3600     # 刷新失败退避：1 小时后重试（区别于引导安装的 6h 大冷却）

# 重启闭环（与 runtime_lock.py 同一标记）：冷文件只有客户端重启才会被重新加载，
# 同步后写意图、客户端启动时按「实际加载哈希」回执清除；钩子只负责在待确认期间提醒。
COLD_FILES = ("inject-main.cjs",)
RESTART_MARKER = ".plugin-restart-required"
RESTART_NOTICE = ("ZCode 状态条已更新，重启 ZCode 后生效：请完全退出 ZCode（所有窗口）"
                  "后重新打开。仅关闭窗口、刷新页面或重开会话无效。")

# tarball 解包硬上限：压缩包 20MB 上限之外再压一层解压面（gzip 可膨胀千倍）
MAX_TAR_MEMBERS = 5000
MAX_MEMBER_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def _paths():
    """仓库 install.py 是 RUNTIME_FILES / DATA_DIR 的唯一权威定义处（防清单漂移）。
    返回 (data_dir, runtime_files, repo_available)：市场形态（插件包内无运行时源）
    回退默认数据目录 + 空清单 + False，运行时由引导安装补全。"""
    try:
        sys.path.insert(0, str(REPO))
        import install
        return Path(install.DATA_DIR), list(install.RUNTIME_FILES), True
    except Exception:
        return Path.home() / ".zcode" / "zcode-token-usage-statusbar", [], False


def _write_if_changed(data, dst):
    """字节比较后原子写入（mkstemp + os.replace），半文件不会暴露给泵/看门狗。"""
    if dst.is_file() and dst.read_bytes() == data:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(dst.parent), prefix="." + dst.name + ".")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.replace(tmp_name, dst)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return True


def _copy_if_changed(src, dst):
    return _write_if_changed(src.read_bytes(), dst) if src.is_file() else False


def sync_runtime(src_root, data_dir, runtime_files):
    """src_root（仓库形态=仓库根）→ 数据目录 / 全局命令目录；返回实际写入的文件名列表。"""
    synced = []
    for name in runtime_files:
        if _copy_if_changed(src_root / name, data_dir / name):
            synced.append(name)
    if _copy_if_changed(src_root / COMMAND_NAME, Path.home() / ".zcode" / "commands" / "usage.md"):
        synced.append(COMMAND_NAME)
    return synced


def _record_restart(data_dir, changed, source):
    """同步改了冷文件 → 记「待重启」意图（哈希确认由客户端下次启动回执）。

    只记冷文件：overlay.js / zusage.py / zquota.py 由泵按 mtime 热更新，提醒是噪音。
    标记写不进只影响提醒，绝不影响同步本身。"""
    cold = [name for name in COLD_FILES if name in (changed or [])]
    if not cold:
        return False
    try:
        hashes = {name: hashlib.sha256((data_dir / name).read_bytes()).hexdigest()
                  for name in cold}
        sys.path.insert(0, str(data_dir))
        import runtime_lock
        runtime_lock.record_restart(data_dir, {
            "state": "pending-sync", "hashes": hashes,
            "changed": list(changed), "source": source,
        })
        return True
    except Exception:
        return False


def _restart_notice(data_dir):
    """待确认的重启意图 → 会话启动提醒（只读；客户端加载新代码后标记即消失）。

    hook 自己刚写的意图给 15 秒宽限：客户端启动回执可能仍在途，本轮不提醒，
    下次会话启动仍待确认再报（避免每次更新后误报一次）。"""
    marker = data_dir / RESTART_MARKER
    if not marker.is_file():
        return None
    try:
        age = time.time() - marker.stat().st_mtime
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if str(data.get("source") or "").startswith("hook") and age < 15:
        return None
    message = RESTART_NOTICE
    return {"systemMessage": message, "hookSpecificOutput": {
        "hookEventName": "SessionStart", "additionalContext": message}}


def _url_guard(url):
    """下载目标硬校验：仅 https + 域名白名单；主机名是 IP 字面量（环回/私有/保留段
    全覆盖）一律拒绝。URL 本是常量，此函数是纵深防御——防未来改常量时引入注入点。"""
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https":
        raise ValueError(f"仅允许 https，实际 {u.scheme!r}")
    host = (u.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"主机 {host!r} 不在白名单 {ALLOWED_HOSTS}")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass          # 域名字面量，白名单已过
    else:
        raise ValueError(f"拒绝 IP 字面量主机：{host}")


class _GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """重定向逐跳过 _url_guard（urllib 默认会无校验地跟随到任意主机）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _url_guard(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url):
    """拉取 tarball 字节。代理：环境变量惯例 + ZCODE_HTTP_PROXY（与 ZCode 市场克隆同源）。"""
    _url_guard(url)
    proxies = urllib.request.getproxies()
    zp = os.environ.get("ZCODE_HTTP_PROXY")
    if zp:
        proxies.setdefault("http", zp)
        proxies.setdefault("https", zp)
    opener = urllib.request.build_opener(
        _GuardedRedirectHandler(), urllib.request.ProxyHandler(proxies))
    req = urllib.request.Request(url, headers={"User-Agent": "zusage-statusbar-plugin/1.1"})
    with opener.open(req, timeout=DOWNLOAD_TIMEOUT) as r:
        data = r.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise ValueError(f"下载体积超过上限 {MAX_DOWNLOAD} 字节")
    return data


def _tar_members(raw, wanted):
    """从 tarball 字节里取 {相对名: 内容}，只收白名单成员（剥掉 GitHub 归档首段目录，
    拒绝链接/设备等非常规成员与路径逃逸）。不用 tarfile.extract()：成员内容只写进
    我们自己命名的目标路径，无路径穿越面。

    解压面硬约束（gzip 可把 20MB 压成数 GB）：成员数、单成员体积、累计体积三重上限，
    且按实际读取长度校验——不信任 header 里声明的 size。"""
    out = {}
    total = 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        for index, m in enumerate(tf):
            if index >= MAX_TAR_MEMBERS:
                raise ValueError(f"tarball 成员数超过上限 {MAX_TAR_MEMBERS}")
            parts = PurePosixPath(m.name).parts
            if m.name.startswith("/") or any(p in ("..", "") for p in parts):
                raise ValueError(f"tarball 含非法成员路径：{m.name!r}")
            if len(parts) < 2 or not m.isfile():
                continue
            rel = "/".join(parts[1:])
            if rel not in wanted or rel in out:
                continue
            if m.size > MAX_MEMBER_BYTES:
                raise ValueError(f"成员 {rel} 声明体积超过上限 {MAX_MEMBER_BYTES} 字节")
            stream = tf.extractfile(m)
            if stream is None:
                continue
            data = stream.read(MAX_MEMBER_BYTES + 1)
            if len(data) > MAX_MEMBER_BYTES:
                raise ValueError(f"成员 {rel} 实际体积超过上限 {MAX_MEMBER_BYTES} 字节")
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise ValueError(f"tarball 解压总量超过上限 {MAX_TOTAL_BYTES} 字节")
            out[rel] = data
    missing = set(wanted) - set(out)
    if missing:
        raise RuntimeError("tarball 缺少文件: " + ", ".join(sorted(missing)))
    return out


def _load_manifest(install_path):
    """从刚落地的 install.py 取权威 DATA_DIR/RUNTIME_FILES（import 安全：模块级只有
    常量与函数定义，argparse 在 __main__ 守卫内）。用独立模块名加载，不占用
    sys.modules 的 "install"——留给随后数据目录 watchdog 的标准导入；
    dont_write_bytecode 防止数据目录冒出 __pycache__。"""
    spec = importlib.util.spec_from_file_location("zusage_bootstrap_install", install_path)
    mod = importlib.util.module_from_spec(spec)
    saved = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = saved
    return Path(mod.DATA_DIR), list(mod.RUNTIME_FILES)


def _boot_state_load():
    try:
        return json.loads(BOOT_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _boot_state_save(data):
    try:
        BOOT_STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass   # 状态写不进（只读目录？）：最坏结果是冷却失效，多试几次下载而已


def _apply_tarball(raw, data_dir, with_min_config):
    """把 main tarball 两阶段落到数据目录：install.py 先落地（它本就是 RUNTIME_FILES
    一员），再从它取权威 DATA_DIR/RUNTIME_FILES 同步全套运行时 + /usage 命令模板。
    with_min_config 仅引导安装用（config.json 缺失时补 python_path）。字节比较，
    内容不变不写（泵按 mtime 热重载，无谓复制会引发空重启）。返回 (data_dir,
    写入文件名列表)。"""
    members = _tar_members(raw, {"install.py"})
    synced = []
    if _write_if_changed(members["install.py"], data_dir / "install.py"):
        synced.append("install.py")
    data_dir, runtime_files = _load_manifest(data_dir / "install.py")

    members = _tar_members(raw, set(runtime_files) | {COMMAND_NAME})
    for name in runtime_files:
        if name == "install.py":
            continue   # 第一阶段已写入并计数
        if _write_if_changed(members[name], data_dir / name):
            synced.append(name)
    if _write_if_changed(members[COMMAND_NAME], Path.home() / ".zcode" / "commands" / "usage.md"):
        synced.append(COMMAND_NAME)

    if with_min_config:
        # 最小 config.json：只补 python_path（泵要拉起 python 跑额度查询），其余键组件
        # 自带默认；已存在的用户配置不动。完整配置/MCP 注册仍由 install.py 主安装提供。
        cfg = data_dir / "config.json"
        if not cfg.is_file():
            _write_if_changed(json.dumps({"python_path": sys.executable}, indent=2).encode("utf-8"), cfg)
    return data_dir, synced


def bootstrap_runtime(default_data_dir):
    """市场形态引导安装：下载 main tarball → _apply_tarball（含最小 config.json）。
    返回 (data_dir, 写入文件名列表)。失败进入冷却（BOOT_RETRY_SECS）；冷却期内的
    会话启动直接抛错留痕，不再碰网络。"""
    st = _boot_state_load()
    now = time.time()
    last = float(st.get("last_attempt") or 0)
    if now - last < BOOT_RETRY_SECS:
        remain = int((BOOT_RETRY_SECS - (now - last)) / 60)
        raise RuntimeError(f"市场形态引导安装冷却中（约 {remain} 分钟后重试）；数据目录仍缺运行时")
    st.update(last_attempt=now, last_attempt_at=time.strftime("%m-%d %H:%M:%S"))
    _boot_state_save(st)
    raw = _download(DOWNLOAD_URL)
    data_dir, synced = _apply_tarball(raw, default_data_dir, with_min_config=True)
    st = _boot_state_load()   # 重读再合并：防覆盖期间其它进程写入的刷新键
    st.update(last_attempt=now, ok=True, synced=synced,
              at=time.strftime("%m-%d %H:%M:%S"),
              last_refresh=now)   # 播种刷新时钟：引导刚拉完远端，不当场再检查一次
    _boot_state_save(st)
    return data_dir, synced


def refresh_runtime(data_dir):
    """市场形态运行时刷新：数据目录已有运行时时，距上次检查 ≥24h（上次失败则按
    1h 退避）拉 main tarball，与引导安装同一受控下载通道，字节比较同步。未到检查
    期返回 None（零网络）；失败返回 []、数据目录 watchdog.log 留痕并置 refresh_error
    （检查间隔随之为 1h；不写 err.log——刷新失败不属钩子核心职责失败，注入自检
    照常执行）；成功返回实际写入的文件名列表。绝不触碰 config.json，不注册 MCP/
    计划任务。仓库形态不做在线刷新（本地仓库同步即权威）。"""
    st = _boot_state_load()
    now = time.time()
    interval = BOOT_REFRESH_RETRY_SECS if st.get("refresh_error") else BOOT_REFRESH_SECS
    if now - float(st.get("last_refresh") or 0) < interval:
        return None
    st["last_refresh"] = now   # 先落检查时钟：进程中途被杀也不会下轮会话启动立刻重拉
    _boot_state_save(st)
    try:
        raw = _download(DOWNLOAD_URL)
        _, synced = _apply_tarball(raw, data_dir, with_min_config=False)
    except BaseException as exc:
        st["refresh_error"] = f"{exc.__class__.__name__}: {exc}"[:200]
        _boot_state_save(st)   # last_refresh 已是本次尝试时间；间隔因 refresh_error 变 1h
        _log_sync(data_dir, "插件钩子运行时刷新失败（%s），1 小时后重试" % exc.__class__.__name__)
        return []
    st = _boot_state_load()
    st.pop("refresh_error", None)
    st.update(last_refresh=now, last_refresh_at=time.strftime("%m-%d %H:%M:%S"),
              refresh_synced=list(synced))
    _boot_state_save(st)
    if synced:
        _log_sync(data_dir, "插件钩子运行时刷新写入 %d 个文件：%s"
                  % (len(synced), ", ".join(synced)))
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


def _log_sync(data_dir, text):
    """同步/引导动作在数据目录 watchdog.log 留痕（仅当实际发生写入时调用）。"""
    try:
        with (data_dir / "watchdog.log").open("a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%m-%d %H:%M:%S"), text))
    except OSError:
        pass


def main():
    data_dir = Path.home() / ".zcode" / "zcode-token-usage-statusbar"
    try:
        data_dir, runtime_files, repo_available = _paths()
        if repo_available:
            synced = sync_runtime(REPO, data_dir, runtime_files)
            if synced:
                _log_sync(data_dir, "插件钩子同步运行时 %d 个文件：%s"
                          % (len(synced), ", ".join(synced)))
            _record_restart(data_dir, synced, "hook-repo")
        elif not (data_dir / "watchdog.py").is_file():
            # 市场形态 + 数据目录无运行时 → 引导安装；随后照常走注入自检
            data_dir, synced = bootstrap_runtime(data_dir)
            _log_sync(data_dir, "插件钩子引导安装（GitHub main）写入 %d 个文件：%s"
                      % (len(synced), ", ".join(synced)))
            _record_restart(data_dir, synced, "hook-bootstrap")
        else:
            # 市场形态 + 已有运行时 → 到期则在线刷新（检查期内零网络）
            _record_restart(data_dir, refresh_runtime(data_dir) or [], "hook-refresh")
        sys.path.insert(0, str(data_dir))
        import watchdog
        watchdog.main(False)   # 注入自检：与计划任务同一实现（verbose=False 收输出）
    except BaseException:
        # SystemExit 也必须接住：watchdog 在依赖导入失败时模块级 sys.exit(0)，
        # 若放行则钩子带非零码/traceback 结束且无留痕（违反 spec 的 MUST）。
        _log_err()
    # 待确认的重启意图 → 本轮会话启动提醒（钩子唯一输出；任何异常都不阻断会话）
    try:
        notice = _restart_notice(data_dir)
    except Exception:
        notice = None
    if notice:
        print(json.dumps(notice, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    # 宿主按 UTF-8 解码钩子 stdout：Windows 无全局 PYTHONUTF8 时中文会按 GBK 写管道
    # （与 usage_mcp.py 同款防护）。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
