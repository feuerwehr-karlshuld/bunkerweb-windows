#!/usr/bin/env python3
"""Fast, platform independent tests of the Windows port (no Windows, no build needed).

  python windows/build/test-port.py            # everything
  python windows/build/test-port.py -k winffi  # a subset (unittest pattern)

* path rewrite (pathrewrite.py): golden cases, idempotence, what must NOT be touched
* patch series tooling (patchseries.py): lint rules
* Python isabs shim (python-site/sitecustomize.py) against pathlib's Windows flavour
* Lua ffi proxy (winffi.lua) with a fake ffi, run in LuaJIT through lupa (skipped, loudly, when lupa is missing;
  set BW_REQUIRE_LUPA=1 to make that an error, CI does)
"""

import importlib.util
import ntpath
import os
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath

BUILD = Path(__file__).resolve().parent
WINDOWS = BUILD.parent
sys.path.insert(0, str(BUILD))

import pathrewrite  # noqa: E402
import patchseries  # noqa: E402


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PathRewrite(unittest.TestCase):
    def rw(self, text, suffix=".py"):
        return pathrewrite.rewrite_text(text, suffix)[0]

    def test_plain_paths(self):
        self.assertEqual(self.rw('"/etc/bunkerweb/variables.env"'), '"/bunkerweb/etc/bunkerweb/variables.env"')
        self.assertEqual(self.rw("/usr/share/bunkerweb/core"), "/bunkerweb/usr/share/bunkerweb/core")
        self.assertEqual(self.rw("/var/cache/bunkerweb/x /var/tmp/bunkerweb/y"), "/bunkerweb/var/cache/bunkerweb/x /bunkerweb/var/tmp/bunkerweb/y")
        self.assertEqual(self.rw("/var/www/html/index"), "/bunkerweb/var/www/html/index")
        self.assertEqual(self.rw("/etc/nginx/nginx.conf"), "/bunkerweb/etc/nginx/nginx.conf")

    def test_urls_and_templates(self):
        self.assertEqual(self.rw("sqlite:////var/lib/bunkerweb/db.sqlite3"), "sqlite:///" + "/bunkerweb/var/lib/bunkerweb/db.sqlite3")
        self.assertEqual(self.rw("{% if x %}/var/www/html{% endif %}", ".conf"), "{% if x %}/bunkerweb/var/www/html{% endif %}")

    def test_relative_to_something_is_untouched(self):
        for text in ("{{ root }}/etc/bunkerweb/x", "$HOME/etc/bunkerweb", "a/etc/bunkerweb", "~/etc/bunkerweb", "x)/etc/bunkerweb", "my-/etc/bunkerweb"):
            self.assertEqual(self.rw(text), text, text)

    def test_system_paths_are_untouched(self):
        for text in ("/dev/null", "/usr/bin/env", "/etc/os-release", "/proc/uptime", "/var/log/syslog", "/usr/local/etc/nginx/nginx.conf"):
            self.assertEqual(self.rw(text), text)

    def test_idempotent(self):
        once = self.rw('open("/etc/bunkerweb/x"); Path("/", "etc", "bunkerweb"); join(sep, "usr", "share", "bunkerweb")')
        self.assertEqual(self.rw(once), once)

    def test_path_parts_only_in_python(self):
        text = 'join(sep, "usr", "share", "bunkerweb", "core")'
        self.assertEqual(self.rw(text), 'join(sep, "bunkerweb", "usr", "share", "bunkerweb", "core")')
        self.assertEqual(self.rw(text, ".lua"), text)
        self.assertEqual(self.rw('Path("/", "etc", "nginx")'), 'Path("/", "bunkerweb", "etc", "nginx")')

    def test_counts(self):
        _, count = pathrewrite.rewrite_text("/etc/bunkerweb /usr/share/bunkerweb")
        self.assertEqual(count, 2)

    def test_skipped_locations_report(self):
        self.assertEqual(pathrewrite.skipped_locations("/bunkerweb/etc/bunkerweb ok"), [])
        self.assertTrue(pathrewrite.skipped_locations("{{ x }}/etc/bunkerweb"))

    def test_rewrite_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            (src / "common").mkdir()
            (src / "deps").mkdir()
            (src / "common" / "a.py").write_text('p = "/etc/bunkerweb"\r\n', newline="")
            (src / "common" / "b.png").write_bytes(b"/etc/bunkerweb")
            (src / "deps" / "c.py").write_text('p = "/etc/bunkerweb"\n')
            (src / "common" / "skip.py").write_text('p = "/etc/bunkerweb"\n')
            done = pathrewrite.rewrite_tree(src, skip={(src / "common" / "skip.py").resolve()})
            self.assertEqual(done, {"common/a.py": 1})
            self.assertEqual((src / "common" / "a.py").read_bytes(), b'p = "/bunkerweb/etc/bunkerweb"\r\n')  # CRLF kept
            self.assertEqual((src / "deps" / "c.py").read_text(), 'p = "/etc/bunkerweb"\n')
            self.assertEqual((src / "common" / "skip.py").read_text(), 'p = "/etc/bunkerweb"\n')


