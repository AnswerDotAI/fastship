import runpy,subprocess,sys,tomllib
from pathlib import Path

import pytest,fastship.release as relmod


def test_project_types():
    assert relmod._project_type(Path("."), {"build-system": {"build-backend": "maturin"}}) == "rust"
    assert relmod._project_type(Path("."), {"build-system": {"requires": ["ziglang==0.15.2"]}}) == "zig"
    assert relmod._project_type(Path("."), {}) == "python"


def test_new_projects_include_site_and_zig_scaffold(tmp_path):
    plain = relmod.ship_new("plain-proj", path=tmp_path)
    rust = relmod.ship_rs_new("rust-proj", path=tmp_path)
    zig = relmod.ship_zig_new("zig-proj", path=tmp_path)

    for root in (plain, rust, zig):
        assert (root / "_config.yml").read_text() == relmod._read_asset("_config.yml")
        assert (root / "_layouts" / "default.html").read_text() == relmod._read_asset("_layouts/default.html")
    assert tomllib.loads((rust/"pyproject.toml").read_text())["tool"]["uv"]["cache-keys"][-1] == {"file": ".git/fastws-cargo-key"}
    profiles = tomllib.loads((rust/"Cargo.toml").read_text())["profile"]
    assert profiles["release"] == {"lto": False, "codegen-units": 16, "package": {"rust-proj": {"incremental": True}}}
    assert profiles["dist"] == {
        "inherits": "release", "lto": True, "incremental": False, "codegen-units": 1, "strip": True,
        "package": {"rust-proj": {"incremental": False}}}
    assert "args: --profile dist --out dist" in (rust/".github"/"workflows"/"ci.yml").read_text()

    data = tomllib.loads((zig / "pyproject.toml").read_text())
    assert data["tool"]["fastship"]["wheel-only"]
    assert data["tool"]["cibuildwheel"]["build"] == "cp311-*"
    assert (zig / "build_lib.py").exists()
    assert (zig / "setup.py").exists()
    assert (zig / "src" / "lib.zig").exists()
    workflow = (zig / ".github" / "workflows" / "ci.yml").read_text()
    assert "ubuntu-24.04-arm" in workflow
    assert "macos-15-intel" in workflow
    assert "gh-action-pypi-publish" in workflow

    pytest.importorskip("ziglang")
    cffi = pytest.importorskip("cffi")
    subprocess.run([sys.executable, "build_lib.py"], cwd=zig, check=True)
    ffi = cffi.FFI()
    ffi.cdef("int add(int a, int b);")
    lib = runpy.run_path(str(zig/"zig_proj"/"_libpath.py"))["LIB_PATH"]
    assert ffi.dlopen(str(lib)).add(2, 3) == 5
