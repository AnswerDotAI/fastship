from pathlib import Path

import fastship.release as relmod


def _make_rs_project(root: Path, configured: bool = True, bins: bool = True, dynamic: bool = True):
    (root / "python" / "exhash.data" / "scripts").mkdir(parents=True)
    cfg = """
[tool.fastship]
branch = "main"

[tool.fastship.rs]
bins = ["exhash", "lnhashview"]
""" if configured else ""
    pyproj = """[build-system]
requires = ["maturin>=1.0,<2.0"]
build-backend = "maturin"

[project]
name = "exhash"
""" + ('dynamic = ["version"]\n' if dynamic else 'version = "0.1.2"\n') + """

[tool.maturin]
data = "python/exhash.data"
""" + cfg
    cargo = """[package]
name = "exhash"
version = "0.1.2"
edition = "2021"
"""
    if bins:
        cargo += """
[[bin]]
name = "exhash"
path = "src/bin/exhash.rs"

[[bin]]
name = "lnhashview"
path = "src/bin/lnhashview.rs"
"""
    (root / "pyproject.toml").write_text(pyproj, encoding="utf-8")
    (root / "Cargo.toml").write_text(cargo, encoding="utf-8")


def test_get_rs_config_reads_maturin_data_scripts(tmp_path):
    _make_rs_project(tmp_path)
    cfg = relmod.get_rs_config(tmp_path)

    assert cfg.version == "0.1.2"
    assert cfg.bins == ["exhash", "lnhashview"]
    assert cfg.data_scripts == tmp_path / "python" / "exhash.data" / "scripts"
    assert cfg.branch == "main"


def test_ship_rs_bump_updates_pyproject_and_cargo(tmp_path, monkeypatch):
    _make_rs_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    relmod.ship_rs_bump(part=1)

    pyproject = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in pyproject
    assert 'version = "0.2.0"' not in pyproject
    assert 'version = "0.2.0"' in (tmp_path / "Cargo.toml").read_text(encoding="utf-8")


def test_get_rs_config_rejects_pyproject_version(tmp_path):
    _make_rs_project(tmp_path, dynamic=False)

    try: relmod.get_rs_config(tmp_path)
    except ValueError as e: msg = str(e)
    else: raise AssertionError("Expected [project].version to be rejected")

    assert "must use Cargo.toml" in msg


def test_copy_rs_bins_builds_and_copies_configured_scripts(tmp_path, monkeypatch):
    _make_rs_project(tmp_path)
    cfg = relmod.get_rs_config(tmp_path)

    def fake_run(cmd):
        assert cmd == "cargo build --release --bins"
        d = tmp_path / "target" / "release"
        d.mkdir(parents=True)
        for name in cfg.bins: (d / name).write_text(name, encoding="utf-8")
        return ""

    monkeypatch.setattr(relmod, "run", fake_run)

    copied = relmod._copy_rs_bins(cfg, release=True)

    scripts = tmp_path / "python" / "exhash.data" / "scripts"
    assert copied == [scripts / "exhash", scripts / "lnhashview"]
    assert [p.read_text(encoding="utf-8") for p in copied] == ["exhash", "lnhashview"]


def test_ship_rs_init_configures_maturin_project(tmp_path, monkeypatch):
    _make_rs_project(tmp_path, configured=False, dynamic=False)
    monkeypatch.chdir(tmp_path)

    relmod.ship_rs_init(branch="main")
    relmod.ship_rs_init(branch="main")

    txt = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert txt.count("[tool.fastship]") == 1
    assert txt.count("[tool.fastship.rs]") == 1
    assert 'branch = "main"' in txt
    assert 'dynamic = ["version"]' in txt
    assert 'version = "0.1.2"' not in txt
    assert 'dev = ["fastship>=0.0.11", "maturin>=1.0,<2.0", "pytest"]' in txt
    assert 'bins = ["exhash", "lnhashview"]' in txt
    assert 'data_scripts = "python/exhash.data/scripts"' in txt


