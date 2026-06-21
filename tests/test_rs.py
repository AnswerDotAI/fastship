from pathlib import Path

import fastship.release as relmod


def _make_rs_project(root: Path, configured: bool = True, dynamic: bool = True):
    cfg = """
[tool.fastship]
branch = "main"
""" if configured else ""
    pyproj = """[build-system]
requires = ["maturin>=1.0,<2.0"]
build-backend = "maturin"

[project]
name = "exhash"
""" + ('dynamic = ["version"]\n' if dynamic else 'version = "0.1.2"\n') + """
[tool.maturin]
python-source = "python"
""" + cfg
    cargo = """[package]
name = "exhash"
version = "0.1.2"
edition = "2021"
"""
    (root / "pyproject.toml").write_text(pyproj, encoding="utf-8")
    (root / "Cargo.toml").write_text(cargo, encoding="utf-8")


def test_get_rs_config(tmp_path):
    _make_rs_project(tmp_path)
    cfg = relmod.get_rs_config(tmp_path)

    assert cfg.version == "0.1.2"
    assert cfg.branch == "main"
    assert cfg.manifest_path == tmp_path / "Cargo.toml"


def test_ship_rs_bump_updates_pyproject_and_cargo(tmp_path, monkeypatch):
    _make_rs_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(relmod, "run", lambda cmd, *a, **k: calls.append(cmd))

    relmod.ship_rs_bump(part=1)

    pyproject = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in pyproject
    assert 'version = "0.2.0"' not in pyproject
    assert 'version = "0.2.0"' in (tmp_path / "Cargo.toml").read_text(encoding="utf-8")
    assert any("maturin develop" in c for c in calls)  # bump refreshes the local install
