# -*- coding: utf-8 -*-
"""runtime_lock 重启标记语义回归。

覆盖四个判定：哈希一致才清除、旧进程不清新标记、未发布的注入不清除、
跨进程锁互斥。运行：python -m unittest discover -s tests
"""
import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import runtime_lock  # noqa: E402

MARKER = ".plugin-restart-required"


class RestartMarkerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.runtime = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def marker(self):
        return self.runtime / MARKER

    def read_marker(self):
        return json.loads(self.marker().read_text(encoding="utf-8"))

    def test_matching_hash_from_newer_process_clears(self):
        runtime_lock.record_restart(self.runtime, {
            "state": "pending-sync", "hashes": {"inject-main.cjs": "a" * 64}})
        self.assertTrue(self.marker().is_file())
        result = runtime_lock.ack_restart(self.runtime, {
            "_loadedAt": (time.time() + 5) * 1000, "inject-main.cjs": "a" * 64})
        self.assertTrue(result["cleared"])
        self.assertFalse(self.marker().exists())

    def test_hash_mismatch_keeps_marker(self):
        runtime_lock.record_restart(self.runtime, {
            "state": "pending-sync", "hashes": {"inject-main.cjs": "a" * 64}})
        result = runtime_lock.ack_restart(self.runtime, {
            "_loadedAt": (time.time() + 5) * 1000, "inject-main.cjs": "b" * 64})
        self.assertFalse(result["cleared"])
        self.assertTrue(self.marker().is_file())

    def test_process_started_before_intent_cannot_ack(self):
        # 旧进程（启动早于意图）加载的必然是旧代码，不能替新标记背书
        runtime_lock.record_restart(self.runtime, {
            "state": "pending-sync", "hashes": {"inject-main.cjs": "a" * 64}})
        result = runtime_lock.ack_restart(self.runtime, {
            "_loadedAt": (time.time() - 60) * 1000, "inject-main.cjs": "a" * 64})
        self.assertFalse(result["cleared"])
        self.assertTrue(self.marker().is_file())

    def test_pending_install_requires_publication(self):
        installation = {"asar": "C:/ZCode/app.asar", "entryHash": "c" * 64}
        runtime_lock.record_restart(self.runtime, {
            "state": "pending-install", "hashes": {"inject-main.cjs": "a" * 64},
            "installation": installation})
        # 未盖章：即便哈希一致也不清除（替换可能还躺在 TMP 里）
        result = runtime_lock.ack_restart(self.runtime, {
            "_loadedAt": (time.time() + 5) * 1000, "inject-main.cjs": "a" * 64})
        self.assertFalse(result["cleared"])
        self.assertTrue(self.marker().is_file())
        # 发布盖章后才允许清除
        with runtime_lock.publish_install(self.runtime, installation):
            pass
        self.assertIn("publishedAt", self.read_marker())
        result = runtime_lock.ack_restart(self.runtime, {
            "_loadedAt": (time.time() + 5) * 1000, "inject-main.cjs": "a" * 64})
        self.assertTrue(result["cleared"])
        self.assertFalse(self.marker().exists())

    def test_cli_ack_clears_marker(self):
        # inject-main.cjs 走的就是这条命令行路径（execFile python runtime_lock.py --ack-restart）
        runtime_lock.record_restart(self.runtime, {
            "state": "pending-sync", "hashes": {"inject-main.cjs": "a" * 64}})
        loaded = json.dumps({"_loadedAt": (time.time() + 5) * 1000, "inject-main.cjs": "a" * 64})
        r = subprocess.run(
            [sys.executable, str(REPO / "runtime_lock.py"), "--ack-restart",
             str(self.runtime), loaded],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(json.loads(r.stdout)["cleared"])
        self.assertFalse(self.marker().exists())

    def test_ack_without_marker_is_noop(self):
        result = runtime_lock.ack_restart(self.runtime, {"_loadedAt": time.time() * 1000})
        self.assertFalse(result["cleared"])
        self.assertFalse(self.marker().exists())

    def test_new_intent_drops_old_publication(self):
        installation = {"asar": "x", "entryHash": "c" * 64}
        runtime_lock.record_restart(self.runtime, {
            "state": "pending-install", "installation": installation,
            "hashes": {"inject-main.cjs": "a" * 64}})
        with runtime_lock.publish_install(self.runtime, installation):
            pass
        self.assertIn("publishedAt", self.read_marker())
        # 新一轮意图不得继承旧盖章（否则客户端可能拿旧发布为新技术背书）
        runtime_lock.record_restart(self.runtime, {
            "state": "pending-install", "installation": installation,
            "hashes": {"inject-main.cjs": "d" * 64}})
        self.assertNotIn("publishedAt", self.read_marker())


class FileLockTests(unittest.TestCase):
    def test_second_holder_times_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "test.lock"
            proc = subprocess.Popen(
                [sys.executable, str(REPO / "runtime_lock.py"), "--hold", str(lock_path)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            try:
                line = proc.stdout.readline()
                self.assertEqual(json.loads(line), {"locked": True})
                with self.assertRaises(TimeoutError):
                    with runtime_lock.file_lock(lock_path, timeout=0.2):
                        pass
            finally:
                proc.kill()
                proc.wait(timeout=10)
                proc.stdin.close()
                proc.stdout.close()

    def test_lock_released_after_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "test.lock"
            with runtime_lock.file_lock(lock_path, timeout=1):
                pass
            with runtime_lock.file_lock(lock_path, timeout=1):   # 可再次获取
                pass


if __name__ == "__main__":
    unittest.main()