def test_ship_rs_init_rehomes_python_version_to_pyo3_module(tmp_path, monkeypatch):
    _make_rs_project(tmp_path, configured=False, bins=False, dynamic=False)
    pyproj = tmp_path / "pyproject.toml"
    pyproj.write_text(pyproj.read_text(encoding="utf-8") + 'python-source = "python"\nmodule-name = "fastpylight._core"\n', encoding="utf-8")
    pkg = tmp_path / "python" / "fastpylight"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("__version__ = '0.1.2'\nfrom fastpylight._core import tokenize, highlight\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    lib = src / "lib.rs"
    rust_src = """#[pymodule]
fn _core(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(py_tokenize, module)?)?;
    Ok(())
}
"""
    lib.write_text(rust_src, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    relmod.ship_rs_init(branch="main")
    relmod.ship_rs_init(branch="main")

    init = (pkg / "__init__.py").read_text(encoding="utf-8")
    rust = lib.read_text(encoding="utf-8")
    assert "__version__ =" not in init
    assert "from fastpylight._core import __version__, tokenize, highlight" in init
    assert rust.count('module.add("__version__", env!("CARGO_PKG_VERSION"))?;') == 1


def test_ship_rs_init_warns_when_runtime_version_rewrite_fails(tmp_path, monkeypatch, capsys):
    _make_rs_project(tmp_path, configured=False, bins=False, dynamic=False)
    pyproj = tmp_path / "pyproject.toml"
    pyproj.write_text(pyproj.read_text(encoding="utf-8") + 'python-source = "python"\nmodule-name = "pkg._core"\n', encoding="utf-8")
    pkg = tmp_path / "python" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("__version__ = '0.1.2'\nfrom elsewhere import thing\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    relmod.ship_rs_init(branch="main")

    err = capsys.readouterr().err
    assert "could not find PyO3 module function `fn _core(...)`" in err
    assert "found literal __version__" in err


def test_ship_rs_new_scaffolds_build_test_release_project(tmp_path):
    root = relmod._create_rs_project("hello-rs", description="Test PyO3 project", path=str(tmp_path))

    assert root == tmp_path / "hello-rs"
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    cargo = (root / "Cargo.toml").read_text(encoding="utf-8")
    rust = (root / "src" / "lib.rs").read_text(encoding="utf-8")
    workflow = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    init = (root / "python" / "hello_rs" / "__init__.py").read_text(encoding="utf-8")

    assert 'name = "hello-rs"' in pyproject
    assert 'dynamic = ["version"]' in pyproject
    assert 'module-name = "hello_rs._core"' in pyproject
    assert 'features = ["extension-module"]' in pyproject
    assert '[tool.fastship]' in pyproject
    assert "[tool.fastship.rs]" not in pyproject
    assert 'name = "hello-rs"' in cargo
    assert 'version = "0.1.0"' in cargo
    assert 'name = "hello_rs"' in cargo
    assert 'pyo3 = { version = ">=0.28", optional = true }' in cargo
    assert 'extension-module = ["pyo3", "pyo3/extension-module"]' in cargo
    assert 'pyo3 = ["dep:pyo3"]' in cargo
    assert 'm.add("__version__", env!("CARGO_PKG_VERSION"))?;' in rust
    assert '#[cfg(feature = "pyo3")]' in rust
    assert "from ._core import __version__, hello" in init
    assert "ship-rs-test" in workflow
    assert "PyO3/maturin-action@v1" in workflow
    assert "pypa/gh-action-pypi-publish@release/v1" in workflow

    cfg = relmod.get_rs_config(root)
    assert cfg.version == "0.1.0"
    assert cfg.bins == []
    assert cfg.branch == "main"


def test_ship_rs_new_bump_only_updates_cargo(tmp_path, monkeypatch):
    root = relmod._create_rs_project("bump-me", path=str(tmp_path))
    monkeypatch.chdir(root)

    relmod.ship_rs_bump(part=2)

    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in pyproject
    assert 'version = "0.1.1"' not in pyproject
    assert 'version = "0.1.1"' in (root / "Cargo.toml").read_text(encoding="utf-8")


def test_ship_rs_init_can_update_ci(tmp_path, monkeypatch):
    _make_rs_project(tmp_path, configured=False, bins=False)
    wf = tmp_path / ".github" / "workflows" / "ci.yml"
    wf.parent.mkdir(parents=True)
    ci = """steps:
      - run: tools/build.sh release
"""
    wf.write_text(ci, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    relmod.ship_rs_init(ci=True)
    relmod.ship_rs_init(ci=True)

    txt = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert 'bins = []' in txt
    ci_txt = wf.read_text(encoding="utf-8")
    assert ci_txt.count("pip install -e '.[dev]'") == 1
    assert ci_txt.count("ship-rs-prep --release") == 1


def test_ship_rs_init_rejects_non_maturin_project(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = \"x\"\nversion = \"0.1.0\"\n", encoding="utf-8")
    (tmp_path / "Cargo.toml").write_text("[package]\nname = \"x\"\nversion = \"0.1.0\"\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    try: relmod.ship_rs_init(branch=None)
    except SystemExit as e: msg = str(e)
    else: raise AssertionError("Expected non-maturin project to be rejected")

    assert "does not look like a maturin project" in msg
