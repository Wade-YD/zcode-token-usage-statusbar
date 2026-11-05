# -*- coding: utf-8 -*-
"""无控制台宿主加固回归：孤儿临时文件清扫、探测命令无窗标志、手动命令模板、WMI 前置。

运行：python -m unittest discover -s tests
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import install as installer  # noqa: E402
import patch_install as pi  # noqa: E402


class SweepTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.asar = self.dir / "app.asar"
        self.asar.write_bytes(b"x")
        pi.set_target(self.asar)          # sweep 默认目录 = ASAR.parent
        pi.set_runtime(self.dir / "runtime")

    def tearDown(self):
        self._tmp.cleanup()

    def test_stale_swept_fresh_kept(self):
        stale = self.dir / ".app.asar.zusage.abc.tmp"
        stale.write_bytes(b"x")
        fresh = self.dir / ".app.asar.zusage.def.tmp"
        fresh.write_bytes(b"x")
        meta_stale = self.dir / ".zusage-meta.ghi"
        meta_stale.write_text("{}")
        old = time.time() - 700
        os.utime(stale, (old, old))
        os.utime(meta_stale, (old, old))
        removed = pi._sweep_stale_stages()
        self.assertGreaterEqual(removed, 2)
        self.assertFalse(stale.exists())
        self.assertFalse(meta_stale.exists())
        self.assertTrue(fresh.exists())   # 活跃文件（mtime 新）不动

    def test_fixed_names_untouched(self):
        fixed_tmp = self.dir / "app.asar.zusage.tmp"
        fixed_tmp.write_bytes(b"x")
        meta = self.dir / "app.asar.zusage.tmp.json"
        meta.write_text("{}")
        lock = self.dir / "app.asar.zusage.monitor.lock"
        lock.write_bytes(b"\0")
        old = time.time() - 700
        for p in (fixed_tmp, meta, lock):
            os.utime(p, (old, old))
        pi._sweep_stale_stages()
        for p in (fixed_tmp, meta, lock):
            self.assertTrue(p.exists(), p.name)


class RunTextTests(unittest.TestCase):
    def test_probe_commands_are_windowless(self):
        if os.name != "nt":
            self.skipTest("windows only")
        captured = {}
        real_run = subprocess.run

        def spy(cmd, **kwargs):
            captured.update(kwargs)
            return real_run(cmd, **kwargs)

        with unittest.mock.patch("subprocess.run", side_effect=spy):
            r = installer.run_text(["cmd", "/c", "echo", "ok"])
        flags = captured.get("creationflags", 0)
        self.assertEqual(flags & subprocess.CREATE_NO_WINDOW, subprocess.CREATE_NO_WINDOW)
        self.assertEqual(r.stdout.strip(), "ok")


class ManualCmdTests(unittest.TestCase):
    def test_uses_absolute_interpreter_and_script(self):
        cmd = pi._manual_cmd("install", "--finalize")
        self.assertTrue(cmd.startswith('"' + pi.PYEXE + '"'))
        self.assertIn("patch_install.py", cmd)
        self.assertNotIn("python patch_install", cmd)

    def test_wmi_interpreter_prefers_pythonw(self):
        if os.name != "nt":
            self.skipTest("windows only")
        interp = pi._wmi_interpreter()
        if interp is None:      # 无 pythonw：WMI 通道应被跳过而不是报错
            return
        self.assertEqual(interp.name, "pythonw.exe")
        self.assertTrue(interp.is_file())


if __name__ == "__main__":
    unittest.main()
