# -*- coding: utf-8 -*-
"""可见监控 breakaway 被拒的 WMI 幸存通道回归（借鉴上游 issue #208 同源修复）。

锁三条不变量：
1. breakaway 被拒（宿主 Job 拒收）→ 先试 WMI 静默幸存监控（--quiet 走 argv），
   成功则不再树内拉起（无双拉），并提示用户监控形态已变；
2. WMI 不可用时仍回退树内普通创建（「永不劣于基线」不变量）；
3. quiet 链全程不出现 CREATE_NEW_CONSOLE（静默链语义不被可见链改动波及）。

运行：python -m unittest discover -s tests
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import patch_install as pi  # noqa: E402


@unittest.skipUnless(os.name == "nt", "Windows-only spawn chain")
class VisibleSpawnSurvivorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.asar = self.dir / "app.asar"
        self.asar.write_bytes(b"x")
        pi.set_target(self.asar)
        pi.set_runtime(self.dir / "runtime")
        # HERE 打桩到临时目录：_spawn_monitor 的监控脚本存在性守卫在此探测。
        # 绝不能对 pi.HERE 下的真实文件 touch/unlink（曾因此误删仓库 install_monitor.py）。
        self.fake_here = self.dir / "fake-here"
        self.fake_here.mkdir()
        (self.fake_here / "install_monitor.py").write_bytes(b"# monitor stub\n")
        self._here_patch = unittest.mock.patch.object(pi, "HERE", self.fake_here)
        self._here_patch.start()
        self.addCleanup(self._here_patch.stop)
        self.epoch = 1700000000.5

    def tearDown(self):
        self._tmp.cleanup()

    def test_breakaway_denied_wmi_survivor(self):
        """breakaway 被拒 → WMI 静默幸存监控接管：argv 同构、无双拉、有提示。"""
        calls = []

        def fake_popen(cmd, **kw):
            calls.append(kw)
            if kw.get("creationflags", 0) & getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0):
                raise OSError(5, "Access is denied")   # 宿主 Job 拒收 breakaway
            return unittest.mock.Mock()

        interpreter = self.dir / "pythonw.exe"
        interpreter.write_bytes(b"")
        with unittest.mock.patch.object(pi, "_wmi_interpreter", return_value=interpreter), \
             unittest.mock.patch.object(pi, "_spawn_via_wmi", return_value=4321) as wmi, \
             unittest.mock.patch.object(pi.subprocess, "Popen", side_effect=fake_popen), \
             redirect_stdout(io.StringIO()) as out:
            ok = pi._spawn_monitor(quiet=False, epoch=self.epoch)
        self.assertTrue(ok)
        wmi.assert_called_once()
        argv = wmi.call_args[0][0]
        self.assertEqual(argv[0], str(interpreter))            # pythonw（不闪控制台）
        self.assertEqual(argv[-1], "--quiet")                  # 静默开关走 argv
        self.assertEqual(argv[2], "%.3f" % self.epoch)         # epoch/asar/runtime 同构
        self.assertEqual(argv[3], str(self.asar))
        self.assertEqual(argv[4], str(self.dir / "runtime"))
        self.assertEqual(len(calls), 1)                        # 仅 breakaway 那次失败尝试，无双拉
        self.assertIn("后台静默监控", out.getvalue())

    def test_breakaway_denied_wmi_unavailable_falls_back_plain(self):
        """WMI 不可用（无 pythonw）→ 树内普通创建仍被尝试（永不劣于基线）。"""
        calls = []

        def fake_popen(cmd, **kw):
            calls.append(kw)
            if kw.get("creationflags", 0) & getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0):
                raise OSError(5, "Access is denied")
            return unittest.mock.Mock()

        with unittest.mock.patch.object(pi, "_wmi_interpreter", return_value=None), \
             unittest.mock.patch.object(pi, "_spawn_via_wmi", return_value=None) as wmi, \
             unittest.mock.patch.object(pi.subprocess, "Popen", side_effect=fake_popen), \
             redirect_stdout(sys.stderr) as _:
            ok = pi._spawn_monitor(quiet=False, epoch=self.epoch)
        self.assertTrue(ok)
        wmi.assert_not_called()
        self.assertEqual(len(calls), 2)   # breakaway 失败 + 普通创建成功
        self.assertEqual(calls[-1]["creationflags"], subprocess.CREATE_NEW_CONSOLE)

    def test_breakaway_denied_wmi_fails_falls_back_plain(self):
        """WMI 拉起返回 None（如 PowerShell 不可用）→ 同样回退树内普通创建。"""
        calls = []

        def fake_popen(cmd, **kw):
            calls.append(kw)
            if kw.get("creationflags", 0) & getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0):
                raise OSError(5, "Access is denied")
            return unittest.mock.Mock()

        interpreter = self.dir / "pythonw.exe"
        interpreter.write_bytes(b"")
        with unittest.mock.patch.object(pi, "_wmi_interpreter", return_value=interpreter), \
             unittest.mock.patch.object(pi, "_spawn_via_wmi", return_value=None), \
             unittest.mock.patch.object(pi.subprocess, "Popen", side_effect=fake_popen), \
             redirect_stdout(sys.stderr) as _:
            ok = pi._spawn_monitor(quiet=False, epoch=self.epoch)
        self.assertTrue(ok)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[-1]["creationflags"], subprocess.CREATE_NEW_CONSOLE)

    def test_quiet_chain_never_new_console(self):
        """quiet 链回归：全程 creationflags 不含 CREATE_NEW_CONSOLE（防可见链改动波及）。"""
        flags_seen = []

        def fake_popen(cmd, **kw):
            flags_seen.append(kw.get("creationflags", 0))
            return unittest.mock.Mock()

        with unittest.mock.patch.object(pi, "_wmi_interpreter", return_value=None), \
             unittest.mock.patch.object(pi, "_spawn_via_wmi", return_value=None), \
             unittest.mock.patch.object(pi.subprocess, "Popen", side_effect=fake_popen):
            ok = pi._spawn_monitor(quiet=True, epoch=self.epoch)
        self.assertTrue(ok)
        self.assertTrue(flags_seen)
        self.assertTrue(all(not (f & subprocess.CREATE_NEW_CONSOLE) for f in flags_seen))

    def test_launch_monitor_no_monitor_env_short_circuit(self):
        """ZUSAGE_NO_MONITOR=1 仍短路（守卫在 launch_monitor，语义不变）。"""
        with unittest.mock.patch.dict(os.environ, {"ZUSAGE_NO_MONITOR": "1"}), \
             unittest.mock.patch.object(pi, "_spawn_monitor") as spawn:
            self.assertIsNone(pi.launch_monitor(self.epoch))
            spawn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