class PatchSeriesLint(unittest.TestCase):
    GOOD = (
        "From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001\n"
        "From: A <a@b.c>\nDate: Thu, 1 Jan 2026 00:00:00 +0000\nSubject: [PATCH] A subject\n\n"
        "Why this exists, written so that a reader a year from now understands it.\n\n"
        "Upstream: not submitted\nCheck: grep the symbol\n---\n a.txt | 2 +-\n\n"
        "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a\n+b\n"
    )

    def lint(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "x.patch").write_text(text)
            (Path(tmp) / ".patches").write_text("x.patch\n")
            return patchseries.lint(Path(tmp))

    def test_good_patch_passes(self):
        self.assertEqual(self.lint(self.GOOD), [])

    def test_missing_metadata_fails(self):
        self.assertTrue(self.lint(self.GOOD.replace("Upstream: not submitted\n", "")))
        self.assertTrue(self.lint(self.GOOD.replace("Check: grep the symbol\n", "")))
        self.assertTrue(self.lint(self.GOOD.replace("Why this exists, written so that a reader a year from now understands it.", "short")))


class IsabsShim(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module("bw_sitecustomize", WINDOWS / "overlay" / "python-site" / "sitecustomize.py")

    def test_rooted_paths_are_absolute(self):
        original = ntpath.isabs
        ntpath.isabs = self.module._rooted_isabs(original)
        try:
            for path in ("/bunkerweb/x", "\\bunkerweb\\x", "C:/x", "C:\\x", "//server/share"):
                self.assertTrue(ntpath.isabs(path), path)
                self.assertTrue(PureWindowsPath(path).is_absolute(), path)
            self.assertTrue(ntpath.isabs(b"/bunkerweb"))
            for path in ("x", "x/y", "C:x", "", "..\\x"):
                self.assertFalse(ntpath.isabs(path), path)
                self.assertFalse(PureWindowsPath(path).is_absolute(), path)
            self.assertTrue(ntpath.isabs(PureWindowsPath("/x")))
        finally:
            ntpath.isabs = original

    def test_original_is_restorable(self):
        original = ntpath.isabs
        shim = self.module._rooted_isabs(original)
        self.assertTrue(shim("/x"))
        self.assertEqual(original("C:/x"), True)  # the original is untouched by creating the shim


class WinffiLua(unittest.TestCase):
    def test_winffi(self):
        try:
            from lupa.luajit21 import LuaRuntime
        except ImportError:
            try:
                from lupa import LuaRuntime
            except ImportError:
                if os.environ.get("BW_REQUIRE_LUPA"):
                    self.fail("lupa is not installed (pip install lupa)")
                self.skipTest("lupa is not installed (pip install lupa); the winffi tests did not run")
        lua = LuaRuntime(unpack_returned_tuples=True)
        overlay = (WINDOWS / "overlay" / "src" / "bw" / "lua").as_posix()
        chunk = (BUILD / "test-winffi.lua").read_text(encoding="utf-8")
        failures = lua.eval("function(src, dir) return load(src, '@test-winffi.lua')(dir) end")(chunk, overlay)
        self.assertEqual([failure for failure in failures.values()], [])

    def test_wincompat_is_inert_outside_windows(self):
        try:
            from lupa.luajit21 import LuaRuntime
        except ImportError:
            self.skipTest("lupa/LuaJIT is not installed")
        lua = LuaRuntime(unpack_returned_tuples=True)
        overlay = (WINDOWS / "overlay" / "src" / "bw" / "lua").as_posix()
        result = lua.execute(
            "local ffi = require 'ffi'\n"
            f"package.path = '{overlay}/?.lua;' .. package.path\n"
            "local m = require 'bunkerweb.wincompat'\n"
            "return jit.os, next(m) == nil, package.loaded.ffi == ffi"
        )
        jit_os, empty, ffi_untouched = result
        if jit_os != "Windows":
            self.assertTrue(empty)
            self.assertTrue(ffi_untouched)


if __name__ == "__main__":
    unittest.main(verbosity=2)
