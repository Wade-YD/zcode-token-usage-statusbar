# -*- coding: utf-8 -*-
"""runtime_lock —— 运行时同步/注入与客户端加载之间的跨进程协作原语（零依赖）。

解决的问题：「数据目录里的文件已换新」不等于「客户端已加载新代码」——运行中的
ZCode 仍执行内存里的旧 inject-main.cjs，用户看到的还是旧状态条，而且从外部无法
区分「没更新」「更新了没重启」「重启了但替换没落地」。本模块把这件事变成可判定
状态：永久文件锁 + 重启意图标记 + 加载哈希确认。

    .plugin-restart-required    重启意图标记（JSON）
        state         pending-sync（运行时已同步）/ pending-install（注入已改）
        hashes        {文件名: sha256}——只有新进程实际加载到一致哈希才可清除
        installation  注入记录（asar 路径 + 入口哈希）；publish_install 盖章 publishedAt
        createdAt     记录时刻；客户端上报的 _loadedAt 必须晚于它（旧进程不清新标记）

    plugin-restart.lock         标记互斥锁。锁文件永不 unlink：删除会破坏其它进程
                                已持有的锁语义（Windows/POSIX 一致）。

API：
    file_lock(path, timeout=10.0)            跨进程排他锁（msvcrt / flock，非阻塞重试）
    record_restart(runtime, data)            合并写入重启意图（持锁）
    publish_install(runtime, installation)   ASAR 发布与意图盖章同锁（持锁跨 rename）
    ack_restart(runtime, loaded)             只清除「已加载且哈希一致」的条目

CLI（供 inject-main.cjs 等非 Python 侧调用）：
    python runtime_lock.py --ack-restart <runtime> '<loaded-json>'
    python runtime_lock.py --record-restart <runtime>      # stdin 读 JSON
    python runtime_lock.py --hold <lockpath>               # 占锁到 stdin EOF（调试用）

移植说明：机制取自同源 MIT 项目 zcode-token-usage-statusbar 的活跃分支
（ByteRax/zcode-token-usage-statusbar，MIT License，Copyright (c) 2026 xhwx），
按本仓库单源架构裁剪：去掉未使用的 config 合并写入，保留锁与重启标记全部语义。
"""
import argparse
import errno
import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

MARKER_NAME = ".plugin-restart-required"
LOCK_NAME = "plugin-restart.lock"


class FileLock:
    """进程级排他锁（Windows msvcrt / POSIX flock），锁文件永不 unlink。

    需要「整个进程生命周期持锁」的场景（监控实例去重）用 acquire()/release()；
    临时临界区用下面的 file_lock() 上下文管理器。"""

    def __init__(self, path, timeout=10.0):
        self.path = os.fspath(path)
        self.timeout = max(0.0, float(timeout))
        self._fd = None
        self._locked = False

    def acquire(self):
        """非阻塞重试直到超时，返回是否拿到锁；其它 OSError 原样抛出。"""
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self.timeout
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")   # msvcrt.locking 需要至少 1 字节可锁区间
            else:
                import fcntl
            while True:
                try:
                    if os.name == "nt":
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._fd = fd
                    self._locked = True
                    return True
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    time.sleep(min(0.05, remaining))
        finally:
            if not self._locked:
                os.close(fd)

    def release(self):
        if not self._locked:
            return
        fd, self._fd, self._locked = self._fd, None, False
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self):
        if not self.acquire():
            raise TimeoutError("runtime lock is busy: " + self.path)
        return self

    def __exit__(self, *exc):
        self.release()
        return False


@contextmanager
def file_lock(path, timeout=10.0):
    """临界区写法：拿不到锁抛 TimeoutError；锁文件永不 unlink。"""
    lock = FileLock(path, timeout)
    if not lock.acquire():
        raise TimeoutError("runtime lock is busy: " + lock.path)
    try:
        yield
    finally:
        lock.release()


def _read_marker(marker):
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _hashes(value):
    """只收文件名 → 非空字符串摘要；"_" 前缀是元数据（如 _loadedAt），不入表。"""
    if not isinstance(value, dict):
        return {}
    return {key: digest for key, digest in value.items()
            if isinstance(key, str) and not key.startswith("_")
            and isinstance(digest, str) and digest}


