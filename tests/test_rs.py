from pathlib import Path

import fastship.release as relmod


def _make_rs_project(root: Path):
    (root / "python" / "exhash.data" / "scripts").mkdir(parents=True)
    pyproj = """[build-system]
requires = ["maturin>=1.0,<2.0"]
build-backend = "maturin"

[project]
name = "exhash"
version = "0.1.2"

[tool.maturin]
data = "python/exhash.data"

[tool.fastship]
branch = "main"

[tool.fastship.rs]
bins = ["exhash", "lnhashview"]
"""
    cargo = """[package]
name = "exhash"
version = "0.1.2"
edition = "2021"
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

    assert 'version = "0.2.0"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.2.0"' in (tmp_path / "Cargo.toml").read_text(encoding="utf-8")


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
