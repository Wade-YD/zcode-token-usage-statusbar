# -*- coding: utf-8 -*-
"""待收尾包（TMP + 来源指纹 sidecar）的信任链回归。

覆盖：指纹校验、源 asar 变化后拒绝用旧包覆盖、TMP 被改动后拒绝、发布后清理元数据。
不触碰真实 ZCode 安装：全部在临时目录里造 asar/TMP。
运行：python -m unittest discover -s tests
"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import patch_install as pi  # noqa: E402


class PendingMetaTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.asar = root / "app.asar"
        self.runtime = root / "runtime"
        self.runtime.mkdir()
        (self.runtime / "inject-main.cjs").write_text("// loader\n", encoding="utf-8")
        self.asar.write_bytes(b"original-asar")
        pi.set_target(self.asar)
        pi.set_runtime(self.runtime)

    def tearDown(self):
        self._tmp.cleanup()

    def _stage(self):
        """模拟 install()：repack 后写 TMP + 身份元数据。"""
        pi.TMP.write_bytes(b"patched-asar")
        pi._write_pending_meta(pi._fingerprint(self.asar, deep=True),
                               pi._fingerprint(pi.TMP, deep=True))

    def test_fingerprint_deterministic(self):
        a = pi._fingerprint(self.asar, deep=True)
        b = pi._fingerprint(self.asar, deep=True)
        self.assertEqual(a, b)
        self.assertEqual(len(a["sha256"]), 64)

    def test_verify_ok_after_stage(self):
        self._stage()
        self.assertEqual(pi.verify_pending(deep=True), (True, "ok"))
        self.assertEqual(pi.verify_pending(), (True, "ok"))

    def test_missing_tmp(self):
        self.assertEqual(pi.verify_pending()[1], "no-tmp")

    def test_missing_meta(self):
        pi.TMP.write_bytes(b"patched")
        self.assertEqual(pi.verify_pending()[1], "no-meta")

    def test_source_changed_rejected(self):
        self._stage()
        time.sleep(0.01)
        self.asar.write_bytes(b"official-new-build")   # ZCode 又升级：mtime/size 都变
        ok, reason = pi.verify_pending()
        self.assertFalse(ok)
        self.assertEqual(reason, "source-changed")

    def test_tmp_tampered_rejected(self):
        self._stage()
        time.sleep(0.01)
        pi.TMP.write_bytes(b"tampered")
        ok, reason = pi.verify_pending()
        self.assertFalse(ok)
        self.assertEqual(reason, "tmp-changed")

    def test_finalize_publishes_and_clears_meta(self):
        self._stage()
        self.assertEqual(pi.finalize_pending(), "published")
        self.assertEqual(self.asar.read_bytes(), b"patched-asar")
        self.assertFalse(pi.TMP.exists())
        self.assertFalse(pi.TMP_META.exists())

    def test_finalize_refuses_stale_package(self):
        self._stage()
        time.sleep(0.01)
        self.asar.write_bytes(b"official-new-build")
        self.assertEqual(pi.finalize_pending(), "untrusted")
        # 关键：新版 asar 不被旧包覆盖，待收尾文件保留供排查
        self.assertEqual(self.asar.read_bytes(), b"official-new-build")
        self.assertTrue(pi.TMP.exists())

    def test_meta_rejects_wrong_runtime(self):
        self._stage()
        data = json.loads(pi.TMP_META.read_text(encoding="utf-8"))
        data["runtime"] = str((self.runtime / "other").resolve())
        pi.TMP_META.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(pi.verify_pending()[1], "no-meta")


if __name__ == "__main__":
    unittest.main()