def _write_marker(marker, data):
    """原子写入（同目录 mkstemp + os.replace）：读方永远看不到半个 JSON。"""
    fd, name = tempfile.mkstemp(prefix=marker.name + ".", suffix=".tmp", dir=marker.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = None
            json.dump(data, stream, ensure_ascii=False)
            stream.write("\n")
        os.replace(name, marker)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def record_restart(runtime, data):
    """合并写入重启意图（持标记锁）。重复记录只累加 hashes，不清除既有 installation。"""
    if not isinstance(data, dict):
        raise ValueError("restart data must be a JSON object")
    runtime = Path(runtime)
    marker = runtime / MARKER_NAME
    with file_lock(runtime / LOCK_NAME):
        previous = _read_marker(marker)
        hashes = _hashes(previous.get("hashes"))
        hashes.update(_hashes(data.get("hashes")))
        if data.get("state") == "pending-install":
            previous.pop("publishedAt", None)   # 新意图不得继承上一次发布的盖章
        previous.update(data)
        previous.update(createdAt=time.time(), hashes=hashes)
        _write_marker(marker, previous)
    return {"ok": True}


@contextmanager
def publish_install(runtime, installation):
    """ASAR 发布与其重启意图共用一把锁；没有意图时不创建标记（不凭空提醒重启）。"""
    runtime = Path(runtime)
    marker = runtime / MARKER_NAME
    if not marker.is_file():
        yield
        return
    with file_lock(runtime / LOCK_NAME):
        data = _read_marker(marker)
        owned = data.get("installation") == installation
        yield
        if owned and marker.is_file() and _read_marker(marker) == data:
            data["publishedAt"] = time.time() * 1000
            try:
                _write_marker(marker, data)
            except OSError as exc:
                # asar 已发布，只是盖章失败：保持「未确认」而不是伪造成功
                print("[zusage] installation publication record failed: " + str(exc),
                      file=sys.stderr)


def ack_restart(runtime, loaded):
    """只清除「实际已加载且哈希一致」的条目；不清别的进程/更晚产生的意图。"""
    if not isinstance(loaded, dict):
        raise ValueError("loaded hashes must be a JSON object")
    runtime = Path(runtime)
    marker = runtime / MARKER_NAME
    with file_lock(runtime / LOCK_NAME):
        if not marker.exists():
            return {"ok": True, "cleared": False}
        data = _read_marker(marker)
        loaded_at = loaded.get("_loadedAt")
        created_at = data.get("createdAt")
        if not isinstance(created_at, (int, float)) or isinstance(created_at, bool):
            created_at = marker.stat().st_mtime
        # 进程启动早于意图产生 = 它加载的必然是旧代码，不能替新标记背书
        if isinstance(loaded_at, (int, float)) and created_at * 1000 > loaded_at:
            return {"ok": True, "cleared": False}
        installation = data.get("installation")
        if data.get("state") == "pending-install" or installation is not None:
            published_at = data.get("publishedAt")
            if (not isinstance(installation, dict)
                    or not isinstance(published_at, (int, float)) or isinstance(published_at, bool)
                    or not isinstance(loaded_at, (int, float)) or isinstance(loaded_at, bool)
                    or loaded_at <= published_at):
                return {"ok": True, "cleared": False}
        hashes = _hashes(data.get("hashes"))
        if hashes:
            remaining = {key: digest for key, digest in hashes.items()
                         if loaded.get(key) != digest}
            if remaining:
                data["hashes"] = remaining
                _write_marker(marker, data)
                return {"ok": True, "cleared": False}
        marker.unlink()
    return {"ok": True, "cleared": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description="runtime lock / restart marker helper")
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--hold", metavar="LOCKPATH")
    operation.add_argument("--record-restart", metavar="RUNTIME")
    operation.add_argument("--ack-restart", nargs=2, metavar=("RUNTIME", "LOADED_JSON"))
    args = parser.parse_args(argv)
    if args.hold:
        try:
            with file_lock(args.hold, timeout=0):
                print(json.dumps({"locked": True}, separators=(",", ":")), flush=True)
                while sys.stdin.buffer.read(65536):
                    pass
        except TimeoutError:
            print(json.dumps({"locked": False}, separators=(",", ":")), flush=True)
    elif args.record_restart:
        print(json.dumps(record_restart(args.record_restart, json.load(sys.stdin))))
    else:
        print(json.dumps(ack_restart(args.ack_restart[0], json.loads(args.ack_restart[1]))))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
