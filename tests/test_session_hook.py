# -*- coding: utf-8 -*-
"""插件钩子的安全与重启提醒回归：tarball 解压面上限、路径逃逸、冷文件重启意图。

运行：python -m unittest discover -s tests
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "zusage_session_start", REPO / "plugin" / "hooks" / "session-start.py")
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)


def make_tar(members):
    """members: [(name, bytes)] → gzip tarball 字节。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class TarMembersTests(unittest.TestCase):
    def test_extracts_whitelisted_members(self):
        raw = make_tar([("repo-main/install.py", b"print(1)\n"),
                        ("repo-main/other.py", b"ignored")])
        got = hook._tar_members(raw, {"install.py"})
        self.assertEqual(got, {"install.py": b"print(1)\n"})

    def test_missing_member_raises(self):
        raw = make_tar([("repo-main/install.py", b"x")])
        with self.assertRaises(RuntimeError):
            hook._tar_members(raw, {"install.py", "zusage.py"})

    def test_parent_traversal_rejected(self):
        raw = make_tar([("repo-main/../evil.py", b"x")])
        with self.assertRaises(ValueError):
            hook._tar_members(raw, {"evil.py"})

    def test_absolute_member_rejected(self):
        raw = make_tar([("/repo-main/install.py", b"x")])
        with self.assertRaises(ValueError):
            hook._tar_members(raw, {"install.py"})

    def test_oversized_member_rejected(self):
        raw = make_tar([("repo-main/install.py", b"\0" * (hook.MAX_MEMBER_BYTES + 1))])
        with self.assertRaises(ValueError):
            hook._tar_members(raw, {"install.py"})


class RestartIntentTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        shutil.copy2(REPO / "runtime_lock.py", self.data_dir / "runtime_lock.py")
        (self.data_dir / "inject-main.cjs").write_text("// loader\n", encoding="utf-8")
        (self.data_dir / "overlay.js").write_text("// overlay\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def marker(self):
        return self.data_dir / hook.RESTART_MARKER

    def test_hot_file_change_records_nothing(self):
        self.assertFalse(hook._record_restart(self.data_dir, ["overlay.js"], "hook-repo"))
        self.assertFalse(self.marker().exists())

    def test_cold_file_change_records_hash(self):
        self.assertTrue(hook._record_restart(self.data_dir, ["inject-main.cjs"], "hook-repo"))
        data = json.loads(self.marker().read_text(encoding="utf-8"))
        self.assertEqual(data["state"], "pending-sync")
        self.assertEqual(len(data["hashes"]["inject-main.cjs"]), 64)

    def test_notice_absent_without_marker(self):
        self.assertIsNone(hook._restart_notice(self.data_dir))

    def test_notice_present_for_install_marker(self):
        hook._record_restart(self.data_dir, ["inject-main.cjs"], "hook-repo")
        self.marker().touch()
        data = json.loads(self.marker().read_text(encoding="utf-8"))
        data["source"] = "install"
        self.marker().write_text(json.dumps(data), encoding="utf-8")
        old = time.time() - 120
        os.utime(self.marker(), (old, old))
        notice = hook._restart_notice(self.data_dir)
        self.assertIsNotNone(notice)
        self.assertIn("重启 ZCode", notice["systemMessage"])

    def test_fresh_hook_marker_gets_grace(self):
        # 钩子刚写、客户端回执可能仍在途：本轮不提醒（避免每次更新误报）
        hook._record_restart(self.data_dir, ["inject-main.cjs"], "hook-repo")
        self.assertIsNone(hook._restart_notice(self.data_dir))

    def test_stale_hook_marker_still_notices(self):
        hook._record_restart(self.data_dir, ["inject-main.cjs"], "hook-repo")
        old = time.time() - 120
        os.utime(self.marker(), (old, old))
        self.assertIsNotNone(hook._restart_notice(self.data_dir))


if __name__ == "__main__":
    unittest.main()
