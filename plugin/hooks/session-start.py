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
     字面量，环回/私有/保留段全覆盖）、体积上限 20MB、重定向逐跳校验；失败留痕
     并进入 6 小时冷却（防离线环境下每次会话启动都空等网络超时）。
  3) 注入自检：调数据目录 watchdog.main(False)——与计划任务同一实现（精确
     匹配、暂存收尾、通知防抖），会话启动即查一次，早于 10 分钟周期。

安全边界：任何异常（含 watchdog 导入失败时的 SystemExit）吞掉后以 0 退出
（绝不阻断会话启动），失败留痕 hooks/session-start.err.log（带时间戳，
超 32KB 截断尾部）。
"""
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
    拒绝链接/设备等非常规成员）。不用 tarfile.extract()：成员内容只写进我们自己
    命名的目标路径，无路径穿越面。"""
    out = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        for m in tf:
            parts = PurePosixPath(m.name).parts
            if len(parts) < 2 or not m.isfile():
                continue
            rel = "/".join(parts[1:])
            if rel in wanted and rel not in out:
                out[rel] = tf.extractfile(m).read()
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


def bootstrap_runtime(default_data_dir):
    """市场形态引导安装：下载 main tarball → 同步全套运行时 + /usage 命令模板 +
    最小 config.json（仅缺失时）。返回 (data_dir, 写入文件名列表)。失败进入冷却
    （BOOT_RETRY_SECS）；冷却期内的会话启动直接抛错留痕，不再碰网络。"""
    st = _boot_state_load()
    now = time.time()
    last = float(st.get("last_attempt") or 0)
    if now - last < BOOT_RETRY_SECS:
        remain = int((BOOT_RETRY_SECS - (now - last)) / 60)
        raise RuntimeError(f"市场形态引导安装冷却中（约 {remain} 分钟后重试）；数据目录仍缺运行时")
    _boot_state_save({"last_attempt": now,
                      "last_attempt_at": time.strftime("%m-%d %H:%M:%S")})
    raw = _download(DOWNLOAD_URL)

    # 两阶段：install.py 先落地数据目录（它本就是 RUNTIME_FILES 一员），再从它取权威清单
    members = _tar_members(raw, {"install.py"})
    synced = []
    if _write_if_changed(members["install.py"], default_data_dir / "install.py"):
        synced.append("install.py")
    data_dir, runtime_files = _load_manifest(default_data_dir / "install.py")

    members = _tar_members(raw, set(runtime_files) | {COMMAND_NAME})
    for name in runtime_files:
        if name == "install.py":
            continue   # 第一阶段已写入并计数
        if _write_if_changed(members[name], data_dir / name):
            synced.append(name)
    if _write_if_changed(members[COMMAND_NAME], Path.home() / ".zcode" / "commands" / "usage.md"):
        synced.append(COMMAND_NAME)

    # 最小 config.json：只补 python_path（泵要拉起 python 跑额度查询），其余键组件
    # 自带默认；已存在的用户配置不动。完整配置/MCP 注册仍由 install.py 主安装提供。
    cfg = data_dir / "config.json"
    if not cfg.is_file():
        _write_if_changed(json.dumps({"python_path": sys.executable}, indent=2).encode("utf-8"), cfg)

    _boot_state_save({"last_attempt": now, "ok": True, "synced": synced,
                      "at": time.strftime("%m-%d %H:%M:%S")})
    return data_dir, synced


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
    try:
        data_dir, runtime_files, repo_available = _paths()
        if repo_available:
            synced = sync_runtime(REPO, data_dir, runtime_files)
            if synced:
                _log_sync(data_dir, "插件钩子同步运行时 %d 个文件：%s"
                          % (len(synced), ", ".join(synced)))
        elif not (data_dir / "watchdog.py").is_file():
            # 市场形态 + 数据目录无运行时 → 引导安装；随后照常走注入自检
            data_dir, synced = bootstrap_runtime(data_dir)
            _log_sync(data_dir, "插件钩子引导安装（GitHub main）写入 %d 个文件：%s"
                      % (len(synced), ", ".join(synced)))
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
