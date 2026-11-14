# -*- coding: utf-8 -*-
"""语法自检按宿主 package.json type 选解析 goal 的回归（借鉴上游 issue #255）。

3.18.x 客户端入口是 ESM（顶层 import），.js 后缀（CJS goal）必误报、fail-closed
挡安装。锁定的行为：
- type=module → 单跑 .mjs；type=commonjs/缺键 → 单跑 .js；
- type 读不到（坏 asar/缺 package.json）→ 双跑 .js→.mjs，任一通过即过（零误报
  零漏报：语法上 CJS⊂ESM，合法文件至少一个 goal 通过，真语法错误两个都不过）；
- 无法执行（OSError）→ 跳过语义不变（True），且无临时文件残留。

运行：python -m unittest discover -s tests
"""
import io
import json
import os
import struct
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys_path_guard = str(REPO)
import sys  # noqa: E402
if sys_path_guard not in sys.path:
    sys.path.insert(0, sys_path_guard)
import patch_install as pi  # noqa: E402

ALIGN = 4   # repack 的 header 对齐单位（与 pi.ALIGN 保持一致地手写，避免依赖私有值）


def make_asar(files: dict) -> Path:
    """files: {asar内路径(带前导/): bytes} → 写入临时 asar，返回路径。

    按插入序分配 offset，payload 同序拼接；header 打包格式与 repack 一致
    （<4I pickle 前缀 + 对齐 padding + payload）。"""
    root = {"files": {}}
    for path, data in files.items():
        parts = path.strip("/").split("/")
        node = root["files"]
        for part in parts[:-1]:
            node = node.setdefault(part, {"files": {}})["files"]
        leaf = node.setdefault(parts[-1], {})
        leaf["size"] = len(data)
    header_bytes = None
    off = 0
    for path, data in files.items():
        parts = path.strip("/").split("/")
        node = root["files"]
        for part in parts[:-1]:
            node = node[part]["files"]
        leaf = node[parts[-1]]
        leaf["offset"] = str(off)
        off += len(data)
    header_bytes = json.dumps(root, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    d = len(header_bytes)
    c = (d + 4 + ALIGN - 1) // ALIGN * ALIGN
    b_total = c + 4
    payload = b"".join(files[p] for p in files)
    blob = struct.pack("<4I", 4, b_total, c, d) + header_bytes + b"\0" * (c - d - 4) + payload
    fd, name = tempfile.mkstemp(prefix="zusage-asar-", suffix=".asar")
    os.close(fd)
    tmp = Path(name)
    tmp.write_bytes(blob)
    return tmp


CJS_OK = b"const x = require('path'); module.exports = x;\n"
ESM_TOP_IMPORT = b"import { app } from 'electron';\nexport default app;\n"
PKG_MODULE = b'{"name":"zcode","type":"module"}\n'
PKG_CJS = b'{"name":"zcode","type":"commonjs"}\n'
PKG_NO_TYPE = b'{"name":"zcode"}\n'


class PackageTypeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _asar(self, files):
        asar = make_asar(files)
        self.addCleanup(lambda: asar.unlink(missing_ok=True))
        return asar

    def test_module_type(self):
        asar = self._asar({"/package.json": PKG_MODULE, "/out/main/index.js": CJS_OK})
        self.assertEqual(pi.asar_package_type(asar), "module")

    def test_commonjs_type(self):
        asar = self._asar({"/package.json": PKG_CJS, "/out/main/index.js": CJS_OK})
        self.assertEqual(pi.asar_package_type(asar), "commonjs")

    def test_missing_type_key(self):
        asar = self._asar({"/package.json": PKG_NO_TYPE, "/out/main/index.js": CJS_OK})
        self.assertEqual(pi.asar_package_type(asar), "")

    def test_broken_package_json(self):
        asar = self._asar({"/package.json": b"{not json", "/out/main/index.js": CJS_OK})
        self.assertIsNone(pi.asar_package_type(asar))

    def test_missing_package_json(self):
        asar = self._asar({"/out/main/index.js": CJS_OK})
        self.assertIsNone(pi.asar_package_type(asar))

    def test_non_asar_garbage(self):
        garbage = self.dir / "app.asar"
        garbage.write_bytes(b"\0" * 64)
        self.assertIsNone(pi.asar_package_type(garbage))


class SyntaxCheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.dummy_exe = self.dir / "dummy-zcode.exe"
        self.dummy_exe.write_bytes(b"MZ")
        self._exe_patch = unittest.mock.patch.object(pi, "ZCODE_EXE", self.dummy_exe)
        self._exe_patch.start()
        self.addCleanup(self._exe_patch.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, files, results, raises=None):
        """results: 每次 run_text 返回的 returncode 序列；返回 (ok, argv列表, stdout)。

        ZCODE_EXE 的 patch 必须在 set_target 之后：set_target 会按 asar 位置重新
        探测宿主可执行文件，覆盖先前的 patch 值。"""
        asar = make_asar(files)
        self.addCleanup(lambda: asar.unlink(missing_ok=True))
        pi.set_target(asar)
        argvs = []
        it = iter(results)

        def fake_run_text(cmd, **kw):
            argvs.append(list(cmd))
            if raises:
                raise raises
            return unittest.mock.Mock(returncode=next(it), stderr="boom")

        with unittest.mock.patch.object(pi, "ZCODE_EXE", self.dummy_exe), \
             unittest.mock.patch.object(pi, "run_text", side_effect=fake_run_text), \
             redirect_stdout(io.StringIO()) as out:
            ok = pi.syntax_check(asar)
        return ok, argvs, out.getvalue()

    def test_module_type_runs_mjs_single(self):
        ok, argvs, _ = self._run({"/package.json": PKG_MODULE, "/out/main/index.js": ESM_TOP_IMPORT},
                                 [0])
        self.assertTrue(ok)
        self.assertEqual(len(argvs), 1)
        self.assertEqual(argvs[0][1], "--check")
        self.assertTrue(argvs[0][2].endswith(".mjs"))

    def test_commonjs_type_runs_js_single(self):
        ok, argvs, _ = self._run({"/package.json": PKG_CJS, "/out/main/index.js": CJS_OK}, [0])
        self.assertTrue(ok)
        self.assertEqual(len(argvs), 1)
        self.assertTrue(argvs[0][2].endswith(".js"))

    def test_missing_type_key_runs_js_single(self):
        ok, argvs, _ = self._run({"/package.json": PKG_NO_TYPE, "/out/main/index.js": CJS_OK}, [0])
        self.assertTrue(ok)
        self.assertEqual(len(argvs), 1)
        self.assertTrue(argvs[0][2].endswith(".js"))

    def test_unknown_type_dual_run_js_fails_mjs_passes(self):
        ok, argvs, _ = self._run({"/out/main/index.js": ESM_TOP_IMPORT}, [1, 0])
        self.assertTrue(ok)
        self.assertEqual(len(argvs), 2)
        self.assertTrue(argvs[0][2].endswith(".js"))
        self.assertTrue(argvs[1][2].endswith(".mjs"))

    def test_unknown_type_dual_run_js_passes_first(self):
        ok, argvs, _ = self._run({"/out/main/index.js": CJS_OK}, [0])
        self.assertTrue(ok)
        self.assertEqual(len(argvs), 1)

    def test_unknown_type_dual_run_both_fail_rejected(self):
        ok, argvs, _ = self._run({"/out/main/index.js": b"definitely not))) js"}, [1, 1])
        self.assertFalse(ok)   # 两个 goal 都不认 = 真语法错误，gate 仍咬人
        self.assertEqual(len(argvs), 2)

    def test_run_error_skips_and_cleans_up(self):
        ok, argvs, _ = self._run({"/package.json": PKG_MODULE, "/out/main/index.js": CJS_OK},
                                 [], raises=OSError("spawn failed"))
        self.assertTrue(ok)   # 跳过语义不变
        leftovers = list(pi.HERE.glob(".entry-check.*"))
        self.assertEqual(leftovers, [])

    def test_missing_exe_skips(self):
        asar = make_asar({"/package.json": PKG_MODULE, "/out/main/index.js": CJS_OK})
        self.addCleanup(lambda: asar.unlink(missing_ok=True))
        with unittest.mock.patch.object(pi, "ZCODE_EXE", self.dir / "no-such.exe"), \
             redirect_stdout(io.StringIO()) as out:
            self.assertTrue(pi.syntax_check(asar))
        self.assertIn("跳过", out.getvalue())


class NodeGoalSmoke(unittest.TestCase):
    """真 node 语义锚点（有 ZCode 才跑）：--check 按 .mjs 是权威 ESM goal，真垃圾
    在任何后缀下必败。不锁 ESM 内容在 .js 下的结果——node ≥23 对无上下文 .js 做
    ESM 自动探测（会过），老 node 按 CJS goal（会败），两种都合法、双跑方案兼容。"""

    def test_esm_goal_semantics(self):
        exe = pi.ZCODE_EXE
        if not exe or not Path(exe).is_file():
            self.skipTest("ZCode executable not available")
        env_probe = dict(os.environ, ELECTRON_RUN_AS_NODE="1")

        def check(suffix, blob):
            import tempfile as _tf
            fd, name = _tf.mkstemp(suffix=suffix)
            tmp = Path(name)
            try:
                os.close(fd)
                tmp.write_bytes(blob)
                return pi.run_text([str(exe), "--check", str(tmp)], env=env_probe).returncode
            finally:
                tmp.unlink(missing_ok=True)

        self.assertEqual(check(".mjs", ESM_TOP_IMPORT), 0)      # 权威 ESM goal 必过
        self.assertNotEqual(check(".mjs", b"definitely ))) not js"), 0)
        self.assertNotEqual(check(".js", b"definitely ))) not js"), 0)


if __name__ == "__main__":
    unittest.main()
