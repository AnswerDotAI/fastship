import runpy, subprocess, sys

import pytest
import fastship.release as relmod


def test_zig_scaffold_builds_loadable_library(tmp_path):
    pytest.importorskip("ziglang")
    cffi = pytest.importorskip("cffi")
    root = relmod.ship_zig_new("zig-proj", path=tmp_path)
    subprocess.run([sys.executable, "build_lib.py"], cwd=root, check=True, timeout=60)
    ffi = cffi.FFI()
    ffi.cdef("int add(int a, int b);")
    lib = runpy.run_path(str(root/"zig_proj"/"_libpath.py"))["LIB_PATH"]
    assert ffi.dlopen(str(lib)).add(2, 3) == 5
